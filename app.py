import streamlit as st
import json
import requests
import trafilatura
from openai import OpenAI
from duckduckgo_search import DDGS
import datetime

# ================= [页面全局配置] =================
st.set_page_config(
    page_title="AI 深度研究员",
    page_icon="🕵️",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.title("🕵️ AI 深度联网搜索助手 (Deep Research)")
st.markdown("---")

# ================= [侧边栏配置区] =================
with st.sidebar:
    st.header("⚙️ 参数配置")

    # 搜索源选择
    search_source_option = st.selectbox(
        "选择搜索源",
        options=[1, 2, 3],
        format_func=lambda x: {
            1: "1. Bocha (博查 - 推荐)",
            2: "2. Google Custom Search",
            3: "3. DuckDuckGo (无需Key+代理)"
        }[x],
        index=2  # 默认 DDG
    )

    # API Keys 配置
    with st.expander("🔑 API Key 设置", expanded=True):
        silicon_key = st.text_input("SiliconFlow API Key", value="",
                                    type="password")
        bocha_key = st.text_input("Bocha API Key", value="", type="password")
        google_key = st.text_input("Google API Key", value="", type="password")
        google_cx = st.text_input("Google CX ID", value="")

    # 网络与模型配置
    with st.expander("🌐 网络与模型", expanded=False):
        # 默认代理留空，根据自己情况填，如 http://127.0.0.1:7890
        proxy_url = st.text_input("HTTP Proxy (如需要)", value="http://127.0.0.1:7890")
        model_name = st.text_input("模型名称", value="Qwen/Qwen3-235B-A22B-Instruct-2507")
        base_url = st.text_input("Base URL", value="https://api.siliconflow.cn/v1")

        max_steps = st.slider("最大思考步数", 3, 15, 8)

# ================= [核心工具函数] =================

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}
BLACKLIST = ["baidu.com", "zhihu.com", "tieba.baidu.com", "csdn.net"]

# 忽略 SSL 警告
requests.packages.urllib3.disable_warnings()


def get_page_content(url, proxy):
    """通用网页抓取工具"""
    try:
        proxies = {"http": proxy, "https": proxy} if proxy else None

        # 1. 尝试 trafilatura 直接下载 (速度快)
        if not proxy:
            downloaded = trafilatura.fetch_url(url)
            if downloaded:
                text = trafilatura.extract(downloaded, include_comments=False, target_language='zh')
                if text: return text[:5000].replace("\n", " ")

        # 2. Requests 回退机制 (支持代理)
        verify_ssl = not bool(proxy)
        resp = requests.get(url, headers=HEADERS, proxies=proxies, timeout=10, verify=verify_ssl)

        if resp.status_code == 200:
            resp.encoding = resp.apparent_encoding
            text = trafilatura.extract(resp.text, include_comments=False, target_language='zh')
            if text:
                return text[:5000].replace("\n", " ")
            return ""
        return ""
    except Exception:
        return ""


# --- 搜索实现 ---
def search_bocha(query, api_key):
    if not api_key: return "❌ 错误：未填写 Bocha API Key"
    url = "https://api.bochaai.com/v1/web-search"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"query": query, "count": 3, "summary": True, "freshness": "noLimit"}

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            if "data" in data and "webPages" in data["data"]:
                items = data["data"]["webPages"]["value"]
                report = f"针对查询 '{query}' 的 Bocha 结果：\n"
                for i, item in enumerate(items):
                    link = item.get('url', '')
                    summary = item.get('summary', '') or item.get('snippet', '')
                    # 爬取正文
                    full_text = get_page_content(link, None)
                    content = full_text if len(full_text) > 200 else f"【摘要】{summary}"
                    report += f"--- 来源 {i + 1}: {item.get('name')} ---\n链接: {link}\n内容: {content}\n\n"
                return report
        return "Bocha 未返回有效结果。"
    except Exception as e:
        return f"Bocha 接口异常: {e}"


def search_google(query, api_key, cx_id):
    if not api_key or not cx_id: return "❌ 错误：未填写 Google API Key 或 CX ID"
    url = "https://www.googleapis.com/customsearch/v1"
    params = {'q': query, 'key': api_key, 'cx': cx_id, 'num': 3}

    try:
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code == 200:
            items = resp.json().get('items', [])
            if not items: return "Google 未找到结果。"

            report = f"针对查询 '{query}' 的 Google 结果：\n"
            for i, item in enumerate(items):
                link = item.get('link', '')
                snippet = item.get('snippet', '')
                full_text = get_page_content(link, None)
                content = full_text if len(full_text) > 200 else f"【摘要】{snippet}"
                report += f"--- 来源 {i + 1}: {item.get('title')} ---\n链接: {link}\n内容: {content}\n\n"
            return report
        return f"Google 接口报错: {resp.status_code}"
    except Exception as e:
        return f"Google 请求异常: {e}"


def search_ddg(query, proxy):
    try:
        results = []
        with DDGS(proxy=proxy, timeout=30) as ddgs:
            results = list(ddgs.text(keywords=query, region='wt-wt', max_results=10, backend="html"))

        if not results: return "DuckDuckGo 未找到结果。"

        report = f"针对查询 '{query}' 的 DDG 结果：\n"
        valid_count = 0

        for item in results:
            if valid_count >= 3: break
            link = item.get('href', '')
            title = item.get('title', '')
            snippet = item.get('body', '')

            # 简单的黑名单过滤
            if any(domain in link for domain in BLACKLIST): continue

            valid_count += 1
            full_text = get_page_content(link, proxy)
            content = full_text if len(full_text) > 500 else f"【摘要】{snippet}"
            report += f"--- 来源 {valid_count}: {title} ---\n链接: {link}\n内容: {content}\n\n"

        return report if valid_count > 0 else "结果均在黑名单中。"
    except Exception as e:
        return f"DuckDuckGo 连接失败: {e}"


def unified_search(query, source, bocha_key, google_key, google_cx, proxy):
    """统一搜索调度入口"""
    if source == 1:
        return search_bocha(query, bocha_key)
    elif source == 2:
        return search_google(query, google_key, google_cx)
    elif source == 3:
        return search_ddg(query, proxy)
    return "无效的搜索源"


# ================= [核心：Agent 逻辑 (生成器)] =================

def run_agent_generator(question, api_key, base_url, model, source, bocha_k, google_k, google_c, proxy, max_steps):
    """
    Agent 主逻辑：通过 yield 返回流式状态更新
    """
    client = OpenAI(api_key=api_key, base_url=base_url)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    source_name = {1: "Bocha", 2: "Google", 3: "DuckDuckGo"}.get(source, "Unknown")

    # 🔥 深度思考的 System Prompt
    system_prompt = f"""
    你是一个具备深度联网搜索能力的智能研究员，当前搜索引擎：{source_name}。
    当前时间：{now}

    【思维模式】：
    你必须展现出显式的“思维链 (Chain of Thought)”。在执行任何操作前，先进行深度的逻辑分析。

    【思考结构】：
    你的 `thought` 字段必须包含以下段落（用换行分隔）：
    1. **[分析]**：当前已知什么？还需要查什么？
    2. **[评估]**：之前的搜索结果可信吗？是否有矛盾？
    3. **[决策]**：下一步具体做什么？为什么？

    【输出格式 (严格 JSON)】：
    {{
        "thought": "你的结构化思考过程...",
        "action": "search" 或 "finish",
        "query": "搜索关键词 (仅当 action=search 时，关键词要具体)",
        "answer": "最终答案 (仅当 action=finish 时，需详尽、结构化并引用来源)"
    }}
    """

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"请解决这个问题：{question}"}
    ]

    step = 0
    while step < max_steps:
        step += 1
        yield {"type": "status_update", "content": f"⚡ 正在进行第 {step} 步深度推理..."}

        try:
            # 调用大模型
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.3,  # 较低温度保持逻辑严密
                response_format={"type": "json_object"},
                max_tokens=2000  # 允许长思考
            )
            content = response.choices[0].message.content
            # 清洗可能存在的 markdown 标记
            content_clean = content.replace("```json", "").replace("```", "").strip()
            decision = json.loads(content_clean)
        except Exception as e:
            yield {"type": "error", "content": f"❌ 模型调用或JSON解析失败: {e}"}
            return

        thought = decision.get("thought", "（未返回思考过程）")
        action = decision.get("action", "")

        # 1. 推送思考过程
        yield {"type": "thought", "content": thought}

        if action == "search":
            query = decision.get("query")
            if not query:
                yield {"type": "error", "content": "⚠️ 生成了空的搜索词，尝试跳过..."}
                continue

            # 2. 推送动作
            yield {"type": "action", "content": f"🔎 **执行搜索**: `{query}`"}

            # 3. 执行搜索工具
            tool_output = unified_search(query, source, bocha_k, google_k, google_c, proxy)

            # 4. 推送工具结果摘要
            yield {"type": "tool_output", "content": tool_output}

            # 更新对话历史
            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user", "content": f"【搜索工具返回数据】:\n{tool_output}"})

        elif action == "finish":
            final_answer = decision.get("answer")
            yield {"type": "final_answer", "content": final_answer}
            return

        else:
            yield {"type": "error", "content": f"⚠️ 未知动作: {action}"}
            break

    yield {"type": "final_answer", "content": "🛑 已达到最大步数，停止搜索。以下是基于现有信息的总结。"}


# ================= [UI 交互逻辑] =================

# 初始化 Session State
if "messages" not in st.session_state:
    st.session_state.messages = []

# 1. 渲染历史消息
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        # 如果有详细过程日志，使用折叠面板显示
        if "details" in msg and msg["details"]:
            with st.expander("🕵️ 查看深度思考与搜索过程"):
                st.markdown(msg["details"])

# 2. 处理用户输入
if prompt := st.chat_input("请输入您的问题，开始深度搜索..."):
    # 显示用户提问
    st.chat_message("user").markdown(prompt)
    st.session_state.messages.append({"role": "user", "content": prompt})

    # 助手响应区
    with st.chat_message("assistant"):
        # 状态容器：用于显示实时的思考动画
        status_container = st.status("🧠 大脑启动中...", expanded=True)
        final_answer_container = st.empty()

        # 用于记录完整的思考日志，以便存入历史
        process_log_markdown = ""

        # 启动生成器
        gen = run_agent_generator(
            prompt, silicon_key, base_url, model_name,
            search_source_option, bocha_key, google_key, google_cx, proxy_url, max_steps
        )

        final_response = ""

        try:
            for event in gen:
                # --- 状态栏标题更新 ---
                if event["type"] == "status_update":
                    status_container.update(label=event["content"], state="running")

                # --- 思考过程展示 ---
                elif event["type"] == "thought":
                    # 格式化一下思考内容，加粗分段
                    formatted_thought = event['content'].replace('\n', '\n\n')
                    msg = f"#### 🤔 深度思考\n{formatted_thought}\n\n---\n"
                    status_container.markdown(msg)
                    process_log_markdown += msg

                # --- 动作展示 ---
                elif event["type"] == "action":
                    msg = f"{event['content']}\n\n"
                    status_container.markdown(msg)
                    process_log_markdown += msg

                # --- 工具结果展示 ---
                elif event["type"] == "tool_output":
                    # 截取前 150 字符做预览
                    preview = event['content'][:1500].replace('\n', ' ') + "..."
                    status_container.caption(f"📄 *已获取网页内容 (摘要)*: {preview}")
                    # 日志里记录较详细的内容（但不至于太长）
                    process_log_markdown += f"📄 **网页抓取结果**: \n```text\n{event['content'][:1000]}...\n```\n\n---\n"

                # --- 错误处理 ---
                elif event["type"] == "error":
                    status_container.error(event["content"])
                    process_log_markdown += f"❌ **Error**: {event['content']}\n"

                # --- 最终答案 ---
                elif event["type"] == "final_answer":
                    final_response = event["content"]
                    status_container.update(label="✅ 任务完成", state="complete", expanded=False)
                    final_answer_container.markdown(final_response)

        except Exception as e:
            st.error(f"程序运行异常: {e}")

        # 将最终结果保存到历史
        if final_response:
            st.session_state.messages.append({
                "role": "assistant",
                "content": final_response,
                "details": process_log_markdown
            })