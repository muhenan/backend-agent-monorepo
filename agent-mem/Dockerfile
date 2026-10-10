FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_PORT=8000

WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY tests ./tests
RUN find src tests -type f -exec chmod 644 {} +
RUN uv sync --locked --no-dev --no-install-project
RUN uv sync --locked --no-dev --no-editable
EXPOSE 8000

CMD ["uv", "run", "--no-sync", "agent-mem"]
