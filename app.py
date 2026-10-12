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


# ============== 正常 import ==============
import os
import re
import json
import uuid
import datetime
import socket
import ssl
from pathlib import Path
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
# override=True 强制 .env 覆盖系统环境变量（避免被 Machine 级旧 key 抢读）
load_dotenv(override=True)


# ============== 密钥读取 ==============
def get_secret(key: str) -> str:
    """优先从 st.secrets 读取（Streamlit Cloud），否则从环境变量/.env 读取"""
    try:
        return st.secrets[key]
    except (KeyError, FileNotFoundError):
        return os.environ.get(key) or ""


DEEPSEEK_API_KEY = get_secret("DEEPSEEK_API_KEY")
AMAP_API_KEY = get_secret("AMAP_API_KEY")
APP_PASSWORD = get_secret("APP_PASSWORD")


def _diag_key(value: str, name: str) -> str:
    """返回 key 的诊断信息（脱敏）"""
    if not value:
        return f"❌ {name} 未配置"
    lines = [
        f"**{name} 长度**：{len(value)}",
        f"**前 4 字符**：`{value[:4]}`",
        f"**后 4 字符**：`{value[-4:]}`",
        f"**首字符 ord**：{ord(value[0])}",
        f"**尾字符 ord**：{ord(value[-1])}",
    ]
    if name == "DEEPSEEK_API_KEY":
        lines.append(f"**以 'sk-' 开头**：{'✅' if value.startswith('sk-') else '❌'}")
    # 检查非可打印字符
    bad = [(i, c, ord(c)) for i, c in enumerate(value) if ord(c) < 32 or ord(c) > 126]
    if bad:
        lines.append(f"⚠️ 发现非可打印字符：{bad[:5]}")
    else:
        lines.append("✅ 所有字符都是可打印 ASCII")
    return "\n\n".join(lines)


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


# ============== 系统提示词的强制规则（硬编码，不让用户改） ==============
# 强制规则确保 AI 必须调用工具而不是凭空编造时间/计算/天气结果。
# 用户在侧边栏只填角色部分（语气/性格），规则会自动附加。
SYSTEM_PROMPT_RULES = (
    "\n\n"
    "【强制规则】当用户提到以下任何场景时，必须调用对应工具，绝不能凭空编造：\n"
    "  - 时间 / 日期 / 几点 / 星期 → get_current_time\n"
    "  - 数学 / 计算 / 算 / 等于 / + - * / 表达式 → calculate\n"
    "  - 天气 / 温度 / 下雨 / 城市 → get_weather（默认未来 7 天，问'今天'传 days=1，问'明天'传 days=2）\n"
    "其他闲聊问题正常回答即可。"
)


# ============== 历史会话存储 ==============
# ⚠️ 路径是本地 Windows 路径，Streamlit Cloud（Linux）上不可访问。
# 在云端部署时，历史功能会自动失效，UI 上会显示警告。
HISTORY_DIR = Path(r"D:\python-project\WelcomeScreen\streamlit_kailun\Conversation")


def _ensure_history_dir() -> bool:
    """确保历史目录存在；返回是否可用（True=可用，False=不可用）"""
    try:
        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        return True
    except OSError:
        return False


def _auto_title(messages: list) -> str:
    """用第一条 user 消息前 30 字作为标题"""
    for msg in messages:
        if msg.get("role") == "user":
            content = msg.get("content", "").strip()
            if content:
                return content[:30] + ("…" if len(content) > 30 else "")
    return "新对话"


def _save_session(session_id: str, messages: list, title: str = None) -> bool:
    """保存会话到 JSON 文件。返回是否成功。"""
    if not messages:
        return False
    if not _ensure_history_dir():
        return False
    if not title:
        title = _auto_title(messages)

    filepath = HISTORY_DIR / f"{session_id}.json"
    is_new = not filepath.exists()

    # 读取已有文件，保留 created_at
    existing_created = None
    if not is_new:
        try:
            existing = json.loads(filepath.read_text(encoding="utf-8"))
            existing_created = existing.get("created_at")
        except (json.JSONDecodeError, OSError):
            pass

    now_iso = datetime.datetime.now().isoformat(timespec="seconds")
    session = {
        "id": session_id,
        "title": title,
        "created_at": existing_created or now_iso,
        "updated_at": now_iso,
        "messages": messages,
    }

    try:
        filepath.write_text(
            json.dumps(session, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return True
    except OSError:
        return False


def _load_all_sessions() -> list:
    """加载所有历史会话（按 updated_at 倒序）"""
    if not HISTORY_DIR.exists():
        return []
    sessions = []
    for filepath in HISTORY_DIR.glob("*.json"):
        try:
            data = json.loads(filepath.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "id" in data:
                sessions.append(data)
        except (json.JSONDecodeError, OSError):
            continue
    return sorted(
        sessions,
        key=lambda s: s.get("updated_at", ""),
        reverse=True,
    )


def _load_session_messages(session_id: str) -> list:
    """加载指定会话的 messages 列表"""
    filepath = HISTORY_DIR / f"{session_id}.json"
    if not filepath.exists():
        return []
    try:
        data = json.loads(filepath.read_text(encoding="utf-8"))
        return data.get("messages", []) if isinstance(data, dict) else []
    except (json.JSONDecodeError, OSError):
        return []


def _delete_session(session_id: str) -> bool:
    """删除指定会话文件"""
    filepath = HISTORY_DIR / f"{session_id}.json"
    try:
        if filepath.exists():
            filepath.unlink()
            return True
    except OSError:
        pass
    return False


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
    """解码 chunked transfer encoding 的流。

    简化方案：因为请求带了 'Connection: close'，服务器关闭 socket 时即响应结束。
    所以一次性把整个 body 读到内存里，再用 regex 拆 chunks。
    这样能彻底避开"socket 延迟把 trailing \\r\\n 单独发来"的边界情况。
    """
    # 1. 一次性读完所有 body（Connection: close 模式）
    buffer = initial_buffer
    while True:
        chunk = sock.recv(8192)
        if not chunk:
            break
        buffer += chunk
        # 兜底：限制最大 body 大小（防止内存爆）
        if len(buffer) > 50 * 1024 * 1024:
            raise RuntimeError("Response body 超过 50MB")

    # 2. 用 regex 拆 chunked 块
    import re
    # 匹配 "<hex_size>\r\n<data>\r\n"
    # 注意：size 行可能包含 chunk-extension（用 ; 分割），忽略
    pattern = re.compile(rb'([0-9a-fA-F]+)(?:;[^\r\n]*)?\r\n', re.ASCII)
    pos = 0
    while pos < len(buffer):
        m = pattern.search(buffer, pos)
        if not m:
            return
        try:
            chunk_size = int(m.group(1), 16)
        except ValueError:
            return
        if chunk_size == 0:
            return  # 终止 chunk
        data_start = m.end()
        data_end = data_start + chunk_size
        if data_end > len(buffer):
            # 截断的 chunk，忽略
            return
        yield buffer[data_start:data_end]
        pos = data_end + 2  # 跳过 trailing \r\n（如果存在）


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
                # reasoner 模型的思考过程（先于 content 输出）
                reasoning = delta.get("reasoning_content") or ""
                if reasoning:
                    yield reasoning
                # 最终回复内容
                content = delta.get("content") or ""
                if content:
                    yield content


# ============== 页面配置 ==============
st.set_page_config(
    page_title="WelcomeScreen · DeepSeek Agent",
    page_icon="🤖",
    layout="centered",
)

# ============== 登录校验（未登录就显示登录窗口并停止渲染） ==============
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    # 登录窗口（居中卡片）
    _, mid, _ = st.columns([1, 1.2, 1])
    with mid:
        st.markdown(
            "<div style='text-align: center; padding: 12px 0 4px 0;'>"
            "<span style='font-size: 42px;'>🔐</span></div>",
            unsafe_allow_html=True,
        )
        st.markdown(
            "<h3 style='text-align: center; margin-top: 0;'>访问凯伦智能管家</h3>",
            unsafe_allow_html=True,
        )
        st.markdown(
            "<p style='text-align: center; color: gray; margin-top: -8px;'>请输入访问密码</p>",
            unsafe_allow_html=True,
        )
        pwd = st.text_input(
            "密码",
            type="password",
            placeholder="请输入密码...",
            key="login_pwd",
            label_visibility="collapsed",
        )
        col_btn_l, col_btn_r = st.columns([1, 1])
        with col_btn_r:
            login_clicked = st.button("🔓 登录", use_container_width=True, type="primary")
        if login_clicked:
            if not APP_PASSWORD:
                st.error("⚠️ 未配置 APP_PASSWORD。请在 Streamlit Secrets 或 .env 添加。")
            elif pwd == APP_PASSWORD:
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("❌ 密码错误，请重试")

        # 提示
        st.markdown(
            "<p style='text-align: center; color: #999; font-size: 12px; margin-top: 24px;'>"
            "没有密码？请联系管理员获取"
            "</p>",
            unsafe_allow_html=True,
        )
    st.stop()  # 没登录就不渲染下面的内容


# ============== 侧边栏 ==============
with st.sidebar:
    st.header("⚙️ 设置")
    # 模型固定为 deepseek-chat（用户不可选）
    model = "deepseek-chat"
    temperature = st.slider("Temperature", min_value=0.0, max_value=1.5, value=0.5, step=0.1)
    # 侧边栏：只让用户填角色部分（语气/性格/人物设定）
    role_prompt = st.text_area(
        "角色设定",
        value="你是一名非常可爱的 AI 助手。请用温柔可爱的语气回答用户问题。",
        height=100,
        help="这里只填 AI 的角色/语气/性格。工具调用的强制规则在代码里硬编码，会自动附加。",
    )
    st.divider()

    # ============== 历史会话区 ==============
    st.header("📚 历史会话")

    history_available = _ensure_history_dir()
    if not history_available:
        st.warning(
            f"⚠️ 历史目录不可用：`{HISTORY_DIR}`\n\n"
            "（Streamlit Cloud 上历史功能不可用，本地运行有效）"
        )
    else:
        # 顶部操作按钮
        col1, col2 = st.columns(2)
        with col1:
            if st.button("🆕 新对话", use_container_width=True):
                st.session_state.messages = []
                st.session_state.current_session_id = str(uuid.uuid4())
                st.session_state.session_title = "新对话"
                st.rerun()
        with col2:
            if st.button("🗑 清空", use_container_width=True):
                st.session_state.messages = []
                st.rerun()

        st.divider()

        # 列出历史会话（最多 30 条）
        sessions = _load_all_sessions()
        if not sessions:
            st.caption("暂无历史对话。开始聊天后会自动保存。")
        else:
            for sess in sessions[:30]:
                sid = sess.get("id", "")
                title = sess.get("title", "未命名") or "未命名"
                updated = sess.get("updated_at", "")[:16].replace("T", " ")
                is_current = sid == st.session_state.get("current_session_id")

                # 标题 + 当前标记
                prefix = "👉 " if is_current else "📄 "
                col_a, col_b = st.columns([5, 1])
                with col_a:
                    if st.button(
                        f"{prefix}{title}",
                        key=f"load_{sid}",
                        use_container_width=True,
                        disabled=is_current,
                    ):
                        msgs = _load_session_messages(sid)
                        st.session_state.messages = msgs
                        st.session_state.current_session_id = sid
                        st.session_state.session_title = title
                        st.rerun()
                with col_b:
                    if st.button("🗑", key=f"del_{sid}"):
                        _delete_session(sid)
                        if is_current:
                            st.session_state.messages = []
                            st.session_state.current_session_id = str(uuid.uuid4())
                            st.session_state.session_title = "新对话"
                        st.rerun()
                st.caption(f"   {updated} · {len(sess.get('messages', []))} 条消息")

    st.divider()

    # 退出登录
    if st.button("🚪 退出登录", use_container_width=True):
        st.session_state.authenticated = False
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
st.markdown(
    "<div style='text-align: center; line-height: 1.8; font-size: 18px;'>"
    "您好。我是凯伦<br>"
    "一个专属您的智能 ai 伴侣<br>"
    "您可以从左侧菜单中定义专属您伴侣的名字，性格，以及地域。"
    "</div>",
    unsafe_allow_html=True,
)

# Session state 初始化
if "messages" not in st.session_state:
    st.session_state.messages = []
if "current_session_id" not in st.session_state:
    st.session_state.current_session_id = str(uuid.uuid4())
if "session_title" not in st.session_state:
    st.session_state.session_title = "新对话"

# 显示历史
for msg in st.session_state.messages:
    role = msg["role"]
    if role == "tool":
        with st.chat_message("assistant"):
            st.markdown(f"🔧 **工具 `{msg.get('name', '')}` 返回：**\n\n```\n{msg['content']}\n```")
    elif role in ("user", "assistant"):
        with st.chat_message(role):
            st.markdown(msg["content"])


# ============== 上下文长度限制 ==============
# DeepSeek-chat 上下文窗口 32K-64K tokens。
# 中文 1 字 ≈ 1.5-2 tokens，英文 1 字 ≈ 0.25-0.5 tokens。
# 简单按字符数估算：超过 24000 字符时截断最早的历史。
MAX_CONTEXT_CHARS = 24000


def _build_messages_with_history(history: list, user_input: str, system_prompt: str) -> list:
    """
    构造发给 DeepSeek 的 messages 列表：
    [system, ...历史 user/assistant（可能截断）, user_input]

    当历史超过 MAX_CONTEXT_CHARS 时，丢弃最早的非 system 消息对。
    """
    messages = [{"role": "system", "content": system_prompt}]

    # 拼接所有历史消息的文本（用于估算长度）
    total_chars = len(system_prompt)
    kept_history = []
    for msg in history:
        # 只保留 user/assistant 消息（tool 消息留给 _run_agent_loop 内部处理）
        if msg.get("role") not in ("user", "assistant"):
            continue
        content = msg.get("content") or ""
        kept_history.append({"role": msg["role"], "content": content})
        total_chars += len(content)

    # 如果超限，从最早开始丢弃
    while total_chars > MAX_CONTEXT_CHARS and len(kept_history) > 2:
        removed = kept_history.pop(0)
        total_chars -= len(removed.get("content", ""))

    messages.extend(kept_history)
    messages.append({"role": "user", "content": user_input})
    return messages


# ============== Agent 循环 ==============
def _run_agent_loop(initial_messages: list, model: str, temperature: float):
    """
    同步处理多轮 Agent 循环（决策 + 工具调用）。

    参数:
      initial_messages: 已经包含 system + 历史 + 当前 user 的 messages 列表
      model, temperature: 模型参数

    返回 (messages, error)：
      - messages: 处理完所有工具调用后的 messages 列表（最后一条是 user/assistant/tool）
      - error: None 表示成功，可继续流式输出；非 None 表示出错（已经是错误字符串）

    调用方在拿到 messages 后再调 call_deepseek_stream 流式输出最终回复。
    """
    messages = list(initial_messages)  # 复制，避免改外部

    for _ in range(10):  # 最多 10 轮，防止无限循环
        # 决策阶段：非流式调用模型
        try:
            resp_json = call_deepseek_non_stream(messages, model, temperature)
        except Exception as e:
            return messages, f"调用失败：{e}"

        choice = (resp_json.get("choices") or [{}])[0]
        msg = choice.get("message") or {}

        # 没有 tool_calls：循环结束，调用方开始流式输出
        tool_calls = msg.get("tool_calls")
        if not tool_calls:
            return messages, None

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
            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id"),
                "name": fn_name,
                "content": str(result),
            })

    return messages, "工具调用超过 10 轮，强制结束"


# ============== 用户输入 ==============
user_input = st.chat_input("请输入你的问题，回车发送...")
if user_input and user_input.strip():
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    with st.chat_message("assistant"):
        final_text = ""
        try:
            full_system_prompt = role_prompt + SYSTEM_PROMPT_RULES
            # 1. 构造带历史的 messages（自动截断超长上下文）
            history = st.session_state.messages[:-1]  # 排除刚 append 的 user
            initial_messages = _build_messages_with_history(
                history, user_input, full_system_prompt
            )
            # 2. 同步处理多轮工具调用（用户看不到过程，spinner 包裹）
            with st.spinner("思考中..."):
                messages, err = _run_agent_loop(
                    initial_messages, model, temperature
                )
            if err:
                # 出错：直接显示错误信息（不流式）
                st.markdown(err)
                final_text = err
            else:
                # 3. 流式输出最终回复（st.write_stream 实现真正的逐字显示）
                final_text = st.write_stream(
                    call_deepseek_stream(messages, model, temperature)
                )
                if not final_text:
                    st.markdown("（空回复）")
                    final_text = "（空回复）"
        except BaseException as e:
            # 捕获所有异常（包括非 Exception 子类），避免静默失败
            final_text = f"❌ 出错了：{type(e).__name__}: {e}"
            st.markdown(final_text)

    st.session_state.messages.append({"role": "assistant", "content": final_text})

    # 自动保存到历史（标题用首条 user 消息）
    if _ensure_history_dir():
        # 如果标题还是默认的"新对话"，更新成首条 user 消息
        if st.session_state.session_title == "新对话":
            st.session_state.session_title = _auto_title(st.session_state.messages)
        _save_session(
            st.session_state.current_session_id,
            st.session_state.messages,
            st.session_state.session_title,
        )