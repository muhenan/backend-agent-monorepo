# henan-rag

一个自己搭建的最小 RAG 实验室，用来直接观察解析、切块、向量检索和 Agent 生成如何连起来。核心流程由本项目代码编排。

这是学习原型，不是生产或医疗产品。没有登录认证、完整权限控制、临床评估或服务等级保障。**不要上传真实患者资料或其他敏感数据。**页面里的 tenant id 只是 Qdrant 查询过滤条件，不是安全边界。

## 技术栈

- Python 3.12、uv、FastAPI
- Docling：PDF、Office、HTML 等文档解析，导出结构化 Markdown
- 自写 Markdown 段落切块：小段尽量合并，长段按字符窗口切分并保留 overlap
- OpenAI Embeddings API：把 chunk 和问题映射到同一向量空间
- Qdrant：存储 chunk、来源、文档 ID、tenant id 和向量，按 tenant 过滤后做 top-k dense retrieval
- OpenAI Agents SDK：通过 DeepSeek 的 OpenAI 兼容接口接收显式检索证据，生成带 `[证据 N]` 标记的回答
- Docker Compose：运行 Web API 与 Qdrant，数据保存在 Qdrant volume 和 `data/uploads/`

Embeddings 使用 `OPENAI_API_KEY` 和 `OPENAI_BASE_URL`，Agent 对话使用 `CHAT_API_KEY`、`CHAT_BASE_URL`、`CHAT_MODEL`。默认值与 `agent-mem` 保持一致：OpenAI 提供 Embeddings，DeepSeek 提供对话模型。Agents SDK tracing 默认关闭，避免学习用的输入和输出被发送到追踪服务。

## 快速开始

需要 Docker Desktop/Engine、Docker Compose 和 `uv`。第一次构建会安装 Docling 及文档解析依赖，镜像可能较大。

```sh
cd henan-rag
make setup
```

编辑本地 `.env`，检查 `OPENAI_API_KEY` 和 `CHAT_API_KEY`。新项目已从同仓库 `agent-mem/.env` 复制这两个 key 到本机忽略文件；如果需要换 key 或服务，在本地 `.env` 修改。然后启动：

```sh
make up
```

打开 <http://localhost:8002>，上传 [`examples/rag-demo.md`](examples/rag-demo.md)。页面会分别显示召回的 chunk、相似度分数和 Agent 的最终回答。

## 一次查询的数据流

```text
文档上传
   └─ Docling → Markdown → 段落感知切块 → OpenAI Embeddings → Qdrant points

问题
   ├─ OpenAI Embeddings → Qdrant tenant filter + top-k dense search
   └─ 可见召回片段 → OpenAI Agents SDK Agent → 回答 + [证据 N] 引用
```

Web 界面提供两个操作：

- **只看召回**：只跑 embedding 和 Qdrant，展示命中文本、来源、chunk 序号、score，不调用 Chat 模型。
- **检索 + Agent 回答**：使用相同 top-k 检索结果作为 Agent 输入，再并排展示答案与引用证据。

这让你可以分辨问题出在召回还是生成：如果正确段落没有出现在“召回证据”里，先调查文档解析、切块、embedding 和 top-k；若证据正确但答案错误，再调查提示词、模型行为或引用约束。

## 建议的体验步骤

1. 在 UI 上传示例 Markdown，等待入库成功，注意 chunk 数量。
2. 问“青禾平台的线下窗口几点开放？”，先点“只看召回”，检查召回片段是否包含 09:00–17:00。
3. 再点“检索 + Agent 回答”，确认回答引用 `[证据 1]` 等编号，并核对展示的证据原文。
4. 对照问“如何修改预约？”和“资料最后更新时间是什么？”，看看不同表达能否召回同一段。
5. 问“青禾平台推荐什么药物剂量？”，观察 Agent 是否承认资料没有覆盖。
6. 修改示例文档中的说法或增加一段内容，再次入库后比较召回变化。删除旧文档可在文档列表点击“删除”。

示例资料是虚构内容，不要把这些回答当作医疗信息。

## 环境变量

| 变量 | 用途 | 默认值 |
| --- | --- | --- |
| `OPENAI_API_KEY` | Embedding API 凭证 | 必填 |
| `OPENAI_BASE_URL` | OpenAI 兼容 API 基址 | `https://api.openai.com/v1` |
| `OPENAI_API_MODE` | 兼容保留项；Embedding API 不使用此设置 | `responses` |
| `CHAT_API_KEY` | Agents SDK 对话凭证；为空时回退到 `OPENAI_API_KEY` | 可选 |
| `CHAT_BASE_URL` | 对话 API 基址 | `https://api.deepseek.com` |
| `CHAT_API_MODE` | Agents SDK API surface：`responses` 或 `chat_completions` | `responses` |
| `CHAT_MODEL` | Agents SDK Agent 使用的模型 | `deepseek-v4-flash` |
| `EMBEDDING_MODEL` | 文档和问题共用的 Embedding 模型 | `text-embedding-3-small` |
| `EMBEDDING_DIMENSIONS` | Qdrant collection 向量维度，必须匹配模型 | `1536` |
| `QDRANT_URL` | Compose 网络中的 Qdrant URL | `http://qdrant:6333` |
| `QDRANT_COLLECTION` | Collection 名称 | `henan_rag_chunks` |
| `APP_PORT` | 本机 Web UI 端口 | `8002` |
| `MAX_UPLOAD_MB` | 单文件上传上限 | `20` |

## 常用命令

```sh
make setup    # 创建 .env（如不存在）并更新 uv.lock
make up       # 构建并启动应用与 Qdrant
make restart  # 重建并启动
make logs     # 跟踪应用和 Qdrant 日志
make shell    # 进入应用容器
make down     # 停止服务（保留 Qdrant volume）
make lock     # 更新 uv.lock
make lint     # Ruff 静态检查
make format   # Ruff 格式化
make test     # pytest
```

## 目录结构

```text
henan-rag/
├── src/henan_rag/
│   ├── app.py          # FastAPI 生命周期与 API
│   ├── config.py       # 环境配置
│   ├── chunking.py     # Markdown 段落切块
│   ├── rag_service.py  # Docling、embedding、Qdrant 和 Agent 编排
│   └── static/         # 可观察入库、召回和回答的 Web UI
├── examples/           # 虚构的学习资料
├── tests/              # 切块单元测试
├── data/uploads/       # 原始上传文件，本地持久化且不提交
├── pyproject.toml
├── uv.lock
├── Dockerfile
├── compose.yaml
├── Makefile
└── .env.example
```
