from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from agents import set_default_openai_api, set_default_openai_client, set_tracing_disabled
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from openai import AsyncOpenAI
from qdrant_client import AsyncQdrantClient

from henan_rag.config import get_settings
from henan_rag.rag_service import RagService
from henan_rag.schemas import ChatResponse, QueryRequest

settings = get_settings()


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    qdrant = AsyncQdrantClient(url=settings.qdrant_url)
    embeddings = AsyncOpenAI(
        api_key=settings.openai_api_key or "missing-api-key", base_url=settings.openai_base_url
    )
    chat = AsyncOpenAI(
        api_key=settings.chat_api_key or settings.openai_api_key or "missing-api-key",
        base_url=settings.chat_base_url,
    )
    await RagService(settings, qdrant, embeddings, chat).ensure_collection()
    set_default_openai_client(chat, use_for_tracing=False)
    set_default_openai_api(settings.chat_api_mode)
    set_tracing_disabled(True)
    application.state.rag = RagService(settings, qdrant, embeddings, chat)
    yield
    await qdrant.close()
    await embeddings.close()
    await chat.close()


app = FastAPI(
    title="Henan RAG Lab",
    description="A visible, self-built RAG pipeline for learning",
    lifespan=lifespan,
)


def get_rag() -> RagService:
    return app.state.rag


def require_openai_key() -> None:
    if not settings.openai_api_key or settings.openai_api_key == "replace-me":
        raise HTTPException(status_code=503, detail="请在 henan-rag/.env 配置 OPENAI_API_KEY")


@app.get("/", response_class=HTMLResponse)
async def home() -> str:
    return Path(__file__).with_name("static").joinpath("index.html").read_text(encoding="utf-8")


@app.get("/health")
async def health() -> dict[str, str | bool]:
    return {
        "status": "ok",
        "qdrant": await get_rag().qdrant.collection_exists(settings.qdrant_collection),
        "openai_configured": bool(settings.openai_api_key and settings.openai_api_key != "replace-me"),
    }


@app.get("/api/documents")
async def list_documents(tenant_id: str = "default"):
    return await get_rag().list_documents(tenant_id)


@app.post("/api/ingest/file")
async def ingest_file(
    file: Annotated[UploadFile, File()],
    tenant_id: Annotated[str, Form()] = "default",
):
    require_openai_key()
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")
    max_bytes = settings.max_upload_mb * 1024 * 1024
    content = await file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise HTTPException(status_code=413, detail=f"文件超过 {settings.max_upload_mb} MB 限制")
    try:
        return await get_rag().ingest_file(file.filename, content, tenant_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"解析或向量化失败：{exc}") from exc


@app.delete("/api/documents/{document_id}")
async def delete_document(document_id: str, tenant_id: str = "default") -> dict[str, str]:
    await get_rag().delete_document(document_id, tenant_id)
    return {"deleted": document_id}


@app.post("/api/retrieve")
async def retrieve(request: QueryRequest):
    require_openai_key()
    return await get_rag().retrieve(request.question, request.tenant_id, request.top_k)


@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: QueryRequest) -> ChatResponse:
    require_openai_key()
    try:
        answer, citations = await get_rag().answer(
            request.question, request.tenant_id, request.top_k
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"模型调用失败：{exc}") from exc
    return ChatResponse(answer=answer, citations=citations)


def run() -> None:
    import uvicorn

    uvicorn.run("henan_rag.app:app", host="0.0.0.0", port=8000)
