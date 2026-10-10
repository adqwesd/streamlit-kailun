# -*- coding: utf-8 -*-
"""
DeepSeek 模型调用示例
请先在系统环境变量中设置 DEEPSEEK_API_KEY，或在项目根目录的 .env 文件中配置。
"""

import os
from openai import OpenAI
from dotenv import load_dotenv

# 自动加载项目根目录下的 .env 文件（本地开发用）
load_dotenv()


def get_client() -> OpenAI:
    """根据环境变量创建 OpenAI 客户端（指向 DeepSeek）"""
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise ValueError(
            "未找到环境变量 DEEPSEEK_API_KEY。\n"
            "请在系统环境变量中配置，或在项目根目录创建 .env 文件：\n"
            "  DEEPSEEK_API_KEY=你的密钥"
        )
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


if __name__ == "__main__":
    print("===== DeepSeek 对话 Demo =====")
    user_input = input("你: ").strip()
    if not user_input:
        print("（输入为空，程序退出）")
    else:
        answer = chat(user_input)
        print(f"AI: {answer}")
