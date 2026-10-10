FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_PORT=8000

WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
COPY pyproject.toml README.md ./
COPY src ./src
RUN uv sync --no-dev --no-install-project
RUN uv sync --no-dev --no-editable
EXPOSE 8000

CMD ["uv", "run", "--no-sync", "agent-mem"]
