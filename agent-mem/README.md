# agent-mem

一个用来学习 **Mem0 memory** 的 Web Chatbot。OpenAI Agents SDK 根据当前会话判断是否调用 `search_memory` 工具搜索 Mem0；生成最终回复后，将本轮用户消息和回复交给 Mem0 提取并保存值得记住的信息。前端可以直接输入或切换身份标识，不需要注册登录。

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
浏览器 POST /api/chat（当前消息 + 最近 20 条会话消息）
       │
       └─ Agents SDK Agent
              ├─ 上下文足够：直接回答
              └─ 缺少历史：search_memory(query) → 当前身份的 Mem0 记忆 → 回答
                                                                    │
                         展示实际写入事件 ◄── Mem0 add(本轮用户消息、最终回复)
```

没有固定前置搜索，也没有独立路由模型。搜索工具只接受 query，user_id 由服务端绑定到当前请求，最多执行两次 Mem0 搜索。身份标识是本地 Demo 的命名空间，不是身份认证。工具结果不会再次作为新事实提交给 Mem0。

浏览器保留当前会话最近 20 条消息，新对话、刷新页面或切换身份会清除会话上下文，长期记忆仍保留。会话历史只接受 user/assistant 角色。

每条回答下方展示搜索调用次数和写入结果，并可展开查看搜索词、召回内容、实际 ADD/UPDATE/DELETE 事件及记录 ID。未调用、查询无结果、查询失败分别展示；写入为空显示“已尝试，无变更”，写入失败保留回答。非预期写入响应显示“无法确认”，不会将返回文本冒充成功保存。

API 保留 recalled_memories/saved_memories，并增加 memory_searches 和 memory_write。保存状态来自 Mem0 已完成操作返回的事件，不根据模型的“我记住了”判断。

Mem0 提取使用 DeepSeek，embedding 使用 OpenAI text-embedding-3-small（1536 维）。镜像使用 uv.lock 构建，并包含 tests。

## 验证

```sh
docker compose exec app uv run --no-sync python -m unittest discover -s tests -v
docker compose exec app uv run --group dev ruff check src tests
# 真实 API 测试，消耗模型调用，使用独立测试身份并在成功后清理其记忆：
python tests/live_smoke.py
```

真实 API 测试要求 localhost:8000 的服务运行。可以手动先介绍姓名与偏好，再点“新对话”询问历史，观察搜索调用和真实保存事件。普通知识问题通常无需搜索；工具选择由模型决定，存在行为波动。

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
