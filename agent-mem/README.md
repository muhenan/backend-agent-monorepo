# agent-mem

一个用来学习 **Mem0 memory** 的 Web Chatbot。后端先按用户身份从 Mem0 检索相关的长期记忆，再交给 OpenAI Agents SDK 生成回复，最后将本轮对话交给 Mem0 提取并保存值得记住的信息。前端可以直接输入或切换身份标识，不需要注册登录。

## 学习目标

- 理解短期对话上下文与跨会话长期记忆的区别。
- 观察 Mem0 `search` 如何检索相关记忆，以及 `add` 如何提取或更新记忆。
- 在界面中查看每轮的 recalled memories 和 saved memories。
- 查看每条记忆对应的 Mem0/Qdrant 记录 ID，并支持单条删除。
- 通过切换身份观察不同用户记忆的隔离，并清空当前身份的长期记忆。

## 技术栈

- Python 3.12、uv、FastAPI
- OpenAI Agents SDK (`openai-agents`)，通过 OpenAI 兼容接口调用 DeepSeek
- Mem0 开源版 (`mem0ai`)
- Qdrant 本地向量数据库
- 原生 HTML / CSS / JavaScript Web UI
- Docker Compose

## 快速开始

需要 Docker Desktop（或 Docker Engine + Compose）、DeepSeek API Key 和 OpenAI API Key。DeepSeek 用于聊天及记忆提取，OpenAI 仅用于 embedding。

1. 在项目目录创建本地环境变量文件：

   ```sh
   cp .env.example .env
   ```

2. 编辑 `.env`，填写 `DEEPSEEK_API_KEY` 和 `OPENAI_API_KEY`。DeepSeek 负责对话和 Mem0 记忆提取；OpenAI `text-embedding-3-small` 负责将记忆和查询转换为向量。

3. 启动 Web 应用及 Qdrant：

   ```sh
   make up
   ```

4. 浏览器打开 <http://localhost:8000> 使用聊天界面，或打开 Qdrant Dashboard：<http://localhost:6333/dashboard>。

   在 Dashboard 的 **Collections** 中选择 `agent_mem` 集合，可以查看数据点及其 payload（包括 Mem0 保存的记忆内容）。

## 体验记忆

在浏览器里告诉助手：

```text
我叫小林，是一名后端工程师。我正在学习 Mem0，平时喜欢用中文交流。
```

页面右侧会显示 Mem0 新增或更新的记忆。右侧身份框可以填写另一个标识并切换，用于观察不同身份读取到的记忆互不相通。身份保存在当前浏览器的 localStorage 中；它只是记忆空间标识，不是登录认证或访问权限保护，任何能访问应用的人都可以输入其他标识。随后开始新对话，再问：

```text
我叫什么？我最近在学什么？
```

助手会搜索相似记忆并结合结果回答。右侧面板展示当前身份保存的记忆；左下角按钮只清空当前身份的长期记忆。

## 常用命令

```sh
make setup    # 创建 .env（如不存在）并生成 uv.lock
make up       # 构建镜像并启动 Web 应用和 Qdrant
make restart  # 重新构建并启动
make logs     # 跟踪应用和 Qdrant 日志
make shell    # 进入应用容器
make down     # 停止并移除容器
make lock     # 更新 uv.lock
make lint     # 在容器内运行 Ruff
make format   # 在容器内格式化代码
make test     # 在容器内运行 pytest
```

所有 Python 程序都在容器内运行。`uv` 用于依赖定义与锁文件；宿主机只需 Docker 和 Make。`.env` 是本地配置，已被 Git 忽略；`.env.example` 是可提交的配置模板。

## 目录结构

```text
agent-mem/
├── src/agent_mem/
│   ├── app.py            # FastAPI 路由与 HTTP API
│   ├── agent.py          # OpenAI Agents SDK Agent
│   ├── memory.py         # Mem0 配置、检索、写入、读取与删除
│   └── static/           # Web 聊天界面
├── tests/                # API 与记忆流程测试
├── pyproject.toml
├── uv.lock
├── Dockerfile
├── compose.yaml
├── Makefile
├── .env                  # 本地密钥与配置，不提交
├── .env.example          # 环境变量模板
└── README.md
```

## 一轮对话的数据流

```text
浏览器 POST /api/chat
       │
       ├─ Mem0 search(message, user_id) ──► 当前身份的相关长期记忆
       │                                           │
       └───────────────────────────────────────────┴─► Agents SDK Agent ──► 回复
                                                                          │
                   浏览器展示回复和记忆活动 ◄── Mem0 add(本轮对话, user_id) ◄┘
```

这个示例把 `search`、`add`、`get_all` 和 `delete` 放在应用编排中，让 Mem0 的读写过程直接可见。每条记忆卡显示其 Mem0 记录 ID；删除时先确认该 ID 属于当前 `user_id`，再删除对应记录。Agents SDK 的回复由应用显式注入 Mem0 搜索结果，不是通过 Agent tool call；Mem0 的记忆提取都使用 DeepSeek。Mem0 embedding 使用 OpenAI `text-embedding-3-small`，需要单独配置 `OPENAI_API_KEY`。向量维度为 1536，并与 Qdrant collection 配置保持一致。

## 环境变量

| 变量 | 用途 | 默认值 |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` | DeepSeek 对话与记忆提取 API Key | 必填 |
| `DEEPSEEK_BASE_URL` | DeepSeek API 地址 | `https://api.deepseek.com` |
| `DEEPSEEK_MODEL` | Chatbot 和 Mem0 记忆提取模型 | `deepseek-flash` |
| `OPENAI_API_KEY` | Mem0 embedding API Key | 必填 |
| `MEM0_QDRANT_HOST` | Qdrant 服务名 | `qdrant` |
| `MEM0_QDRANT_PORT` | Qdrant 端口 | `6333` |
| `MEM0_COLLECTION` | 向量集合名称 | `agent_mem` |
| `APP_PORT` | Web 服务宿主机端口 | `8000` |
