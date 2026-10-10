# -*- coding: utf-8 -*-
"""
WelcomeScreen - Streamlit + DeepSeek Agent（带工具调用）
"""

# ============== 强制 UTF-8 编码（四重防御，解决 Streamlit Cloud ASCII/latin-1 问题） ==============
# 这一段必须在所有其它 import 之前执行。
#
# 根因：Streamlit Cloud 容器的 Python 默认编码是 'ascii'，
#       任何调用 .encode() 不带 encoding 参数的代码，遇到中文都会爆。
#       加上 urllib3 在编码 HTTP header 时硬编码用 latin-1，
#       导致即使手动 data=body_bytes 也会在 header 处理路径上失败。
#
# 防御策略（按优先级）：
#   1. locale fallback：C.UTF-8（最通用，几乎所有 Linux 容器都有）→ en_US.UTF-8
#   2. Monkey-patch sys.getdefaultencoding() 返回 'utf-8'
#   3. 重新配置 stdout/stderr 为 utf-8
#   4. 强制时区为 Asia/Shanghai（Streamlit Cloud 容器默认 UTC）
import locale as _locale
import os as _os
import sys as _sys

for _loc in ("C.UTF-8", "C.utf-8", "en_US.UTF-8", "en_US.utf-8"):
    try:
        _locale.setlocale(_locale.LC_ALL, _loc)
        _os.environ["LC_ALL"] = _loc
        _os.environ["LANG"] = _loc
        break
    except _locale.Error:
        continue
del _loc

_os.environ["PYTHONIOENCODING"] = "utf-8"
_os.environ["PYTHONLEGACYWINDOWSFSENCODING"] = "utf-8"
# 强制时区为北京时间（UTC+8）。Streamlit Cloud 容器默认是 UTC，
# 不设置的话 datetime.datetime.now() 会返回 UTC 时间（差 8 小时）。
_os.environ["TZ"] = "Asia/Shanghai"
try:
    import time as _time
    _time.tzset()  # 仅 Linux/Unix 有效；Windows 跳过
except (AttributeError, OSError):
    pass


def _forced_utf8_getdefaultencoding():
    return "utf-8"


_sys.getdefaultencoding = _forced_utf8_getdefaultencoding

if hasattr(_sys.stdout, "reconfigure"):
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    _sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

del _locale, _os, _sys

"""
真实工具（非模拟）：
- get_current_time：获取当前时间
- calculate：数学计算
- get_weather：调用高德地图 API 查询天气

为什么不用 openai SDK？
  openai SDK 内部某处调用 .encode() 不带 encoding 参数，在 Streamlit Cloud
  容器（sys.getdefaultencoding()='ascii'）下遇到中文会爆 'ascii' codec error。

为什么不用 requests / urllib.request / http.client？
  - requests 底层用 urllib3，urllib3 编码 HTTP header 时调 .encode('latin-1')
  - urllib.request 底层走 http.client.HTTPConnection
  - http.client.HTTPConnection.putheader() 硬编码用 latin-1 编码所有 header value
  任何一个路径都会爆 'latin-1' codec error。
  改用纯 socket + ssl 自己拼 HTTP 报文，彻底绕开所有标准库编码路径。

部署到 Streamlit Cloud 后，请在 Settings → Secrets 配置：
  DEEPSEEK_API_KEY = "sk-..."
  AMAP_API_KEY     = "你的高德 key"
"""

# ============== 正常 import ==============
import os
import re
import json
import datetime
import socket
import ssl
from urllib.parse import quote
from urllib.request import urlopen, Request
from urllib.error import URLError

# requests 不再用于 DeepSeek 请求路径（会触发 urllib3 latin-1 bug），
# 但保留 import 以便将来如果需要 fallback 时可用。
try:
    import requests  # noqa: F401
except ImportError:
    pass

from dotenv import load_dotenv
import streamlit as st

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

# DeepSeek 兼容 OpenAI 接口
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"


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
    safe_pattern = re.compile(r"^[\d\s\+\-\*\/\(\)\.\,\%eEpiPI]+$")
    if not safe_pattern.match(expression):
        return f"错误：表达式含不允许的字符: {expression!r}"
    try:
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


def tool_get_weather(city: str, days: int = 7) -> str:
    """
    调用高德地图天气 API，返回未来 N 天天气预报（默认 7 天）。
    使用 extensions=all 接口（预报天气），不是 extensions=base（实时天气）。

    Args:
        city: 城市名称，如 "北京"、"上海"
        days: 要返回的天数，1-7。默认 7（包含今天）。
              1 → 只今天；2 → 今天+明天；7 → 未来一周。
    """
    if not AMAP_API_KEY:
        return "错误：未配置 AMAP_API_KEY，无法查询天气。请在 Streamlit Secrets 中配置。"
    if not city or not isinstance(city, str):
        return "错误：城市名不能为空"

    # 限制 days 在 1-7
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 7
    days = max(1, min(7, days))

    url = (
        f"https://restapi.amap.com/v3/weather/weatherInfo"
        f"?city={quote(city)}&key={AMAP_API_KEY}"
        f"&extensions=all&forecast_days={days}&output=JSON"
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

    forecasts = data.get("forecasts") or []
    if not forecasts:
        return f"未找到 {city} 的天气信息"

    info = forecasts[0]
    city_name = info.get("city", city)
    reporttime = info.get("reporttime", "未知")
    casts = info.get("casts") or []
    if not casts:
        return f"未找到 {city_name} 的预报数据"

    # 周几映射（高德返回 1=周一, 2=周二, ..., 7=周日）
    week_map = {"1": "周一", "2": "周二", "3": "周三", "4": "周四", "5": "周五", "6": "周六", "7": "周日"}

    lines = [
        f"📍 {city_name} 未来 {len(casts)} 天天气预报",
        f"🕐 预报发布时间：{reporttime}",
        "",
    ]

    for i, cast in enumerate(casts):
        date = cast.get("date", "?")
        week_num = str(cast.get("week", "?"))
        week_cn = week_map.get(week_num, f"周{week_num}")
        day_weather = cast.get("dayweather", "?")
        night_weather = cast.get("nightweather", "?")
        day_temp = cast.get("daytemp", "?")
        night_temp = cast.get("nighttemp", "?")
        day_wind = cast.get("daywind", "?")
        night_wind = cast.get("nightwind", "?")
        day_power = cast.get("daypower", "?")
        night_power = cast.get("nightpower", "?")

        # 标签：第一天=今天，第二天=明天，第三天=后天
        if i == 0:
            label = "（今天）"
        elif i == 1:
            label = "（明天）"
        elif i == 2:
            label = "（后天）"
        else:
            label = ""

        lines.append(f"📅 {date} {week_cn} {label}".rstrip())
        lines.append(f"   ☀️ 白天：{day_weather}，{day_temp}°C，{day_wind}风 {day_power}级")
        lines.append(f"   🌙 夜间：{night_weather}，{night_temp}°C，{night_wind}风 {night_power}级")
        lines.append("")

    return "\n".join(lines).rstrip()


# ============== 工具 schema（OpenAI 兼容） ==============
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
            "description": (
                "查询某个城市的天气预报（默认未来 7 天，包含今天）。"
                "使用高德地图 API，每天预报包含白天/夜间天气、温度、风向风力。"
                "如果用户只想知道今天，传 days=1；问'明天'传 days=2；问'未来一周'传 days=7（或不传，默认 7）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "城市名称，例如 '北京'、'上海'、'广州'",
                    },
                    "days": {
                        "type": "integer",
                        "description": (
                            "要返回的天数，1-7。默认 7（包含今天）。"
                            "1=只今天；2=今天+明天；7=未来一周。"
                        ),
                        "minimum": 1,
                        "maximum": 7,
                        "default": 7,
                    },
                },
                "required": ["city"],
            },
        },
    },
]

TOOL_FUNCS = {
    "get_current_time": tool_get_current_time,
    "calculate": tool_calculate,
    "get_weather": tool_get_weather,
}


# ============== 关键：纯 socket 发 HTTPS POST（彻底绕开 http.client 的 latin-1 bug） ==============

def _send_http_post(host: str, path: str, headers: dict, body_bytes: bytes,
                    port: int = 443, use_https: bool = True, timeout: float = 60):
    """
    用纯 socket 发 HTTPS POST 请求。

    为什么不用 urllib.request 或 http.client？
      Python 标准库 http.client.HTTPConnection.putheader() 硬编码
      .encode('latin-1', 'strict') 编码所有 header value。
      含非 ASCII 字符的 header 会爆 'latin-1' codec error。
      用纯 socket 自己拼 HTTP 报文，绕开所有标准库编码路径。

    Args:
        host: 服务器域名
        path: URL 路径
        headers: 所有 value 必须是纯 ASCII；非 ASCII 抛清晰错误（含位置）
        body_bytes: 已编码好的请求体 bytes

    Returns:
        (status_code: int, resp_headers: dict, body_iter: generator)
        body_iter 逐块 yield body bytes（chunked encoding 已自动解码）
    """
    # 1. 校验所有 header value 是纯 ASCII
    for k, v in headers.items():
        try:
            v.encode("ascii")
        except UnicodeEncodeError as e:
            bad = [(i, c, f"U+{ord(c):04X}") for i, c in enumerate(v) if ord(c) >= 128]
            raise ValueError(
                f"HTTP header '{k}' 含非 ASCII 字符 (位置 {e.start}-{e.end})：{bad[:5]}\n"
                f"提示：检查 Streamlit Secrets 中的密钥配置，可能有多余的不可见字符。"
            ) from e

    # 2. 拼 HTTP 请求报文（全 ASCII）
    lines = [
        f"POST {path} HTTP/1.1",
        f"Host: {host}",
        "User-Agent: Streamlit-DeepSeek-Agent/1.0",
        "Accept: text/event-stream",
    ]
    for k, v in headers.items():
        lines.append(f"{k}: {v}")
    lines.append(f"Content-Length: {len(body_bytes)}")
    lines.append("Connection: close")
    lines.append("")
    lines.append("")

    request_bytes = "\r\n".join(lines).encode("ascii")

    # 3. 建立连接（含 HTTPS）
    sock = socket.create_connection((host, port), timeout=timeout)
    if use_https:
        ctx = ssl.create_default_context()
        sock = ctx.wrap_socket(sock, server_hostname=host)

    sock.sendall(request_bytes + body_bytes)

    # 4. 读 HTTP header（一次性读到 \r\n\r\n）
    header_buf = b""
    while b"\r\n\r\n" not in header_buf:
        chunk = sock.recv(1)
        if not chunk:
            raise RuntimeError("连接关闭，未读到完整 HTTP header")
        header_buf += chunk
        if len(header_buf) > 16384:
            raise RuntimeError("HTTP header 过大 (>16KB)")

    sep = header_buf.find(b"\r\n\r\n")
    raw_headers = header_buf[:sep].decode("latin-1")
    initial_body = header_buf[sep + 4:]

    # 5. 解析 status line
    status_line = raw_headers.split("\r\n")[0]
    parts = status_line.split(" ", 2)
    if len(parts) < 2:
        raise RuntimeError(f"无效 status line: {status_line!r}")
    status_code = int(parts[1])

    # 6. 解析其他 headers
    resp_headers = {}
    for line in raw_headers.split("\r\n")[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            resp_headers[k.strip().lower()] = v.strip()

    is_chunked = resp_headers.get("transfer-encoding", "").lower() == "chunked"

    return status_code, resp_headers, _iter_body(sock, initial_body, is_chunked)


def _iter_body(sock, initial_buffer, is_chunked):
    """generator: 逐块 yield 响应 body bytes，处理 chunked / 普通两种模式"""
    try:
        if is_chunked:
            yield from _read_chunked(sock, initial_buffer)
        else:
            # 无 Content-Length 或 Connection: close → 读到 EOF
            yield initial_buffer
            while True:
                chunk = sock.recv(8192)
                if not chunk:
                    break
                yield chunk
    finally:
        try:
            sock.close()
        except Exception:
            pass


def _read_chunked(sock, initial_buffer):
    """解码 chunked transfer encoding 的流"""
    buffer = initial_buffer
    while True:
        # 找 chunk size 行
        while b"\r\n" not in buffer:
            chunk = sock.recv(4096)
            if not chunk:
                return
            buffer += chunk

        line_end = buffer.find(b"\r\n")
        size_line = buffer[:line_end].decode("ascii", errors="replace").strip()
        chunk_size_str = size_line.split(";", 1)[0].strip()
        try:
            chunk_size = int(chunk_size_str, 16)
        except ValueError:
            return

        if chunk_size == 0:
            return  # 终止 chunk

        # 读 chunk 数据（可能跨多次 recv）
        buffer = buffer[line_end + 2:]
        while len(buffer) < chunk_size:
            chunk = sock.recv(max(4096, chunk_size - len(buffer)))
            if not chunk:
                return
            buffer += chunk

        yield buffer[:chunk_size]
        buffer = buffer[chunk_size:]
        # 跳过 trailing \r\n
        if buffer.startswith(b"\r\n"):
            buffer = buffer[2:]
        elif buffer.startswith(b"\n"):
            buffer = buffer[1:]


def _post_json(payload: dict):
    """发请求到 DeepSeek，返回 (status_code, resp_headers, body_iter)"""
    body_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Authorization": "Bearer " + DEEPSEEK_API_KEY,
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "text/event-stream",
    }
    return _send_http_post(
        host="api.deepseek.com",
        path="/v1/chat/completions",
        headers=headers,
        body_bytes=body_bytes,
        port=443,
        use_https=True,
        timeout=60,
    )


def call_deepseek_non_stream(messages, model, temperature):
    """非流式调用，返回完整响应 dict"""
    payload = {
        "model": model,
        "temperature": temperature,
        "messages": messages,
        "tools": TOOLS_SCHEMA,
        "tool_choice": "auto",
    }
    status_code, resp_headers, body_iter = _post_json(payload)
    full_body = b"".join(body_iter)
    if status_code >= 400:
        raise RuntimeError(
            f"DeepSeek API 返回 {status_code}：{full_body.decode('utf-8', errors='replace')[:500]}"
        )
    return json.loads(full_body.decode("utf-8", errors="replace"))


def call_deepseek_stream(messages, model, temperature):
    """流式调用，返回 generator，每个生成增量文本字符串（SSE 解析）"""
    payload = {
        "model": model,
        "temperature": temperature,
        "messages": messages,
        "stream": True,
    }
    status_code, resp_headers, body_iter = _post_json(payload)
    if status_code >= 400:
        full_body = b"".join(body_iter)
        raise RuntimeError(
            f"DeepSeek API 返回 {status_code}：{full_body.decode('utf-8', errors='replace')[:500]}"
        )

    # SSE 解析（响应是 utf-8 bytes，按行分割处理）
    buffer = b""
    for chunk in body_iter:
        buffer += chunk
        while b"\n" in buffer:
            line_bytes, buffer = buffer.split(b"\n", 1)
            try:
                line = line_bytes.decode("utf-8", errors="replace").strip()
            except Exception:
                continue
            if not line.startswith("data:"):
                continue
            data_str = line[5:].strip()
            if data_str == "[DONE]":
                return
            try:
                chunk_json = json.loads(data_str)
            except json.JSONDecodeError:
                continue
            choices = chunk_json.get("choices") or []
            if choices:
                delta = choices[0].get("delta") or {}
                content = delta.get("content") or ""
                if content:
                    yield content


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
        index=0,
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
            "【天气工具细节】get_weather 默认返回未来 7 天（含今天），用户问'今天'传 days=1，'明天'传 days=2。\n"
            "其他闲聊问题正常回答即可。"
        ),
        height=180,
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


# ============== 对话区 ==============
st.divider()
st.header("💬 和 AI 聊聊天")

if "messages" not in st.session_state:
    st.session_state.messages = []

# 显示历史
for msg in st.session_state.messages:
    role = msg["role"]
    if role == "tool":
        with st.chat_message("assistant"):
            st.markdown(f"🔧 **工具 `{msg.get('name', '')}` 返回：**\n\n```\n{msg['content']}\n```")
    elif role in ("user", "assistant"):
        with st.chat_message(role):
            st.markdown(msg["content"])


# ============== Agent 循环 ==============
def run_agent(user_input: str, model: str, temperature: float, system_prompt: str):
    """
    Agent 循环：调用模型 → 如果有 tool_calls 就执行工具 → 把工具结果加入 messages →
    再调一次模型 → 直到模型不再调用工具，最后流式输出回复。
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_input},
    ]

    while True:
        # 第一轮（决策阶段）：非流式
        try:
            resp_json = call_deepseek_non_stream(messages, model, temperature)
        except Exception as e:
            return f"调用失败：{e}", []

        choice = (resp_json.get("choices") or [{}])[0]
        msg = choice.get("message") or {}

        # 模型想调用工具
        tool_calls = msg.get("tool_calls")
        if tool_calls:
            # 把模型这一轮（带 tool_calls）加入 messages
            messages.append({
                "role": "assistant",
                "content": msg.get("content") or "",
                "tool_calls": [
                    {
                        "id": tc.get("id"),
                        "type": "function",
                        "function": {
                            "name": tc["function"]["name"],
                            "arguments": tc["function"]["arguments"],
                        },
                    }
                    for tc in tool_calls
                ],
            })
            # 执行每个工具
            for tc in tool_calls:
                fn_name = tc["function"]["name"]
                fn_args_raw = tc["function"].get("arguments") or "{}"
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
                # 工具结果加入 messages
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id"),
                    "name": fn_name,
                    "content": str(result),
                })
            continue  # 继续下一轮

        # 没有 tool_calls，进入最终流式输出
        final_text = ""
        placeholder = st.empty()
        try:
            for delta in call_deepseek_stream(messages, model, temperature):
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
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    with st.chat_message("assistant"):
        with st.spinner("思考中..."):
            final_text, _ = run_agent(user_input, model, temperature, system_prompt)

    st.session_state.messages.append({"role": "assistant", "content": final_text})