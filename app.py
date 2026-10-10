# -*- coding: utf-8 -*-
"""
WelcomeScreen - Streamlit 欢迎页 + DeepSeek 对话 Demo（流式输出版）

部署到 Streamlit Cloud 后，请到后台 Secrets 配置 DEEPSEEK_API_KEY：
  DEEPSEEK_API_KEY = "sk-你的真实密钥"

本地运行：streamlit run app.py
"""

import os
from openai import OpenAI
from dotenv import load_dotenv
import streamlit as st

# 加载本地 .env（仅本机使用，不会被 Git 上传）
load_dotenv()


# ---------- 密钥读取 ----------
def get_api_key() -> str:
    """优先从 st.secrets 读取（Streamlit Cloud），否则从环境变量/.env 读取"""
    try:
        return st.secrets["DEEPSEEK_API_KEY"]
    except (KeyError, FileNotFoundError):
        return os.environ.get("DEEPSEEK_API_KEY") or ""


def get_client() -> OpenAI:
    """根据密钥创建 OpenAI 客户端（指向 DeepSeek）"""
    api_key = get_api_key()
    if not api_key:
        st.error(
            "❌ 未找到 DEEPSEEK_API_KEY\n\n"
            "**本地运行**：在项目根目录的 `.env` 文件中配置：\n\n"
            "```\nDEEPSEEK_API_KEY=sk-你的密钥\n```\n\n"
            "**Streamlit Cloud**：到应用的 `Settings → Secrets` 中添加：\n\n"
            "```toml\nDEEPSEEK_API_KEY = \"sk-你的密钥\"\n```"
        )
        st.stop()
    return OpenAI(api_key=api_key, base_url="https://api.deepseek.com")


# ========== 页面配置 ==========
st.set_page_config(
    page_title="WelcomeScreen · DeepSeek Demo",
    page_icon="🤖",
    layout="centered",
)

# ========== 欢迎页 ==========
st.title("🎉 WelcomeScreen")
st.subheader("一个由 DeepSeek 大模型驱动的 AI 对话 Demo（流式输出 ✨）")

st.markdown(
    """
    👋 欢迎来到 **WelcomeScreen**！

    - 🤖 使用 DeepSeek 大模型，支持**流式输出**（像打字机一样）
    - 🔐 密钥通过 `st.secrets` / 环境变量管理，**安全无泄露**
    - 💬 在下方输入问题开始聊天吧
    """
)

st.divider()

# ========== 侧边栏设置 ==========
with st.sidebar:
    st.header("⚙️ 设置")
    model = st.selectbox(
        "选择模型",
        ["deepseek-chat", "deepseek-reasoner"],
        index=0,
        help="deepseek-chat：通用对话模型；deepseek-reasoner：深度思考模型",
    )
    temperature = st.slider("Temperature", min_value=0.0, max_value=1.5, value=0.7, step=0.1)
    system_prompt = st.text_area(
        "系统提示词",
        value="你是一名非常可爱的 AI 助手，请用温柔可爱的语气回答用户的问题。",
        height=100,
    )
    st.divider()
    if st.button("🗑️ 清空对话"):
        st.session_state.messages = []
        st.rerun()

# ========== 对话区 ==========
st.header("💬 和 AI 聊聊天")

# 用 session_state 保存历史对话
if "messages" not in st.session_state:
    st.session_state.messages = []

# 显示历史
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# 用户输入
user_input = st.chat_input("请输入你的问题，回车发送...")
if user_input and user_input.strip():
    # 显示用户消息
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    # 调用 AI（流式输出）
    client = get_client()
    with st.chat_message("assistant"):
        placeholder = st.empty()
        full_reply = ""
        try:
            stream = client.chat.completions.create(
                model=model,
                temperature=temperature,
                messages=[
                    {"role": "system", "content": system_prompt},
                    *[
                        {"role": m["role"], "content": m["content"]}
                        for m in st.session_state.messages
                    ],
                ],
                stream=True,
            )
            for chunk in stream:
                delta = chunk.choices[0].delta.content or ""
                full_reply += delta
                placeholder.markdown(full_reply + "▌")  # ▌ 模拟光标
            placeholder.markdown(full_reply)
        except Exception as e:
            st.error(f"出错了：{e}")
            full_reply = f"（出错）{e}"

    st.session_state.messages.append({"role": "assistant", "content": full_reply})