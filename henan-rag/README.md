# henan-rag

一个自己搭建的 RAG 学习实验室：管理多个知识库，把文档上传到指定库，聊天时选择一个或多个库，观察解析、切块、向量检索和 Agent 流式回答的全过程。

这是本机运行的学习原型。知识库 ID 用于组织资料；当前没有登录或权限系统。请使用公开或合成资料进行实验。

## 技术栈与存储

- Python 3.12、uv、FastAPI，原生 HTML/JavaScript 页面。
- SQLite：知识库名称、描述、文档归属、处理状态与分页列表。
- Docling：PDF、Office、HTML 等文档解析，导出 Markdown。
- 自写段落切块：合并短段，长段按字符窗口切分并保留 overlap。
- OpenAI Embeddings：文档片段与问题使用同一向量空间。
- Qdrant：单集合保存向量，通过 knowledge_base_id 和 indexing_state 过滤。
- OpenAI Agents SDK：真实调用 search_knowledge，逐步生成回答与编号引用。
- Docker Compose：Web API 和 Qdrant；元数据及原文件挂载在 data 目录。

Embeddings 使用 OPENAI_API_KEY 与 OPENAI_BASE_URL；聊天使用 CHAT_API_KEY、CHAT_BASE_URL、CHAT_MODEL。聊天密钥为空时回退到 OPENAI_API_KEY。Agents SDK tracing 默认关闭。

## 启动

```sh
cd henan-rag
make setup
# 编辑 .env，填写 Embeddings 与聊天服务的配置
make up
```

Windows 没有 make 时也可以复制 .env.example 为 .env，填写配置后运行：

```sh
docker compose up --build -d
```

- [RAG 网页](http://localhost:8002/)
- [Qdrant Dashboard](http://localhost:6335/dashboard#/collections)
- [API 文档](http://localhost:8002/docs)

首次启动会创建默认知识库。知识库名称可修改，ID 保持不变；默认库保留为基础入口，可以删除其文档。

## 知识库管理与聊天

页面使用同一个 URL，通过“聊天”和“知识库管理”两个 Tab 切换，默认显示聊天。切换 Tab 保留当前问题、回答、上传状态和选择。

1. 在“知识库管理” Tab 创建“产品资料”“部署资料”等知识库，并填写可选描述。
2. 选择一个库，将文件上传到这个库；查看文档状态、chunk 数量与删除操作。入库完成后点击文件旁的“查看分块”，浏览每块正文和存储详情。
3. 在“聊天” Tab 选择一个或多个知识库，不设固定数量上限。管理区域的当前库与聊天选择独立。
4. 点击“只看召回”检查证据，或点击“检索 + Agent 回答”观察流式文字与工具调用。
5. 回复下方的工具记录可展开查看挂载范围、各库命中数量、检索问题和耗时。
6. 展开“召回证据”，查看知识库名称、文件来源、chunk 序号、分数和原文。
7. 生成期间可以停止；已有回复保留。删除库会同时清理该库的文档与向量，需要确认。

知识库总数和单次聊天挂载数均不设固定数量上限。知识库多选表示本次检索范围，不复制资料。所选库的证据统一按相关度排序，最多返回 top_k 条；不保证每个库都有命中。每轮问题独立，不保存聊天历史或传入前文。

支持上传 PDF、DOCX、PPTX、XLSX、HTML、Markdown、TXT、CSV，默认单文件上限 20 MB。上传仍在请求内完成；同一知识库同时进行另一个上传或删除会返回忙状态。

### 在网页查看文档分块

“查看分块”打开管理页内的文档详情：按原文顺序列出块目录，选择某一块后查看完整文本、字符数与序号。每页加载 12 个块，可前后翻页；不会一次拉取整份大型文档的全部向量。

展开“存储详情”可查看实际 point ID、知识库与文档 ID、索引状态、payload，以及向量的维度和前 8 个数值。正文和元数据直接读取 Qdrant 已保存的记录；查看时不重新解析、切块或调用模型。网页中的第 1 块对应内部 `chunk_index=0`。

文档内容以纯文本显示，Markdown、HTML 和脚本都不会执行。尚未完成入库的文档只显示处理状态；切换知识库或删除文档后，旧详情会失效。

## 两个知识库的实验

- 建“产品资料”，上传 [examples/product-notes.md](examples/product-notes.md)。
- 建“部署资料”，上传 [examples/deployment-notes.md](examples/deployment-notes.md)。
- 分别单选两个库，观察只返回所选库的证据。
- 同时选两个库，问“青禾平台的产品版本和部署最低内存分别是多少？”。
- 应能从产品库找到 3.2，从部署库找到 8 GB；回复引用对应证据。
- 重命名知识库后再问，观察 ID、文档和向量不变，新的引用名称更新。

[examples/rag-demo.md](examples/rag-demo.md) 仍可用于业务资料检索实验。示例资料均为虚构内容。

## 数据流

```text
上传到指定知识库
  → SQLite importing → 原文件 → Docling → Markdown → 段落切块
  → Embeddings → Qdrant staging → 全批次完成后 ready → SQLite ready

选择知识库 + 问题
  → Agent 调用 search_knowledge
  → 问题 Embedding → Qdrant MatchAny(所选库) + ready
  → 核对文档元数据 → 全局 top-k
  → 带知识库和文档来源的证据 → 流式回答 + 引用
```

查询检查库和文档状态，未完成或清理失败的文档不进入回答。上传/删除中断后，启动时尝试恢复；失败记录保留错误状态，可以继续清理。

## API

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| GET / POST | /api/knowledge-bases | 分页列表 / 创建 |
| GET / PATCH / DELETE | /api/knowledge-bases/{id} | 详情 / 修改名称描述 / 删除 |
| GET / POST | /api/knowledge-bases/{id}/documents | 文档分页 / multipart 上传 file |
| GET | /api/knowledge-bases/{id}/documents/{document_id}/chunks | 分页查看已存储分块、payload 与向量预览 |
| DELETE | /api/knowledge-bases/{id}/documents/{document_id} | 删除本库文档 |
| POST | /api/retrieve | 返回召回证据数组 |
| POST | /api/chat | 返回完整 JSON 回答 |
| POST | /api/chat/stream | SSE 流式回答 |

列表支持 limit（默认 50，最大 200）和 offset。查询请求：

```json
{
  "question": "产品版本和部署内存分别是多少？",
  "knowledge_base_ids": ["实际知识库 UUID", "另一个实际知识库 UUID"],
  "top_k": 5
}
```

ID 列表必填、不得为空或重复，不限制知识库数量，必须是已有的可用知识库；top_k 范围为 1 至 12，是多个库合计的证据条数，不是可选知识库数量。模型只能提供检索问题，服务端绑定知识库范围。

SSE 事件为 status、tool_start、tool_end、citations、text_delta、done、error。tool_end 包含各所选库的命中数量；done 与非流式接口包含 answer、citations、knowledge_bases。HTTP 错误 detail 为包含 code、message、retryable 的对象。

分块浏览接口的 `limit` 默认 12、最大 100；`offset` 是起始 `chunk_index`，从 0 开始。下一页使用响应中的 `next_offset`，为 null 表示末页。`total` 是符合归属及 ready 状态的实际向量记录数，`document.chunk_count` 是入库时的元数据计数。接口读取知识库和文档的明确范围，不依赖模型密钥。

## 新存储与持久化

本版本从新的多知识库存储开始，不迁移旧 tenant_id 数据。

- Qdrant 集合：henan_rag_knowledge_chunks。
- SQLite：data/metadata.sqlite3。
- 原文件：data/knowledge-uploads/。

旧 henan_rag_chunks 集合和 data/uploads 文件不进入新界面或问答。重启服务不会清空新知识库；data 下运行数据与密钥不会提交到 Git。

本地 .env 如果仍写着旧 QDRANT_COLLECTION，请改为新集合，并填写 METADATA_DB_PATH、UPLOAD_DIR。备份与恢复时保持 Qdrant 集合、SQLite 元数据和原文件对应；不要仅恢复其中一部分。

## 环境变量

| 变量 | 用途 | 默认值 |
| --- | --- | --- |
| OPENAI_API_KEY | Embeddings 凭证 | 必填 |
| OPENAI_BASE_URL | Embeddings 服务地址 | https://api.openai.com/v1 |
| CHAT_API_KEY | 聊天凭证；为空时回退 Embeddings 凭证 | 可选 |
| CHAT_BASE_URL | 聊天服务地址 | https://api.deepseek.com |
| CHAT_API_MODE | responses 或 chat_completions | responses |
| CHAT_MODEL | 对话模型 | deepseek-v4-flash |
| EMBEDDING_MODEL | 文档和问题的向量模型 | text-embedding-3-small |
| EMBEDDING_DIMENSIONS | 必须与集合维度一致 | 1536 |
| QDRANT_URL | Compose 网络内向量库地址 | http://qdrant:6333 |
| QDRANT_COLLECTION | 新知识库向量集合 | henan_rag_knowledge_chunks |
| METADATA_DB_PATH | 容器内 SQLite 路径 | /app/data/metadata.sqlite3 |
| UPLOAD_DIR | 容器内上传文件目录 | /app/data/knowledge-uploads |
| APP_PORT | 本机网页端口 | 8002 |
| MAX_UPLOAD_MB | 单文件上传上限 | 20 |

第一版运行一个 app 实例和一个 Uvicorn worker，写操作协调使用进程内状态。扩展成多进程/多节点前需要替换此机制。

## 验证

```sh
make lint
make test
node --test tests/test_frontend.cjs
```

真实端到端验收会调用已配置的模型与 Embeddings，并创建合成知识库。默认结束后清理脚本创建的库，加 --keep 可留下两个示例库供手动体验：

```sh
docker compose exec app uv run --no-sync python tests/live_knowledge_base_smoke.py --base-url http://127.0.0.1:8000 --keep
```

脚本检查创建、上传、文档列表、重命名、单库与多库召回、同时挂载 10 个知识库、零命中库记录、无效选择、跨库删除防护、默认库保护、真实工具事件及流式回答。

文档分块浏览的真实验收会上传多章节合成文档，逐页检查块顺序、全部正文、归属、向量预览与 Qdrant 原始记录一致。默认清理本脚本创建的库，`--keep` 保留示例供网页浏览：

```sh
docker compose exec app uv run --no-sync python tests/live_document_inspection_smoke.py --keep
```

详细数据结构、接口和验收见 [多知识库技术方案](docs/multi-knowledge-base-design.md)。
