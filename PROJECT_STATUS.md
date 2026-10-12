# PROJECT_STATUS.md — streamlit-kailun 项目交接文档

最后更新：2026-10-12（第十二轮提交：主标题三行居中 + 登录窗口保留开关）

---

## 1. 当前目标

把 `my-python-project`（命令行版 DeepSeek Demo）改造成一个 **AI Agent Streamlit 应用**（"凯伦"智能管家），部署到 Streamlit Community Cloud，对外可访问。功能：

- 和 DeepSeek 大模型流式对话
- 三大真实工具（Function Calling，非模拟）：
  - `get_current_time`：返回当前北京时间
  - `calculate`：执行数学计算（沙箱 eval）
  - `get_weather`：调高德 API，未来 1-7 天预报
- 多轮记忆（自动截断超长上下文）
- 历史会话（本地 JSON，云端不可用）
- 登录窗口（已实现但默认关闭，代码完整保留）

---

## 2. 当前部署状态

| 项目 | 值 |
|---|---|
| 应用 URL | https://kailun-app.streamlit.app |
| GitHub 仓库 | git@github.com:adqwesd/streamlit-kailun.git（public，main 分支） |
| 远程最新 commit | `59d56cd` — "关闭登录窗口（代码保留，加 LOGIN_ENABLED 开关）" |
| Streamlit 应用状态 | 已部署（最近 12 轮改动全部上线） |
| Secrets 配置 | 已配 `DEEPSEEK_API_KEY`、`AMAP_API_KEY`（线上）；`APP_PASSWORD` 已加但暂不强制（LOGIN_ENABLED=False） |
| 本地 streamlit | 任务已被系统因内存不足停掉（最后任务 ID `b1s5h98cw`）。重启命令见 §11 |

---

## 3. 已完成内容

### 3.1 Agent 能力

- **纯 socket HTTPS 客户端**（`app.py` `_send_http_post` L437-523）：绕开 `http.client` / `urllib3` 的硬编码 `latin-1` 编码
- **流式输出**（`call_deepseek_stream` L626-670 + `st.write_stream`）：真正的逐字打字效果
- **多轮工具调用**（`_run_agent_loop` L898-971）：模型决策 → 调工具 → 结果回传 → 再次生成，最多 10 轮
- **三工具**：
  - `tool_get_current_time` L126-129（`datetime.now()`，已强制 Asia/Shanghai 时区）
  - `tool_calculate` L132-153（沙箱 eval + 字符白名单）
  - `tool_get_weather(city, days=7)` L156-244（高德 `extensions=all&forecast_days=N`，1-7 天）
- **多轮记忆**（`_build_messages_with_history` L868-896 + `MAX_CONTEXT_CHARS=24000` L865）：超长自动从最早截断

### 3.2 UI

- **侧边栏**（L734-812）：
  - Temperature 滑块（0-1.5）
  - 角色设定文本框（用户只填语气/性格，工具规则硬编码后置）
  - 历史会话列表 + 新对话/清空按钮（本地有效）
  - 退出登录按钮（仅 `LOGIN_ENABLED=True` 时显示）
  - 模型选择已**隐藏**（固定 `deepseek-chat`）
- **主页面**（L831-840）：三行居中欢迎语（HTML/CSS）
  ```
  您好。我是凯伦
  一个专属您的智能 ai 伴侣
  您可以从左侧菜单中定义专属您伴侣的名字，性格，以及地域。
  ```
- **登录窗口**（L681-731）：居中卡片，🔐 + 密码框 + 登录按钮，**默认关闭**（`LOGIN_ENABLED=False`）

### 3.3 已隐藏的元素

- 主页面原 `st.header("💬 和 AI 聊聊天")` 改成三行居中
- 调试 expander 移除
- `_diag_key` 内部函数保留但不再渲染到 UI
- 模型选择器从侧边栏移除
- 历史目录路径从侧边栏警告中隐藏

### 3.4 部署流程

- 仓库从 private 改 public（Streamlit Cloud 限制）
- 分支 `master` → `main`
- 主文件名 `streamlit_app.py` → `app.py`
- 通过 Streamlit Cloud `Settings → Secrets` 配置三个 key

---

## 4. 关键技术决策

### 4.1 弃用 openai SDK，改纯 socket（**最重要的决策**）

- 根因：Streamlit Cloud 容器 `sys.getdefaultencoding()='ascii'`，加上 `http.client.HTTPConnection.putheader()` 硬编码 `.encode('latin-1', 'strict')` 编码所有 header value
- 方案：完全丢弃 `urllib.request` / `http.client` / `requests`，用 `socket + ssl` 标准库自己拼 HTTP 报文
- 加 ASCII 校验，发现非 ASCII header 抛清晰错误（含位置 + Unicode 码点）

### 4.2 四重 UTF-8 防御（L19-55）

1. `locale.setlocale` 依次试 `C.UTF-8` / `en_US.UTF-8`
2. Monkey-patch `sys.getdefaultencoding` 返回 `utf-8`
3. `sys.stdout/stderr.reconfigure(encoding="utf-8")`
4. 强制 `TZ=Asia/Shanghai` + `time.tzset()`（解决 UTC 容器差 8 小时）

### 4.3 `load_dotenv(override=True)`（L83）

Windows Machine 级 `DEEPSEEK_API_KEY` 旧值会抢读，强制 `.env` 覆盖。

### 4.4 工具规则硬编码

`SYSTEM_PROMPT_RULES`（L250-257）拼在用户填的角色后面，确保模型必须调工具而非编造。用户只填语气/性格。

### 4.5 上下文截断用字符数估算

`MAX_CONTEXT_CHARS=24000`（L865）。粗略按"中文 1 字 ≈ 1.5-2 tokens"算，比精确 token 计数简单 100 倍，够用。

### 4.6 Agent 循环 + 工具调用解耦

- `_run_agent_loop`（L898-971）：同步非流式，只跑工具决策和执行
- `call_deepseek_stream`（L626-670）：纯流式输出最终回复
- 流程：用户输入 → spinner 跑 loop → 拿到最终 messages → `st.write_stream` 流式输出

### 4.7 历史会话用本地 JSON

`HISTORY_DIR = Path(r"D:\python-project\...")`（L263），Streamlit Cloud（Linux）不可访问，UI 自动 warning。

### 4.8 LOGIN_ENABLED 开关模式（L683）

所有"暂时关闭但保留代码"的功能都用 `XXX_ENABLED = False` 模式：
- 一行开关，不用注释代码
- 后续恢复只改 True 即可
- 退出登录按钮也用 `if LOGIN_ENABLED` 包起来

### 4.9 模型固定 `deepseek-chat`

之前的 `deepseek-reasoner` 切回 `deepseek-chat`（原因：reasoner 输出含推理过程时 UI 难处理）。模型选择已从 UI 移除。

### 4.10 Secrets 配三项

```toml
DEEPSEEK_API_KEY = "sk-..."
AMAP_API_KEY = "..."
APP_PASSWORD = "..."   # 仅 LOGIN_ENABLED=True 时用
```

---

## 5. 修改过的核心文件

### 5.1 `app.py`（958 行，单文件 Streamlit 应用）

**import**（L58-79）：
```
os, re, json, uuid, datetime, socket, ssl,
Path, urllib.parse.quote, urllib.request.{urlopen,Request},
urllib.error.URLError, requests(可选), dotenv.load_dotenv, streamlit
```

**代码结构**：

| 行号 | 段落 |
|------|------|
| L1-4 | 编码声明 + 模块 docstring |
| L6-55 | 强制 UTF-8 locale + 时区（用户最早指定） |
| L58-79 | 标准 import |
| L83 | `load_dotenv(override=True)` |
| L86-97 | `get_secret()` + 三个 key |
| L99-118 | `_diag_key()`（已不再渲染到 UI，保留备用） |
| L122 | `DEEPSEEK_URL` 常量 |
| L126-244 | 三个工具函数 |
| L248-257 | `SYSTEM_PROMPT_RULES`（硬编码工具规则） |
| L261-365 | 历史会话 CRUD（本地 JSON） |
| L369-432 | `TOOLS_SCHEMA` + `TOOL_FUNCS` |
| L437-523 | `_send_http_post`（**纯 socket**） |
| L526-586 | `_iter_body` + `_read_chunked` |
| L589-605 | `_post_json` |
| L608-670 | `call_deepseek_non_stream` / `call_deepseek_stream` |
| L674-679 | `st.set_page_config` |
| **L681-731** | **登录窗口**（LOGIN_ENABLED 开关） |
| L734-812 | 侧边栏（含退出登录） |
| L820-828 | 密钥校验 + 错误提示 |
| L831-840 | 主页面三行居中欢迎语 |
| L842-859 | Session state + 历史渲染 |
| L861-896 | 上下文截断（`MAX_CONTEXT_CHARS=24000`） |
| L898-971 | `_run_agent_loop`（同步工具循环） |
| L973+ | 用户输入处理（chat_input + spinner + st.write_stream） |

**不要在本地编辑时引入 openai SDK 依赖**（会触发 UTF-8 bug 历史问题）。

### 5.2 `requirements.txt`

```
streamlit>=1.30.0
requests>=2.28.0
python-dotenv>=1.0.0
```

`openai` 已移除。`requests` 保留（仅作 fallback，未使用）。

### 5.3 `.env`（本地用，**不入 Git**）

```
DEEPSEEK_API_KEY=<your key>
AMAP_API_KEY=<your key>
APP_PASSWORD=<your password>   # 可选，仅本地试登录用
```

### 5.4 `README.md` / `deepseek_demo.py` / `.gitignore` / `.env.example`

保留仓库原版未改。

---

## 6. 测试与验证结果

### 6.1 已通过的测试（用户截图确认）

| 测试 | 结果 |
|------|------|
| GitHub push 12 次 commit | ✅ 全部成功 |
| Streamlit Cloud 自动部署 | ✅ 每次 push 后 1-2 分钟内生效 |
| 主页三行居中欢迎语 | ✅ 用户截图确认 |
| 模型选择已隐藏 | ✅ |
| 调试信息已隐藏 | ✅ |
| 流式输出逐字显示 | ✅ |
| 多轮对话记忆 | ✅ |
| 工具调用（时间/计算/天气） | ✅ 已实测 |
| 时区为北京时间 | ✅ |
| 编码问题彻底解决（纯 socket） | ✅ 用户已验证 |

### 6.2 失败 / 已知遗留

| 测试 | 状态 |
|------|------|
| 历史会话在云端可用 | ❌ 路径是 Windows 本地，云端失效（设计如此） |
| 登录窗口在线上测试 | ⏸️ 已实现但关闭（`LOGIN_ENABLED=False`），用户选择"先不启用" |
| 本地 streamlit 后台任务 | ⚠️ 任务 `b1s5h98cw` 因系统内存不足被自动停掉 |

### 6.3 待验证

- 三个工具**最新代码**下的实际调用（最近几次重构后没专门再测）
- 高德 key 是否仍为"Web 服务"类型（如果换过账号）

---

## 7. 已知问题

### 7.1 历史会话在云端失效

`HISTORY_DIR` 是 Windows 路径，云端 Linux 容器不可访问。UI 会显示 warning。要解决需：
- 改用浏览器 localStorage（纯前端）
- 或接外部存储（Supabase / Firebase）

**当前决定**：暂不修，承认这个限制。

### 7.2 系统内存不足导致本地 streamlit 任务被停

最后任务 `b1s5h98cw` 因 Windows 内存不够被自动 kill。**不影响线上**（云端独立运行）。本地要看 UI 就重新启动。

### 7.3 退出登录在关闭状态下点了会清空消息

如果 `LOGIN_ENABLED=False` 时用户点"🚪 退出登录"按钮（已用 `if LOGIN_ENABLED` 包住，正常不可见），会把 `authenticated=False` + `messages=[]` + rerun。但因为登录窗口不弹，session state 残留。**已用条件渲染规避**（按钮本身不显示）。

### 7.4 登录窗口的"忘记密码"流程未实现

只做了登录本身，没做"忘记密码" / "邀请码" / "用户注册"等扩展。

### 7.5 `requests` 已不再使用

保留 import 是为 fallback。如果确认永远不用，可以删。

---

## 8. 尝试过但失败的方案

### 8.1 ❌ openai SDK 原生调用

- `f75379f` Monkey-patch `openai._utils._json.openapi_dumps` —— 无效，名字已绑定
- `6cd9f23` 也 patch `_base_client.openapi_dumps` —— 仍无效，错误源是 urllib3 的 latin-1

### 8.2 ❌ `requests.post(json=payload)`

- `1c45dd9` 改用 requests —— 仍报 `latin-1`
- 推测是 requests 内部某处 `.encode('latin-1')` 触发

### 8.3 ❌ `urllib.request` + 手动 bytes

- `0b400f2` 顶层加 `en_US.UTF-8` locale
- `b0268cd` 改 urllib.request
- 仍失败，根因是 `http.client.HTTPConnection.putheader` 硬编码 latin-1

### 8.4 ✅ 最终方案：纯 `socket` + `ssl`（`588c804`）

完全抛弃标准库 HTTP 栈，手动拼 HTTP 报文。**这是当前已部署的方案**。

### 8.5 ❌ deepseek-reasoner

之前默认 reasoner，后来切回 chat。原因：reasoner 在流式输出中先输出 `reasoning_content`（思考过程）再输出 `content`，UI 体验割裂。

---

## 9. 下一步开发顺序

### 9.1 【P0 立即】重启本地 streamlit 看 UI

系统因内存不足停了任务。手动重启验证最后改动。

```bash
cd D:\tools\tools\ai工具\Trae work CN\6ac77b864784f64088a3e3c0\my-python-project
streamlit run app.py --server.port 8501 --server.address localhost
```

### 9.2 【P0 立即】线上加 `APP_PASSWORD` 到 Secrets（可选）

如果想开启登录，步骤：
1. 改 `app.py` L683 `LOGIN_ENABLED = True`
2. 改 `app.py` L714 按钮文案或保留
3. 在 Streamlit Cloud Secrets 加 `APP_PASSWORD = "你的密码"`
4. 推送 commit

### 9.3 【P1 重要】修历史会话云端失效

方案选择（待定）：
- A. 改用浏览器 `st.session_state` 持久化（仅限单浏览器）
- B. 接 Supabase 免费层（用户级持久化）
- C. 接 Firebase（类似 Supabase）

### 9.4 【P1 重要】真正测试三个工具

最近 12 轮重构后没有专门测过"现在几点 / 3*4 / 北京天气"。

### 9.5 【P2 可选】UI 增强

- 登录窗口加 logo
- 主页面加历史会话预览卡片
- 加"导出对话"按钮（JSON / Markdown）
- 加"清空对话"二次确认

### 9.6 【P2 可选】更多工具

- 网页搜索（接搜索 API）
- 翻译（接翻译 API）
- 文件读写
- 图片生成（DALL-E / Stable Diffusion）

### 9.7 【P3】性能优化

- 工具调用缓存（同一查询不重复调）
- 长对话的真正 token 计数（用 tiktoken）
- 流式首字延迟优化

### 9.8 【P3】多用户隔离（如果启用登录后）

当前通用密码所有人共享同一会话状态（`st.session_state.messages`）。要做真多用户需要：
- 按 IP / cookie 区分 session
- 或用外部数据库存历史

---

## 10. 关键链接

| 用途 | URL |
|------|-----|
| 应用 | https://kailun-app.streamlit.app |
| GitHub | https://github.com/adqwesd/streamlit-kailun |
| Streamlit Cloud 控制台 | https://share.streamlit.io/ |
| Streamlit Secrets 文档 | https://docs.streamlit.io/streamlit-community-cloud/get-started/deploy-an-app/connect-to-data-sources/secrets-management |
| 高德 key 控制台 | https://console.amap.com/dev/key/app |
| DeepSeek 控制台 | https://platform.deepseek.com/ |

---

## 11. 本地开发注意事项

### 11.1 重启 streamlit

```powershell
Set-Location "D:\tools\tools\ai工具\Trae work CN\6ac77b864784f64088a3e3c0\my-python-project"
streamlit run app.py --server.port 8501 --server.address localhost
```

或在后台跑（避免本对话重启）：
```powershell
Start-Process powershell -ArgumentList "streamlit run app.py --server.port 8501" -WindowStyle Hidden
```

### 11.2 推送代码

```powershell
cd <项目目录>
git add app.py
git commit -m "<改动说明>"
git push origin main
```

Streamlit Cloud 会自动拉取并部署（约 1-2 分钟）。

### 11.3 不要做的事

- ❌ `pip install openai`（不再使用，会引发 UTF-8 历史问题）
- ❌ 改 `HISTORY_DIR` 路径到云端能写的位置（除非有持久化方案）
- ❌ 删除登录代码（哪怕 LOGIN_ENABLED=False 也保留）

### 11.4 部署前自检清单

- [ ] 本地能正常聊天
- [ ] `git status` 干净
- [ ] `.env` 不在 commit 里
- [ ] commit message 描述清楚改了什么

---

## 12. 关键 commit 索引

```
59d56cd  关闭登录窗口（代码保留，加 LOGIN_ENABLED 开关）         ← 当前
a43a3f2  添加通用密码登录窗口（居中卡片）
7b5bf64  欢迎语改为三行居中: 凯伦/智能ai伴侣/菜单说明
40495d1  主标题改为三行居中显示
6e04942  主标题改为三行: 您好我叫凯伦/我是痞老板的智能管家/...
a225c06  改主标题为凯伦智能管家欢迎语
e935ba2  Hide model selector from sidebar
5bf5ee8  Add memory + true streaming + context length guard
fda864c  Hide internal docs and debug UI; fix _read_chunked bug; override .env
0c0e8c1  UI: hide history directory path in sidebar
c7dc8b2  Add conversation history feature
3454f81  Refactor system prompt: hardcode tool rules
f66785e  Weather tool: support multi-day forecast (1-7 days, default 7)
ef4ec5c  Fix timezone: force Asia/Shanghai
588c804  Fix latin-1 bug: pure socket implementation
b0268cd  Fix UTF-8: urllib.request + multi-layer defense
0b400f2  Set en_US.UTF-8 locale
1c45dd9  Replace openai SDK with direct requests
6cd9f23  Patch _base_client.openapi_dumps
f75379f  Monkey-patch openai SDK
49f1de0  Fix ASCII codec error
7fb68e0  Upgrade to DeepSeek Agent with real tools
b59d512  Streaming output + sidebar settings
1761e8f  Initial commit
```

`git show <sha>` 可看具体改动。

---

## 13. 联系人

- 仓库所有者：adqwesd（GitHub）
- 开发会话：本会话期间完成 12 轮提交
- 文档最后更新：2026-10-12
