# WelcomeScreen 🤖

一个由 **DeepSeek** 大模型驱动的 Streamlit 欢迎页 + 对话 Demo。

## ✨ 功能

- 🎉 漂亮的 Streamlit 欢迎页
- 💬 和 DeepSeek 模型对话，支持多轮上下文
- 🔐 密钥从环境变量 / `.env` 读取，**不在代码里明文出现**
- 🚀 一键部署到 Streamlit Community Cloud（免费）

## 📦 安装依赖

```bash
pip install -r requirements.txt
```

## 🔑 配置密钥

**方法 1：用 `.env` 文件（推荐）**

把 `.env.example` 复制成 `.env`，填入你的 key：

```powershell
# Windows PowerShell
Copy-Item .env.example .env
```

```bash
# macOS / Linux
cp .env.example .env
```

然后编辑 `.env`：

```
DEEPSEEK_API_KEY=sk-你的真实密钥
```

**方法 2：用系统环境变量**

```powershell
# Windows PowerShell（当前会话）
$env:DEEPSEEK_API_KEY="sk-你的真实密钥"
```

## 🚀 本地运行

```bash
streamlit run app.py
```

启动后浏览器会自动打开 http://localhost:8501

## ☁️ 部署到 Streamlit Cloud（让好友也能访问）

1. 把这个项目**先上传到你的 GitHub 私有仓库**
2. 打开 https://share.streamlit.io 用 GitHub 登录
3. **New app** → 选你的仓库
4. **Main file path** 填 `app.py`
5. 点 **Deploy**，几秒钟后获得免费网址

部署后在 Streamlit Cloud 的 **Settings → Secrets** 里配置 key：

```toml
DEEPSEEK_API_KEY = "sk-你的真实密钥"
```

## 📁 目录结构

```
streamlit kailun/
├── .env                  # 本地密钥（不会上传）
├── .env.example          # 密钥模板（会上传）
├── .gitignore            # 忽略 .env 等敏感文件
├── README.md             # 本文件
├── requirements.txt      # Python 依赖
├── app.py                # Streamlit 主程序
└── deepseek_demo.py      # 命令行版本（可选）
```

## ⚠️ 安全提示

- **不要**把 `.env` 或含真实 key 的文件提交到 Git
- `.gitignore` 已经默认忽略 `.env`
- 旧的明文 key（如已在聊天截图中泄露）请**立即到 DeepSeek 控制台重置**

## 📄 License

MIT
