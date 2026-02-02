import json
import time
import requests
import trafilatura
from openai import OpenAI

# ================= 配置区 =================

# 1. LLM 配置 (保持不变)
LLM_API_KEY = ""
BASE_URL = ""
MODEL_NAME = ""

# 2. Bocha (博查) 配置
# ！！！请在此处填入您的博查 API Key ！！！
BOCHA_API_KEY = ""

# 3. 爬虫伪装头
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

client = OpenAI(api_key=LLM_API_KEY, base_url=BASE_URL)


# ================= 第一部分：博查搜索与内容获取工具 =================

def bocha_search(query, count=3):
    """
    调用博查 Web Search API
    文档参考: https://bocha-ai.feishu.cn/wiki/RXEOw02rFiwzGSkd9mUcqoeAnNK
    """
    print(f"   [正在搜索(Bocha)]: {query}")

    url = "https://api.bochaai.com/v1/web-search"

    headers = {
        "Authorization": f"Bearer {BOCHA_API_KEY}",
        "Content-Type": "application/json"
    }

    # 构造请求体
    payload = {
        "query": query,
        "count": count,
        "summary": True,  # 请求长摘要，确保信息量
        "freshness": "noLimit"  # 不限制时间，如果是新闻类可改为 oneDay/oneWeek
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            # 兼容博查返回结构: data -> webPages -> value
            if "data" in data and "webPages" in data["data"]:
                return data["data"]["webPages"]["value"]
            else:
                print(f"   博查返回数据为空或格式异常: {data}")
                return []
        else:
            print(f"   博查接口报错: {resp.status_code} - {resp.text}")
            return []
    except Exception as e:
        print(f"   搜索请求异常: {e}")
        return []


def get_page_content(url):
    """
    尝试抓取网页全文。
    如果 trafilatura 抓取失败，返回空字符串，交由上层逻辑使用博查摘要兜底。
    """
    try:
        # 1. 尝试 trafilatura 直接下载
        downloaded = trafilatura.fetch_url(url)

        # 2. 如果 trafilatura 下载失败，尝试 requests 补救
        if not downloaded:
            resp = requests.get(url, headers=HEADERS, timeout=10)
            if resp.status_code == 200:
                resp.encoding = resp.apparent_encoding
                downloaded = resp.text
            else:
                return ""

        # 3. 提取正文
        if downloaded:
            text = trafilatura.extract(downloaded, include_comments=False, target_language='zh')
            if text:
                # 截取前 3000 字符，防止 Token 溢出，同时保证主要内容被读取
                return text[:3000].replace("\n", " ")

        return ""
    except Exception:
        return ""


def search_tool(query):
    """
    Agent 调用的核心工具：
    1. 使用博查搜索
    2. 遍历结果，尝试爬取全文
    3. 如果爬不到全文，使用博查提供的 Summary
    """
    # 搜索前3条，保证速度和相关性
    items = bocha_search(query, count=3)

    if not items:
        return "【系统提示】：未找到任何搜索结果，请尝试更换关键词。"

    report = f"针对查询 '{query}' 的搜索结果：\n"

    for i, item in enumerate(items):
        # 博查的字段通常是 name(标题), url(链接), summary(摘要), snippet(片段)
        title = item.get('name', '无标题')
        link = item.get('url', '')
        # 优先取 summary (长摘要)，没有则取 snippet
        bocha_summary = item.get('summary', '') or item.get('snippet', '')

        # --- 核心逻辑：获取完整内容 ---
        # 尝试访问链接获取全文
        full_text = get_page_content(link)

        # 决策：如果抓到了全文且长度足够，用全文；否则用博查的摘要
        if len(full_text) > 100:
            content = f"【网页全文提取】: {full_text}"
        else:
            content = f"【博查摘要(网页不可爬)】: {bocha_summary}"

        report += f"--- 来源 {i + 1}: {title} ---\n"
        report += f"链接: {link}\n"
        report += f"内容: {content}\n\n"

    return report


# ================= 第二部分：ReAct Agent 逻辑 (保持逻辑严密性) =================

def run_agent(question, max_steps=10):
    print("=" * 60)
    print(f"Agent 启动 | 目标问题: {question}")
    print("=" * 60)

    # Prompt 保持不变，强调逻辑推理
    system_prompt = """
    你是一个具备深度联网搜索能力的智能助手。
    当今是202
    你的任务是通过分步骤的搜索来解决复杂问题。

    【工作流】：
    1. 分析用户问题，决定搜索什么关键词。
    2. 观察搜索结果（我会提供网页全文或长摘要）。
    3. 根据结果决定是继续搜索新信息，还是进行总结回答。

    【输出格式(严格JSON)】：
    {
        "thought": "思考过程：分析当前获取到了什么，还需要什么",
        "action": "search" 或 "finish",
        "query": "搜索关键词(仅当action为search时)",
        "answer": "最终答案(仅当action为finish时，需详尽并引用数据)"
    }
    """

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"请解决这个问题：{question}"}
    ]

    step = 0
    while step < max_steps:
        step += 1
        print(f"\n⚡ [Step {step}]: 思考中...")

        # 1. LLM 决策
        try:
            response = client.chat.completions.create(
                model=MODEL_NAME,
                messages=messages,
                temperature=0.3,
                response_format={"type": "json_object"},
                max_tokens=1000
            )
            content = response.choices[0].message.content

            # 清洗可能存在的 Markdown 符号
            content_clean = content.replace("```json", "").replace("```", "").strip()
            decision = json.loads(content_clean)

        except Exception as e:
            print(f"   JSON解析失败，重试... {e}")
            continue

        thought = decision.get("thought", "")
        action = decision.get("action", "")
        print(f"   [思维链]: {thought}")

        # 2. 执行动作
        if action == "search":
            query = decision.get("query")
            if not query:
                print("   [警告] 模型未生成查询词")
                continue

            # 调用博查搜索工具
            tool_output = search_tool(query)

            # 写入历史
            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user", "content": f"【搜索工具返回数据】:\n{tool_output}"})

        elif action == "finish":
            final_answer = decision.get("answer")
            print("\n" + "=" * 30 + " 🏁 最终结论 " + "=" * 30)
            print(final_answer)
            return final_answer

        else:
            print(f"   未知动作: {action}")
            break

    print("\n任务达到最大步数停止。")


if __name__ == "__main__":
    # 示例：汇率查询及分析
    # 博查能很好地检索到实时数据和新闻分析
    complex_question = "评价一下星际争霸2中三个种族强度"

    if "YOUR_BOCHA_API_KEY" in BOCHA_API_KEY:
        print("❌ 错误：请先在代码第 16 行填入你的博查 API Key")
    else:
        run_agent(complex_question)