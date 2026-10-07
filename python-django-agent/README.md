# Python Django + OpenAI Agents SDK 学习项目

这是一个可以单独运行的后端学习项目，包含 Django 聊天 API、原生网页界面、OpenAI Agents SDK、PostgreSQL 和 MinIO 私有对象存储。Docker Compose 会一起启动三个服务；`uv` 负责 Python 依赖管理。

## 快速启动

需要 Docker Desktop（含 Docker Compose）和一个 OpenAI API key。

1. 复制环境变量模板：

   ```powershell
   Copy-Item .env.example .env
   ```

2. 编辑 `.env`，填入 `OPENAI_API_KEY` 和服务支持的 `OPENAI_MODEL`。如需使用兼容服务，再填写 `OPENAI_BASE_URL`。
3. 在本目录运行：

   ```sh
   docker compose up --build
   ```

   如果安装了 GNU Make，也可以运行 `make up`。
4. 打开 <http://localhost:8000> 开始聊天。Django 管理后台在 <http://localhost:8000/admin/>。

第一次启动时，Django 容器会等待 PostgreSQL 健康检查通过，自动应用数据库迁移，再执行 `init_object_storage` 等待 MinIO 可用并创建私有 bucket，最后启动 Uvicorn ASGI 服务器。停止服务按 `Ctrl+C`，然后运行 `docker compose down`；数据库和附件分别保存在 `postgres_data`、`minio_data` Docker volume 中。

## PDF 和图片附件

在聊天输入框上方点击“上传 PDF / 图片”。上传成功后，文件存入 MinIO，PostgreSQL 保存文件信息和 PDF 提取的文字。勾选附件后发送问题，助手会结合选中的资料回答；刷新后恢复当前会话的附件，点击文件名下载原文件。取消勾选可以停止向模型发送该附件，新对话使用独立的附件列表。

- 支持文字型 PDF、PNG、JPEG、WebP；单文件最多 10 MB，每次提问最多 4 个附件。
- PDF 最多 50 页、60000 字符，提取结果包含页码；加密 PDF、无文字的扫描 PDF 暂不支持。PDF 中的插图暂不传给模型，可单独上传截图。
- 图片最多 2000 万像素，使用内联 base64 输入；`OPENAI_MODEL` 必须支持图片和所使用的模型 API。仅支持文本的兼容模型不能分析图片。
- 上传只保存到本地；勾选文件并提问时，PDF 文字或图片内容才会发送给配置的模型服务。
- Bucket 默认私有，文件通过 Django 下载。项目仍没有用户权限隔离，知道附件 UUID 即可访问下载接口，请只用于可信的本地环境。

MinIO 固定到 `RELEASE.2025-09-07T16-13-09Z`，由 `minio/Dockerfile` 从固定发行源码构建，生成本地镜像 `django-agent-minio:RELEASE.2025-09-07T16-13-09Z`，不依赖官方旧镜像。首次构建需要网络并下载 Go 依赖，可能较慢。社区源码许可为 AGPLv3，参见 [固定发行源码](https://github.com/minio/minio/tree/RELEASE.2025-09-07T16-13-09Z)。Go 和 Debian 基础镜像使用 ECR 公共镜像源，并固定版本标签及 SHA256 摘要。需要长期保存完全相同的成品镜像时，可执行：

```sh
docker image save -o minio-local.tar django-agent-minio:RELEASE.2025-09-07T16-13-09Z
# 在另一台机器恢复
docker image load -i minio-local.tar
```

MinIO API 为 `http://localhost:9000`，浏览界面为 `http://localhost:9001`。默认本地账号 `django-agent`、密码 `django-agent-local-password`，可以在 `.env` 中设置 `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD`。上传时自动创建 `chat-attachments` 私有 bucket。Compose 内部 Django 使用 `http://minio:9000`，本机 Django 使用 `.env` 中的 `S3_ENDPOINT_URL=http://localhost:9000`。

> `.env` 里的 Django 密钥和数据库密码仅供本地学习使用。部署到公网前请换成安全配置，并使用正式的 WSGI/ASGI 服务器。
>
> 练习版没有用户登录和对话权限控制，请仅在本机或可信的开发环境中运行。

## 常用命令

| 命令 | 作用 |
| --- | --- |
| `make up` | 构建并启动 Django、PostgreSQL 和 MinIO |
| `make down` | 停止容器并保留数据库数据 |
| `make logs` | 查看 Django、PostgreSQL 与 MinIO 日志 |
| `make migrate` | 应用数据库迁移 |
| `make makemigrations` | 根据模型变更生成迁移 |
| `make shell` | 打开 Django shell |
| `make createsuperuser` | 创建 Django 管理员 |
| `make db` | 打开 PostgreSQL 命令行 |

如果 Windows 环境没有 `make`，可以使用对应的 `docker compose` 命令，例如：

```sh
docker compose exec web uv run --no-sync python manage.py migrate
docker compose exec web uv run --no-sync python manage.py shell
```

## 不用 Docker 运行 Django

你仍可以用本机 Python 和 `uv` 启动 Django；PostgreSQL 可以继续由 Docker 提供：

```sh
docker compose up -d --build db minio
uv sync
uv run python manage.py migrate
uv run uvicorn config.asgi:application --host 127.0.0.1 --port 8000
```

此时 `.env` 中的 `DB_HOST=localhost` 会让本机 Django 连接 Compose 暴露的数据库端口。想用 SQLite 快速试 Django ORM 时，将 `DB_ENGINE=sqlite`，然后运行迁移即可。

仓库包含 `uv.lock`；Docker 使用 `uv sync --frozen` 安装锁定的依赖。本地可运行 `uv sync --locked`。

## 验证

默认在 Docker 内使用 PostgreSQL 测试数据库运行测试：

```sh
docker compose exec -T web uv run --no-sync python manage.py test chat --noinput
docker compose exec -T web uv run --no-sync python manage.py check
docker compose exec -T web uv run --no-sync python manage.py makemigrations --check --dry-run
```

真实 HTTP / MinIO 联动验证会创建专用测试会话，检查 PDF 和图片上传、私有 bucket、下载内容以及重启持久化，最后只清理它创建的数据：

```sh
docker compose exec -T web uv run --no-sync python manage.py smoke_attachments seed
# 可选：实际调用模型，会产生少量费用；需要模型支持图片
docker compose exec -T web uv run --no-sync python manage.py smoke_attachments model
docker compose restart db minio web
# 等待 web 启动完成，再运行
docker compose exec -T web uv run --no-sync python manage.py smoke_attachments verify
docker compose exec -T web uv run --no-sync python manage.py smoke_attachments cleanup
```

`seed` / `verify` / `cleanup` 期间不要重建或删除 web 容器，测试状态文件暂存在该容器 `/tmp`，普通 restart 会保留它。

以下测试使用 SQLite 测试数据库，并模拟 S3 和模型服务，不需要 API key 或运行 MinIO：

```powershell
$env:DB_ENGINE = "sqlite"
uv run --no-sync python manage.py test chat
uv run --no-sync python manage.py check
uv run --no-sync python manage.py makemigrations --check --dry-run
```

Docker 启动后的人工验收：上传文字 PDF 并针对内容提问；上传图片并要求解释；刷新页面检查附件和消息恢复；点击文件名下载；`docker compose down` 后重新启动，检查资料仍可用。

## 项目结构

```text
python-django-agent/
├── chat/                  # Django 聊天 app：模型、视图、API、Agent 服务
├── config/                # Django 项目配置、URL、ASGI/WSGI 入口
├── static/chat/           # 聊天页 CSS 和 JavaScript
├── templates/chat/        # Django 模板
├── Dockerfile
├── minio/                 # 从固定发行源码构建 MinIO 镜像
├── compose.yaml           # web + PostgreSQL + MinIO 服务
├── Makefile
├── pyproject.toml         # uv 项目及依赖定义
└── manage.py
```

## 关键代码怎么串起来

1. `chat/models.py` 定义 `Conversation`、`Message` 和 `Attachment`；Django migration 创建 PostgreSQL 表。
2. 页面通过 `POST /api/attachments/` 上传文件，再向 `POST /api/chat/stream/` 发送问题和选中的附件 ID。
3. `chat/views.py` 校验输入和附件所属会话，加载历史消息；`chat/attachments.py` 组装文字、PDF 参考文本和图片输入。
4. `chat/services.py` 创建 Agent，用 `Runner.run_streamed()` 执行，并通过 SSE 向页面逐段发送回复。`POST /api/chat/` 则保留同步调用 `Runner.run_sync()` 的方式。
5. 完整回复生成后，Django 把用户消息和助手回复保存到数据库；页面会用会话 ID 恢复聊天记录及附件列表。

## 附件从上传到聊天的完整流程

上传与提问是两个独立阶段：上传时检查、处理并保存文件；发送消息时才把选中的附件内容交给 Agent。上传动作本身不会调用模型。

### 1. 前端选择文件并立即上传

`templates/chat/index.html` 提供上传按钮、文件选择框和附件列表，`static/chat/app.css` 负责样式，`static/chat/app.js` 处理交互。

用户选中文件后，前端先检查 10 MB 大小限制，用 `FormData` 携带文件及已有的 `conversation_id`，附上 CSRF token，立即请求 `POST /api/attachments/`，不需要先点击发送消息。上传期间禁用发送、新对话和继续上传，避免会话状态发生冲突。

### 2. 后端先验证和处理，再保存

`chat/views.py` 的 `upload_attachment()` 接收请求，调用 `chat/attachments.py` 的 `validate_file()`：

| 文件类型 | 上传时的处理 | 此时是否调用模型 |
| --- | --- | --- |
| PDF | 检查文件头、加密状态、页数；用 `pypdf` 逐页提取文字，加上 `[第 N 页]` 标记，检查文本长度 | 否 |
| PNG / JPEG / WebP | 用 Pillow 检查实际图片格式、文件有效性和像素数量 | 否 |

`pypdf` 读取 PDF 已有的文字数据，不是 OCR 或本地 AI 模型。项目没有独立的 OCR 流程；没有可提取文字的扫描 PDF 会被拒绝。即使 PDF 有文字，其插图和原始页面布局也不会在当前流程中发送给模型；图片理解由后续调用的视觉模型完成。

验证通过后，后端生成 UUID 对象路径，例如 `conversations/<会话 UUID>/<附件 UUID>.pdf`，避免同名文件覆盖。若尚未有会话，先准备一个新会话，然后保存：

| 存储位置 | 保存的内容 |
| --- | --- |
| MinIO 私有 bucket | 原始 PDF 或图片文件 |
| PostgreSQL `Attachment` | 文件名称、类型、大小、bucket、对象路径、所属会话、创建时间、PDF 提取文本 |

顺序是先写入 MinIO，再用数据库事务保存会话和附件记录。如果数据库保存失败，后端会尝试删除刚写入 MinIO 的对象；清理本身也可能失败，需要查看日志。附件只有在数据库保存成功后才会作为上传成功结果返回。纯上传操作不会创建聊天消息。

### 3. 前端展示和选择附件

上传成功后，后端返回会话 ID 和附件信息。前端把会话 ID 存入 `localStorage`，显示附件名称、勾选框及下载链接。新附件默认勾选，但每次最多选 4 个。取消勾选只表示本轮不发送该附件，不会删除文件；新对话会清空当前页面的附件列表，也不会删除旧会话数据。

### 4. 发消息时准备 Agent 输入

前端默认请求 `POST /api/chat/stream/`，JSON 包含：

```json
{
  "message": "解释文档中的例子",
  "conversation_id": "当前会话 UUID",
  "attachment_ids": ["本轮选中的附件 UUID"]
}
```

后端加载历史消息，校验附件 ID 格式、数量以及是否属于当前会话，再通过 `build_input()` 组装模型输入：

- PDF：读取 PostgreSQL 中已提取的文字和页码。每次提问不重新解析 PDF，也不从 MinIO 下载原 PDF；发送给模型的是参考文本，而非 PDF 文件。
- 图片：从 MinIO 读取原图，转换为 base64 data URL，构造 `input_image` 输入。远端模型不需要访问本地 MinIO 地址。
- 历史记录：已有用户消息和助手回复排在前面，本次问题及选中的资料放在最后一条用户输入中。

同步聊天接口使用相同的附件校验和输入组装方式。流式接口通过 `sync_to_async` 调用同步的输入准备逻辑，避免在异步迭代器里直接执行同步存储读取。

### 5. 流式回答完成后保存聊天消息

Agent 使用 `Runner.run_streamed()` 生成内容。后端通过 SSE 的 `delta` 事件逐段发送文字，前端追加到助手消息气泡中。

收到 Agent 的完整最终回复后，后端才使用数据库事务成对保存本轮用户消息、助手回复，并更新会话时间；保存成功后发送 `done` 事件。生成或保存中途失败时发送 `error` 事件，前端移除本轮气泡、恢复输入，方便重试。此前上传成功的附件仍然保留。

### 6. 刷新恢复和下载原文件

前端从 `localStorage` 获取会话 ID，通过 `GET /api/conversations/<会话 UUID>/messages/` 获取历史消息和附件列表。当前实现恢复后默认勾选列表前 4 个附件，不记忆刷新前的勾选状态。

点击附件名称会请求 `GET /api/attachments/<附件 UUID>/download/`。Django 从 MinIO 读取对象，通过下载响应返回原文件；不会向浏览器暴露存储凭据或开放 bucket。

附件目前关联整个会话，没有逐条记录某条消息使用了哪些附件。历史消息只保存文字问题和回复，不保存当轮完整的 PDF / 图片模型输入；每轮由当前勾选决定重新发送哪些资料。取消勾选后，模型可能仍从此前的助手回复了解部分信息，但不会再次收到该附件原始内容。

### 代码职责索引

| 文件 | 职责 |
| --- | --- |
| `templates/chat/index.html`、`static/chat/app.css` | 上传入口、附件列表及样式 |
| `static/chat/app.js` | 上传、附件选择、发送问题、解析 SSE、恢复会话 |
| `chat/models.py`、`chat/migrations/0002_attachment.py` | 附件模型和数据库表 |
| `chat/attachments.py` | 文件验证、PDF 提取、S3 客户端、模型输入组装 |
| `chat/views.py`、`chat/urls.py` | 上传、下载、消息查询、同步及流式聊天接口 |
| `chat/services.py` | Agent 指令、工具与模型执行 |
| `chat/admin.py` | 管理后台查看附件记录 |
| `config/settings.py`、`.env.example` | 存储连接配置及上传限制 |
| `minio/Dockerfile`、`compose.yaml` | 固定版本 MinIO 镜像、三个服务及持久化数据卷 |
| `chat/management/commands/init_object_storage.py` | 启动前等待存储并创建 bucket |
| `chat/tests.py`、`chat/management/commands/smoke_attachments.py` | 自动化测试及 Docker 真实联动验证 |

配置中的 `S3` 表示兼容 S3 的接口，我们实际连接本地 MinIO。`boto3` 负责对象存储操作，`pypdf` 负责 PDF 文字提取，Pillow 负责图片验证；Python 依赖由 `uv.lock` 固定。

可以从这些实验开始：修改 `Agent.instructions`、给 Agent 增加工具、在 `Message` 添加字段、生成并应用 migration，或在 `chat/urls.py` 新增 API 路由。

## 官方文档

- [OpenAI Agents SDK Quickstart](https://openai.github.io/openai-agents-python/quickstart/)：Agent、Runner、工具和 API key。
- [OpenAI Agents SDK Running agents](https://openai.github.io/openai-agents-python/running_agents/)：运行方式和多轮对话管理。
- [Django 文档](https://docs.djangoproject.com/en/5.2/)
- [uv 文档](https://docs.astral.sh/uv/)
