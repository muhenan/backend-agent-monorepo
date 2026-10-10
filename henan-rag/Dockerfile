FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 libmagic1 poppler-utils tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev
COPY tests ./tests
RUN find src tests -type f -name '*.py' -exec chmod 0644 {} +

EXPOSE 8000
CMD ["uv", "run", "--no-sync", "uvicorn", "henan_rag.app:app", "--host", "0.0.0.0", "--port", "8000"]
