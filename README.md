# Backend Agent Monorepo

这个仓库收录用于学习和试验后端开发与 AI Agent 的项目。各项目按自己的目录维护说明、依赖和运行方式；需要了解或运行某个项目时，请看对应的小节和项目 README。

## Python Django Agent

一个基于 Django 和 OpenAI Agents SDK 的聊天后端 Demo。包含流式聊天、会话历史，以及 PDF 和图片附件问答；PostgreSQL 保存会话数据，MinIO 保存附件。

- 目录：[python-django-agent/](./python-django-agent/)
- 详细说明：[python-django-agent/README.md](./python-django-agent/README.md)
- 技术栈：Django、OpenAI Agents SDK、PostgreSQL、MinIO、Docker Compose
- 启动：在 `python-django-agent/` 下配置 `.env` 后运行 `docker compose up --build`
- 本地地址：聊天界面 <http://localhost:8000>；MinIO 控制台 <http://localhost:9001>

## Agent Mem

一个用于学习 Mem0 长期记忆的 Web Chatbot Demo。应用先检索当前身份的相关记忆，将记忆交给 Agent 生成回答，再把本轮对话交给 Mem0 提取并保存记忆。

- 目录：[agent-mem/](./agent-mem/)
- 详细说明：[agent-mem/README.md](./agent-mem/README.md)
- 技术栈：FastAPI、OpenAI Agents SDK、Mem0、Qdrant、DeepSeek、Docker Compose
- 启动：在 `agent-mem/` 下配置 `.env` 后运行 `make up`
- 本地地址：聊天界面 <http://localhost:8000>；Qdrant Dashboard <http://localhost:6333/dashboard>

## 项目架构可视化

用静态网页图解仓库中的项目架构。目前提供 Django Agent 的架构图，不需要安装依赖或启动服务。

- 目录：[visualize_learn/](./visualize_learn/)
- 详细说明：[visualize_learn/README.md](./visualize_learn/README.md)
- 查看：[Django Agent 架构图](./visualize_learn/python-django-learn/index.html)
- 技术：HTML、CSS、SVG
