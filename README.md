# Backend Agent Monorepo

这是一个用于学习和试验 Python 后端与 AI Agent 的项目集合。每个子目录都是独立项目，依赖、配置和启动方式各自管理；仓库根目录没有统一的应用入口。

## 项目一览

| 项目 | 内容 | 主要技术 | 入口 |
| --- | --- | --- | --- |
| [Python Django Agent](./python-django-agent/README.md) | 带原生聊天界面的 Agent 后端，支持流式回复、会话历史，以及 PDF 和图片附件问答。 | Django、OpenAI Agents SDK、PostgreSQL、MinIO、Docker Compose | `python-django-agent/` |
| [Agent Mem](./agent-mem/README.md) | 展示跨会话长期记忆：检索用户记忆、生成回复，再从本轮对话中提取并保存新记忆。 | FastAPI、OpenAI Agents SDK、Mem0、Qdrant、DeepSeek、Docker Compose | `agent-mem/` |
| [项目架构可视化](./visualize_learn/README.md) | 用静态 HTML、CSS 和 SVG 图解仓库项目，当前包含 Django Agent 架构图。 | HTML、CSS、SVG | `visualize_learn/` |

## 快速开始

两个后端项目都需要 Docker Desktop（或 Docker Engine + Compose），并需要各自配置模型 API Key。请按项目 README 中的说明准备 `.env`，再从对应目录启动：

```sh
cd python-django-agent
docker compose up --build
```

或：

```sh
cd agent-mem
make up
```

架构图不需要安装依赖或启动服务，可直接打开 [Django Agent 架构图](./visualize_learn/python-django-learn/index.html)。每个项目的详细配置、功能说明和命令见其 README。

## 本地地址

- Python Django Agent：聊天界面 <http://localhost:8000>，MinIO 控制台 <http://localhost:9001>。
- Agent Mem：聊天界面 <http://localhost:8000>，Qdrant Dashboard <http://localhost:6333/dashboard>。

两个后端默认都使用宿主机 `8000` 端口，请不要同时以默认配置启动；如需并行运行，可在各自 `.env` 中修改 `WEB_PORT` 或 `APP_PORT`。Django 项目的 MinIO 和 PostgreSQL 数据，以及 Agent Mem 的 Qdrant 数据分别保存在各自的 Docker volumes 中。

## 目录结构

```text
backend-agent-monorepo/
├── agent-mem/          # Mem0 + Qdrant 长期记忆聊天 Demo
├── python-django-agent/ # Django Agent、聊天记录及文件附件 Demo
├── visualize_learn/    # 仓库项目架构图和相关说明
└── README.md           # 仓库项目索引
```
