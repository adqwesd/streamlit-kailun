# -*- coding: utf-8 -*-
"""
WelcomeScreen - Streamlit 欢迎页 + DeepSeek 对话 Demo
运行命令：streamlit run app.py
"""

import os
from openai import OpenAI
from dotenv import load_dotenv
import streamlit as st

# 加载本地 .env（仅本机使用，不会被 Git 上传）
load_dotenv()


def get_client() -> OpenAI:
    """根据环境变量创建 OpenAI 客户端（指向 DeepSeek）"""
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        st.error(
            "❌ 未找到环境变量 DEEPSEEK_API_KEY\n\n"
            "请在系统环境变量中配置，或在项目根目录创建 .env 文件：\n\n"
            "```\nDEEPSEEK_API_KEY=你的密钥\n```"
        )
        st.stop()
    return OpenAI(api_key=api_key, base_url="https://api.deepseek.com")


def chat(question: str, model: str = "deepseek-chat") -> str:
    """和 DeepSeek 模型对话，返回回答文本"""
    client = get_client()
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "你是一名非常可爱的 AI 助手，请用温柔可爱的语气回答用户的问题。"},
            {"role": "user", "content": question},
        ],
        stream=False,
    )
    return response.choices[0].message.content


# ========== 页面配置 ==========
st.set_page_config(
    page_title="WelcomeScreen · DeepSeek Demo",
    page_icon="🤖",
    layout="centered",
)

# ========== 欢迎页 ==========
st.title("🎉 WelcomeScreen")
st.subheader("一个由 DeepSeek 大模型驱动的 AI 对话 Demo")

st.markdown(
    """
    👋 欢迎来到 **WelcomeScreen**！

    - 🤖 使用 DeepSeek 大模型
    - 🔐 密钥通过环境变量管理，**安全无泄露**
    - 💬 在下方输入问题开始聊天吧
    """
)

st.divider()

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
user_input = st.chat_input("请输入你的问题...")
if user_input:
    # 显示用户消息
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    # 调用 AI
    with st.chat_message("assistant"):
        with st.spinner("AI 正在思考中..."):
            try:
                answer = chat(user_input)
                st.markdown(answer)
                st.session_state.messages.append({"role": "assistant", "content": answer})
            except Exception as e:
                st.error(f"出错了：{e}")
