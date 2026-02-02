import json
import time
import requests
import trafilatura
from openai import OpenAI
from duckduckgo_search import DDGS

# ================= 配置区 =================

# 1. 代理设置 (爬取海外内容必须开启)
PROXY_URL = "http://127.0.0.1:7890"

# 2. LLM 设置 (SiliconFlow)
LLM_API_KEY = ""
BASE_URL = ""
MODEL_NAME = ""

# 3. 黑名单域名列表 (遇到这些域名直接跳过，不给 LLM 看)
BLACKLIST = [
    "baidu.com",
    "zhihu.com",
    "tieba.baidu.com",
    "zhidao.baidu.com",
    "bilibili.com",
    "csdn.net"
]

# 4. 伪装浏览器头
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.9,zh-CN;q=0.8',  # 降低中文权重
}

client = OpenAI(api_key=LLM_API_KEY, base_url=BASE_URL)


# ================= 第一部分：过滤型深度搜索工具 =================

def get_full_page_text(url):
    """
    爬取海外/非黑名单网站：强制使用 requests + 代理
    """
    if not url: return ""
    print(f"     -> 正在深入阅读: {url[:50]}...")

    # 构造代理
    proxies = {"http": PROXY_URL, "https": PROXY_URL}

    try:
        # 强制代理，超时 15秒
        # verify=False 避免某些国外网站 SSL 证书报错
        resp = requests.get(url, headers=HEADERS, proxies=proxies, timeout=15, verify=False)

        if resp.status_code == 200:
            resp.encoding = resp.apparent_encoding
            # 提取正文
            text = trafilatura.extract(resp.text, include_comments=False, include_tables=False)
            if text:
                # 限制长度，防止 Token 溢出
                return text[:3500].replace("\n", " ")
            return "⚠️ 无法提取文本 (可能是纯图片/视频站)"
        else:
            return f"❌ HTTP 状态码 {resp.status_code}"

    except Exception as e:
        return f"❌ 读取错误: {str(e)[:50]}"


def search_tool(query):
    """
    Agent 调用的核心接口：
    1. Global 搜索 (wt-wt)
    2. 过滤黑名单
    3. 爬取前 2-3 个有效结果
    """
    print(f"   [DuckDuckGo 国际搜索]: {query}")
    print(f"   [黑名单过滤]: 已启用")

    results = []
    try:
        # 初始化 DDGS，必须挂代理
        with DDGS(proxy=PROXY_URL, timeout=30) as ddgs:
            # max_results 设大一点(15)，因为过滤掉黑名单后剩余的会变少
            results_gen = ddgs.text(
                keywords=query,
                region='wt-wt',  # 全球模式
                max_results=30,
                backend="html"
            )
            results = list(results_gen)
    except Exception as e:
        return f"【系统提示】：搜索工具连接失败，错误信息: {e}"

    if not results:
        return "【系统提示】：DuckDuckGo 未找到任何结果，请尝试更换关键词。"

    # === 构建返回给 LLM 的报告 ===
    report = f"针对查询 '{query}' 的搜索结果（已过滤国内内容）：\n"

    valid_count = 0
    target_valid_count = 10 # 我们只需要给 LLM 看前 2 个有效的详细结果

    for item in results:
        # 达到目标数量停止，节省时间
        if valid_count >= target_valid_count:
            break

        title = item.get('title', 'No Title')
        link = item.get('href', '')
        snippet = item.get('body', '')

        # --- 1. 黑名单检查 ---
        is_blacklisted = False
        for domain in BLACKLIST:
            if domain in link:
                print(f"     🛑 [跳过黑名单] {domain} -> {title}")
                is_blacklisted = True
                break

        if is_blacklisted:
            continue  # 跳过当前循环

        # --- 2. 爬取有效链接 ---
        valid_count += 1
        full_text = get_full_page_text(link)

        # 如果爬取失败，使用摘要回退
        content_to_use = full_text if len(full_text) > 200 else f"【爬取失败，仅提供摘要】{snippet}"

        report += f"--- 来源 {valid_count}: {title} ---\n"
        report += f"链接: {link}\n"
        report += f"内容: {content_to_use}\n\n"

    if valid_count == 0:
        return "【系统提示】：搜索到了结果，但全部都在黑名单中（如百度/知乎），建议更换英文关键词再试。"

    return report


# ================= 第二部分：ReAct 逻辑核心 =================

def run_agent(question, max_steps=50):
    print("=" * 60)
    print(f"Agent 启动 | 目标问题: {question}")
    print(f"模式: 全球搜索 (wt-wt) | 过滤: 百度/知乎等")
    print("=" * 60)

    # 核心 Prompt
    system_prompt = """
    你是一个模仿 Google AI Studio 高级搜索逻辑的智能助手。
    你的任务是通过分步骤的互联网搜索来解决复杂的多跳推理问题。

    【重要规则】：
    1. 你必须严格按照逻辑顺序一步步来，不能跳跃。
    2. **利用上一步的结果**：如果第一步搜到了某个实体的名称，第二步搜索必须使用这个名称。
    3. 每次输出必须是严格的 JSON 格式。
    4. **优先使用英文搜索**：因为搜索工具已配置为国际模式，使用英文关键词能获得更准确的一手资料。

    【JSON 输出格式】：
    {
        "thought": "分析当前已知信息，说明为什么需要进行下一步搜索，或者为什么可以回答了",
        "action": "search" 或 "finish",
        "query": "如果是 search，这里填写搜索关键词（建议用英文）",
        "answer": "如果是 finish，这里填写最终答案（需引用搜索到的数据）"
    }
    """

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"请解决这个问题：{question}"}
    ]

    step = 0
    while step < max_steps:
        step += 1
        print(f"\n⚡ [Step {step}]: Agent 思考中...")

        # 1. 调用 LLM 决策
        try:
            response = client.chat.completions.create(
                model=MODEL_NAME,
                messages=messages,
                temperature=0.3,
                response_format={"type": "json_object"},
                max_tokens=1000,
                timeout=60
            )
            content = response.choices[0].message.content

            # 清洗
            content_clean = content.replace("```json", "").replace("```", "").strip()
            decision = json.loads(content_clean)

        except Exception as e:
            print(f"   ❌ 模型输出解析失败或超时: {e}")
            break

        # 2. 解析决策
        thought = decision.get("thought", "")
        action = decision.get("action", "")

        print("\n" + "-" * 20 + " 🧠 思维链 " + "-" * 20)
        print(thought)
        print("-" * 60)

        # 3. 分支执行
        if action == "search":
            query = decision.get("query")

            # 调用新的过滤搜索工具
            tool_output = search_tool(query)

            # 将结果写入历史
            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user", "content": f"【搜索工具返回结果】:\n{tool_output}"})

        elif action == "finish":
            final_answer = decision.get("answer")
            print("\n" + "=" * 30 + " 🏁 最终结论 " + "=" * 30)
            print(final_answer)
            return final_answer

        else:
            print(f"  ❌ 未知动作: {action}")
            break

    print("\n达到最大步数，任务未完全终结。")


if __name__ == "__main__":
    # 关闭 urllib3 的 insecure request 警告 (因为用了 verify=False)
    requests.packages.urllib3.disable_warnings()

    # 示例问题
    complex_question = "搜索一下现在美金和人民币的汇率，并搜索相关新闻分析原因"

    run_agent(complex_question)