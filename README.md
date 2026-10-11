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

一个用于学习短期会话上下文与 Mem0 长期记忆的聊天 Demo。Agent 根据问题和当前会话决定是否调用 `search_memory` 搜索相关记忆；回复完成后，Mem0 从本轮用户消息和最终回复中提取、保存或更新值得记住的信息。

可以体验和观察：

- 流式回答，以及回复下方可展开的记忆搜索活动、搜索词和结果。
- 回答完成后的记忆写入进度，以及实际新增、更新、删除事件和失败状态。
- 当前身份的记忆列表、记录 ID、单条删除和全部清空。
- 切换自定义身份标识，观察不同记忆空间；开始新对话后仍可检索长期记忆。

身份标识用于划分记忆空间，当前没有登录认证。会话上下文只保留在浏览器内存中，新对话、刷新页面或切换身份时清除；长期记忆及向量由 Mem0 和 Qdrant 持久化。

- 目录：[agent-mem/](./agent-mem/)
- 详细说明：[agent-mem/README.md](./agent-mem/README.md)
- 技术栈：FastAPI、OpenAI Agents SDK、Mem0、Qdrant、DeepSeek、OpenAI Embeddings、Docker Compose
- 启动：在 `agent-mem/` 下复制 `.env.example` 为 `.env`，配置 DeepSeek 和 OpenAI 密钥，运行 `docker compose up --build -d`（或 `make up`）。
- 默认本地地址：[聊天界面](http://localhost:8000/)；[Qdrant Dashboard](http://localhost:6333/dashboard)；[API 文档](http://localhost:8000/docs)。

## Henan RAG

一个可观察完整 RAG 流程的学习实验室：文档经过 Docling 解析、段落切块和向量化后保存到 Qdrant；聊天时由 Agent 调用 `search_knowledge` 检索所选知识库，依据召回片段流式回答并引用来源。

可以体验和观察：

- 同一个 URL 下的“聊天”和“知识库管理” Tab，切换时保留输入、回答和管理状态。
- 知识库创建、重命名、描述修改与删除；向指定库上传文档，查看入库状态并删除文档。
- 单库或多库挂载，不设知识库总数和单次挂载数的固定上限；管理目标与聊天范围独立。
- 分页浏览每份文档的分块目录、完整文本、字符数、实际存储元数据和向量预览。
- 单独查看召回证据，或观察 Agent 的工具调用、各库命中数、流式回答及知识库和文档引用；生成期间可以停止。

SQLite 保存知识库和文档元数据，Qdrant 保存分块正文与向量，原文件保存在本地 `data/` 目录。当前是单机学习原型，聊天按独立问题执行，不保存会话历史。

- 目录：[henan-rag/](./henan-rag/)
- 详细说明：[henan-rag/README.md](./henan-rag/README.md)
- 技术方案：[多知识库管理与分块浏览](./henan-rag/docs/multi-knowledge-base-design.md)。
- 技术栈：FastAPI、SQLite、Docling、OpenAI Embeddings、Qdrant、OpenAI Agents SDK、Docker Compose。
- 启动：在 `henan-rag/` 下复制 `.env.example` 为 `.env`，配置 Embeddings 和聊天模型服务，运行 `docker compose up --build -d`（或 `make up`）。
- 默认本地地址：[RAG 学习界面](http://localhost:8002/)；[Qdrant Dashboard](http://localhost:6335/dashboard#/collections)；[API 文档](http://localhost:8002/docs)。

## 项目架构可视化

用静态网页图解仓库中的项目架构。目前提供 Django Agent 的架构图，不需要安装依赖或启动服务。

- 目录：[visualize_learn/](./visualize_learn/)
- 详细说明：[visualize_learn/README.md](./visualize_learn/README.md)
- 查看：[Django Agent 架构图](./visualize_learn/python-django-learn/index.html)
- 技术：HTML、CSS、SVG
