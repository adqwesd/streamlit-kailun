# -*- coding: utf-8 -*-
"""
WelcomeScreen - Streamlit + DeepSeek Agent（带工具调用）

真实工具（非模拟）：
- get_current_time：获取当前时间
- calculate：数学计算
- get_weather：调用高德地图 API 查询天气

部署到 Streamlit Cloud 后，请在 Settings → Secrets 配置：
  DEEPSEEK_API_KEY = "sk-..."
  AMAP_API_KEY     = "你的高德 key"
"""

# ============== 修复 Streamlit Cloud 容器默认 ASCII 编码 ==============
# 必须在 import 任何东西之前设置环境变量
import os as _os
_os.environ.setdefault("LC_ALL", "C.UTF-8")
_os.environ.setdefault("LANG", "C.UTF-8")
_os.environ.setdefault("PYTHONIOENCODING", "utf-8")
import sys as _sys
if hasattr(_sys.stdout, "reconfigure"):
    _sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    _sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
del _os, _sys

# ============== 正常 import ==============
import os
import re
import json
import datetime
from urllib.parse import quote
from urllib.request import urlopen, Request
from urllib.error import URLError
from openai import OpenAI
from dotenv import load_dotenv
import streamlit as st

# ============== 关键修复：monkey-patch openai JSON 编码 ==============
# Streamlit Cloud 容器 sys.getdefaultencoding() 是 ASCII，导致 openai 内部
# `bytes_str.encode()`（无参数）失败。强制用 UTF-8 重新实现 openapi_dumps。
import openai._utils._json as _oj_module

def _patched_openapi_dumps(obj):
    """和 openai SDK 的 _utils/_json.py 一致，但强制 UTF-8 编码"""
    return json.dumps(
        obj,
        cls=_oj_module._CustomEncoder,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")

_oj_module.openapi_dumps = _patched_openapi_dumps
del _oj_module
del _patched_openapi_dumps

# 加载本地 .env（仅本机使用，不会被 Git 上传）
load_dotenv()


# ============== 密钥读取 ==============
def get_secret(key: str) -> str:
    """优先从 st.secrets 读取（Streamlit Cloud），否则从环境变量/.env 读取"""
    try:
        return st.secrets[key]
    except (KeyError, FileNotFoundError):
        return os.environ.get(key) or ""


DEEPSEEK_API_KEY = get_secret("DEEPSEEK_API_KEY")
AMAP_API_KEY = get_secret("AMAP_API_KEY")


# ============== 真实工具函数（不是模拟） ==============

def tool_get_current_time() -> str:
    """返回当前时间"""
    now = datetime.datetime.now()
    return now.strftime("%Y-%m-%d %H:%M:%S %A")


def tool_calculate(expression: str) -> str:
    """
    计算数学表达式（沙箱执行）
    只允许数字、运算符、常用数学函数名，禁止其他字符。
    """
    if not isinstance(expression, str):
        return "错误：表达式必须是字符串"
    # 安全：只允许数字、运算符、小数点、空格、括号、e、pi 等
    safe_pattern = re.compile(r"^[\d\s\+\-\*\/\(\)\.\,\%eEpiPI]+$")
    if not safe_pattern.match(expression):
        return f"错误：表达式含不允许的字符: {expression!r}"
    try:
        # 用 math 命名空间
        import math as _math
        result = eval(expression, {"__builtins__": {}}, {
            "sin": _math.sin, "cos": _math.cos, "tan": _math.tan,
            "sqrt": _math.sqrt, "log": _math.log, "ln": _math.log,
            "log10": _math.log10, "exp": _math.exp, "pow": _math.pow,
            "pi": _math.pi, "e": _math.e, "abs": abs, "round": round,
            "min": min, "max": max,
        })
        return f"{expression} = {result}"
    except Exception as e:
        return f"计算失败: {e}"


def tool_get_weather(city: str) -> str:
    """
    调用高德地图天气 API 查询天气
    文档: https://lbs.amap.com/api/webservice/guide/api/weatherinfo
    """
    if not AMAP_API_KEY:
        return "错误：未配置 AMAP_API_KEY，无法查询天气。请在 Streamlit Secrets 中配置。"
    if not city or not isinstance(city, str):
        return "错误：城市名不能为空"

    # 第一步：先用"输入提示"接口把城市名 → adcode
    # 高德 API 简化处理：直接传 city 作为 keywords 让高德自动识别
    # 实测直接传中文城市名（如 "北京"）高德能识别
    url = (
        f"https://restapi.amap.com/v3/weather/weatherInfo"
        f"?city={quote(city)}&key={AMAP_API_KEY}&extensions=base&output=JSON"
    )
    try:
        req = Request(url, headers={"User-Agent": "Streamlit-DeepSeek-Agent/1.0"})
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except URLError as e:
        return f"网络请求失败: {e}"
    except Exception as e:
        return f"查询失败: {e}"

    status = data.get("status")
    if status != "1":
        return f"高德返回错误：status={status}, info={data.get('info')}, infocode={data.get('infocode')}"

    lives = data.get("lives") or []
    if not lives:
        return f"未找到 {city} 的天气信息"

    info = lives[0]
    return (
        f"📍 {info.get('city')} 当前天气：\n"
        f"  🌡️ 温度：{info.get('temperature')}°C\n"
        f"  💧 湿度：{info.get('humidity')}%\n"
        f"  🌬️ 风向：{info.get('winddirection')} 风力 {info.get('windpower')} 级\n"
        f"  ☁️ 天气：{info.get('weather')}\n"
        f"  🕐 报告时间：{info.get('reporttime')}"
    )


# ============== OpenAI 工具定义 ==============
TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "获取当前时间。当用户询问'现在几点'、'今天日期'、'星期几'等时间相关问题时调用。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate",
            "description": "执行数学计算。表达式使用 Python 语法，支持 + - * / () 和三角函数、sqrt、log、pi、e 等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "要计算的数学表达式，例如 '(3 + 5) * 2' 或 'sqrt(2) + pi'",
                    },
                },
                "required": ["expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "查询某个城市的实时天气。需要传入城市名（如 '北京'、'上海'）。使用高德地图 API。",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "城市名称，例如 '北京'、'上海'、'广州'",
                    },
                },
                "required": ["city"],
            },
        },
    },
]

# 工具名 → 真实函数映射
TOOL_FUNCS = {
    "get_current_time": tool_get_current_time,
    "calculate": tool_calculate,
    "get_weather": tool_get_weather,
}


# ============== 页面配置 ==============
st.set_page_config(
    page_title="WelcomeScreen · DeepSeek Agent",
    page_icon="🤖",
    layout="centered",
)

st.title("🎉 WelcomeScreen · Agent")
st.subheader("DeepSeek Agent：天气 🌤️ 计算 🧮 时间 🕐，自动调用真实工具")

st.markdown(
    """
    👋 这是一个**真正的 AI Agent**，不是模拟！

    当你问下面这些类型的问题，AI 会**自动调用工具**：
    - 🕐 **"现在几点？"** → 调用 `get_current_time`
    - 🧮 **"123 × 456 等于多少？"** → 调用 `calculate`
    - 🌤️ **"北京今天天气怎么样？"** → 调用 `get_weather`（高德 API）
    """
)

# ============== 侧边栏 ==============
with st.sidebar:
    st.header("⚙️ 设置")
    model = st.selectbox(
        "选择模型",
        ["deepseek-reasoner", "deepseek-chat"],
        index=0,  # 默认 reasoner（对工具调用更稳定）
        help="deepseek-reasoner：深度思考模型，对工具调用更稳定；deepseek-chat：通用对话模型",
    )
    temperature = st.slider("Temperature", min_value=0.0, max_value=1.5, value=0.5, step=0.1)
    system_prompt = st.text_area(
        "系统提示词",
        value=(
            "你是一名非常可爱的 AI 助手。请用温柔可爱的语气回答用户问题。\n"
            "【强制规则】当用户提到以下任何场景时，必须调用对应工具，绝不能凭空编造：\n"
            "  - 时间 / 日期 / 几点 / 星期 → get_current_time\n"
            "  - 数学 / 计算 / 算 / 等于 / + - * / 表达式 → calculate\n"
            "  - 天气 / 温度 / 下雨 / 城市 → get_weather\n"
            "其他闲聊问题正常回答即可。"
        ),
        height=160,
    )
    st.divider()
    if st.button("🗑️ 清空对话"):
        st.session_state.messages = []
        st.rerun()

# ============== 密钥校验 ==============
if not DEEPSEEK_API_KEY or DEEPSEEK_API_KEY == "sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx":
    st.error(
        "❌ 未检测到 DEEPSEEK_API_KEY\n\n"
        "**本地运行**：在 `.env` 文件中配置。\n\n"
        "**Streamlit Cloud**：到应用的 `Settings → Secrets` 添加：\n\n"
        "```toml\nDEEPSEEK_API_KEY = \"sk-你的密钥\"\n"
        "AMAP_API_KEY = \"你的高德 key\"\n```"
    )
    st.stop()

client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")


# ============== 对话区 ==============
st.divider()
st.header("💬 和 AI 聊聊天")

if "messages" not in st.session_state:
    st.session_state.messages = []

# 显示历史
for msg in st.session_state.messages:
    role = msg["role"]
    if role == "tool":
        # 工具结果用灰色块显示
        with st.chat_message("assistant"):
            st.markdown(f"🔧 **工具 `{msg.get('name', '')}` 返回：**\n\n```\n{msg['content']}\n```")
    elif role in ("user", "assistant"):
        with st.chat_message(role):
            st.markdown(msg["content"])


# ============== Agent 循环 ==============
def run_agent(user_input: str, model: str, temperature: float, system_prompt: str):
    """
    Agent 循环：不断调用直到没有 tool_calls 为止
    流式输出最终给用户的回复
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_input},
    ]

    # 第一轮：模型决定是否调用工具
    while True:
        try:
            response = client.chat.completions.create(
                model=model,
                temperature=temperature,
                messages=messages,
                tools=TOOLS_SCHEMA,
                tool_choice="auto",
                stream=False,  # 工具调用决策阶段不开流式
            )
        except Exception as e:
            return f"调用失败：{e}", []

        choice = response.choices[0]
        msg = choice.message

        # 1. 模型想调用工具
        if msg.tool_calls:
            # 把模型这轮的 tool_calls 也加入 messages
            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ],
            })

            # 执行每个工具调用
            for tc in msg.tool_calls:
                fn_name = tc.function.name
                fn_args_raw = tc.function.arguments or "{}"
                try:
                    fn_args = json.loads(fn_args_raw)
                except json.JSONDecodeError:
                    fn_args = {}

                fn = TOOL_FUNCS.get(fn_name)
                if not fn:
                    result = f"错误：未知工具 {fn_name}"
                else:
                    try:
                        result = fn(**fn_args)
                    except Exception as e:
                        result = f"工具执行出错：{e}"

                # 把工具结果加入 messages
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": fn_name,
                    "content": str(result),
                })

            # 继续下一轮（让模型基于工具结果继续生成）
            continue

        # 2. 模型不再调用工具，开始流式输出最终回复
        final_text = ""
        placeholder = st.empty()
        try:
            stream = client.chat.completions.create(
                model=model,
                temperature=temperature,
                messages=messages,
                stream=True,
            )
            for chunk in stream:
                delta = chunk.choices[0].delta.content or ""
                final_text += delta
                placeholder.markdown(final_text + "▌")
            placeholder.markdown(final_text)
        except Exception as e:
            final_text = f"流式输出失败：{e}"
            placeholder.markdown(final_text)

        return final_text, []


# ============== 用户输入 ==============
user_input = st.chat_input("请输入你的问题，回车发送...")
if user_input and user_input.strip():
    # 显示用户消息
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    # 调用 Agent（带工具的循环）
    with st.chat_message("assistant"):
        with st.spinner("思考中..."):
            final_text, _ = run_agent(user_input, model, temperature, system_prompt)

    st.session_state.messages.append({"role": "assistant", "content": final_text})