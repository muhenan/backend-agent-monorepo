# Python Django + OpenAI Agents SDK 学习项目

这是一个可以单独运行的后端学习项目，包含 Django 聊天 API、原生网页界面、OpenAI Agents SDK 和 PostgreSQL。Docker Compose 会一起启动 Django 与数据库；`uv` 负责 Python 依赖管理。

## 快速启动

需要 Docker Desktop（含 Docker Compose）和一个 OpenAI API key。

1. 复制环境变量模板：

   ```powershell
   Copy-Item .env.example .env
   ```

2. 编辑 `.env`，填入 `OPENAI_API_KEY`。可以按需修改 `OPENAI_MODEL`。
3. 在本目录运行：

   ```sh
   docker compose up --build
   ```

   如果安装了 GNU Make，也可以运行 `make up`。
4. 打开 <http://localhost:8000> 开始聊天。Django 管理后台在 <http://localhost:8000/admin/>。

第一次启动时，Django 容器会等待 PostgreSQL 健康检查通过，再自动应用数据库迁移并启动开发服务器。停止服务按 `Ctrl+C`，然后运行 `docker compose down`；数据库数据保存在 Docker volume 中。

> `.env` 里的 Django 密钥和数据库密码仅供本地学习使用。部署到公网前请换成安全配置，并使用正式的 WSGI/ASGI 服务器。
>
> 练习版没有用户登录和对话权限控制，请仅在本机或可信的开发环境中运行。

## 常用命令

| 命令 | 作用 |
| --- | --- |
| `make up` | 构建并启动 Django 和 PostgreSQL |
| `make down` | 停止容器并保留数据库数据 |
| `make logs` | 查看 Django 与 PostgreSQL 日志 |
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
docker compose up -d db
uv sync
uv run python manage.py migrate
uv run python manage.py runserver
```

此时 `.env` 中的 `DB_HOST=localhost` 会让本机 Django 连接 Compose 暴露的数据库端口。想用 SQLite 快速试 Django ORM 时，将 `DB_ENGINE=sqlite`，然后运行迁移即可。

首次运行 `uv sync` 会根据 `pyproject.toml` 解析依赖并生成 `uv.lock`；之后可以把这个锁文件一并提交，固定项目依赖版本。

## 项目结构

```text
python-django-agent/
├── chat/                  # Django 聊天 app：模型、视图、API、Agent 服务
├── config/                # Django 项目配置、URL、ASGI/WSGI 入口
├── static/chat/           # 聊天页 CSS 和 JavaScript
├── templates/chat/        # Django 模板
├── Dockerfile
├── compose.yaml           # web + PostgreSQL 服务
├── Makefile
├── pyproject.toml         # uv 项目及依赖定义
└── manage.py
```

## 关键代码怎么串起来

1. `chat/models.py` 定义 `Conversation` 和 `Message`；Django migration 创建 PostgreSQL 表。
2. 页面向 `POST /api/chat/` 发送 JSON。`chat/views.py` 校验输入、加载历史消息，再调用服务层。
3. `chat/services.py` 定义 Agents SDK 的 `Agent` 和一个简单的 Django 学习工具，并用 `Runner.run_sync()` 执行。
4. 成功后，Django 把用户消息和助手回复保存到数据库；页面会用会话 ID 恢复聊天记录。

可以从这些实验开始：修改 `Agent.instructions`、给 Agent 增加工具、在 `Message` 添加字段、生成并应用 migration，或在 `chat/urls.py` 新增 API 路由。

## 官方文档

- [OpenAI Agents SDK Quickstart](https://openai.github.io/openai-agents-python/quickstart/)：Agent、Runner、工具和 API key。
- [OpenAI Agents SDK Running agents](https://openai.github.io/openai-agents-python/running_agents/)：运行方式和多轮对话管理。
- [Django 文档](https://docs.djangoproject.com/en/5.2/)
- [uv 文档](https://docs.astral.sh/uv/)
