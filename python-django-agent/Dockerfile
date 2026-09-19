FROM ghcr.io/astral-sh/uv:0.12.17 AS uv
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY --from=uv /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml README.md ./
RUN uv sync --no-dev --no-install-project

COPY . .
EXPOSE 8000

CMD ["uv", "run", "--no-sync", "python", "manage.py", "runserver", "0.0.0.0:8000"]
