import json
import time
import requests
import trafilatura
from openai import OpenAI

# ================= 配置区 =================
# 1. LLM (SiliconFlow / DeepSeek / Qwen)
LLM_API_KEY = ""
BASE_URL = ""
MODEL_NAME = ""
# 2. Google Search API
GOOGLE_API_KEY = ""
GOOGLE_CX_ID = ""



# 3. 爬虫伪装头
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

client = OpenAI(api_key=LLM_API_KEY, base_url=BASE_URL)


# ================= 第一部分：增强型搜索工具 =================

def google_search(query, num=3):
    """执行 Google 搜索并返回结构化数据"""
    print(f"   [正在搜索]: {query}")
    url = "https://www.googleapis.com/customsearch/v1"
    params = {'q': query, 'key': GOOGLE_API_KEY, 'cx': GOOGLE_CX_ID, 'num': num}
    try:
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code == 200:
            return resp.json().get('items', [])
        return []
    except Exception as e:
        print(f"   搜索报错: {e}")
        return []


def get_page_content(url):
    """抓取网页正文，带回退机制"""
    try:
        # 1. 尝试 trafilatura 直接抓取
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            # 2. 回退到 requests
            resp = requests.get(url, headers=HEADERS, timeout=8)
            resp.encoding = resp.apparent_encoding
            downloaded = resp.text

        # 提取正文
        text = trafilatura.extract(downloaded, include_comments=False, target_language='zh')
        if not text:
            return ""
        # 截取前 1500 字符，避免 Context 溢出，同时足够覆盖摘要
        return text[:2500].replace("\n", " ")
    except:
        return ""


def search_tool(query):
    """
    Agent 调用的统一接口：搜索 + 自动阅读前 2 个结果
    """
    items = google_search(query, num=3)
    if not items:
        return "【系统提示】：未找到任何搜索结果，请尝试更换关键词。"

    report = f"针对查询 '{query}' 的搜索结果：\n"

    for i, item in enumerate(items):
        title = item.get('title', 'No Title')
        link = item.get('link', '')
        snippet = item.get('snippet', '')

        # 尝试抓取全文细节
        full_text = get_page_content(link)
        # 如果抓不到全文，就用 Google 提供的 snippet
        content = full_text if len(full_text) > 1000 else snippet

        report += f"--- 来源 {i + 1}: {title} ---\n"
        report += f"链接: {link}\n"
        report += f"内容: {content}\n\n"

    return report


# ================= 第二部分：ReAct 逻辑核心 =================

def run_agent(question, max_steps=50):
    print("=" * 60)
    print(f"Agent 启动 | 目标问题: {question}")
    print("=" * 60)

    # 核心 Prompt：教会模型如何思考和输出 JSON
    system_prompt = """
    你是一个模仿 Google AI Studio 高级搜索逻辑的智能助手。
    你的任务是通过分步骤的互联网搜索来解决复杂的多跳推理问题。

    【重要规则】：
    1. 你必须严格按照逻辑顺序一步步来，不能跳跃。
    2. **利用上一步的结果**：如果第一步搜到了某个实体的名称，第二步搜索必须使用这个名称。
    3. 每次输出必须是严格的 JSON 格式。

    【JSON 输出格式】：
    {
        "thought": "分析当前已知信息，说明为什么需要进行下一步搜索，或者为什么可以回答了，如果超过6次搜索不到就根据你的所知道的去回答",
        "action": "search" 或 "finish",
        "query": "如果是 search，这里模拟人类填写搜索引擎关键词（要精确，语义也不一定要连贯，可以多关键词），也可以根据上面调查到的网站信息输入你想要继续查询的链接",
        "answer": "如果是 finish，这里填写最终答案（需引用搜索到的数据）"
    }
    """

    # 初始对话历史
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"请解决这个问题：{question}"}
    ]

    step = 0
    while step < max_steps:
        step += 1
        print(f"\n⚡ [Step {step}]: 思考中...")

        # 1. 调用 LLM 决策
        try:
            response = client.chat.completions.create(
                model=MODEL_NAME,
                messages=messages,
                temperature=0.3,
                response_format={"type": "json_object"},
                max_tokens=1000
            )
            content = response.choices[0].message.content

            # 容错处理：清洗 Markdown 标记
            content_clean = content.replace("```json", "").replace("```", "").strip()
            decision = json.loads(content_clean)

        except Exception as e:
            print(f"   模型输出解析失败: {e}\n  原文: {content}")
            break

        # 2. 解析决策
        thought = decision.get("thought", "")
        action = decision.get("action", "")

        print(f"   [思维链]: {thought}")

        # 3. 分支执行
        if action == "search":
            query = decision.get("query")

            # 执行搜索
            tool_output = search_tool(query)

            # 将“行动”和“结果”都写入历史，形成闭环
            # 模型看到自己刚才搜了什么，得到了什么
            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user", "content": f"【搜索工具返回结果】:\n{tool_output}"})

        elif action == "finish":
            final_answer = decision.get("answer")
            print("\n" + "=" * 30 + " 🏁 最终结论 " + "=" * 30)
            print(final_answer)
            return final_answer

        else:
            print(f"  未知动作: {action}")
            break

    print("\n达到最大步数，任务未完全终结。")




if __name__ == "__main__":

    complex_question = (
        "Of the authors (First M. Last) that worked on the paper \"Pie Menus or Linear Menus, Which Is Better?\" in 2015, what was the title of the first paper authored by the one that had authored prior papers?"

    )


    run_agent(complex_question)