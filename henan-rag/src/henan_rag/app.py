import json
from collections.abc import AsyncIterator
from contextlib import aclosing, asynccontextmanager
from pathlib import Path
from typing import Annotated

from agents import set_default_openai_api, set_default_openai_client, set_tracing_disabled
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from openai import AsyncOpenAI
from qdrant_client import AsyncQdrantClient

from henan_rag.config import get_settings
from henan_rag.rag_service import RagService
from henan_rag.schemas import ChatResponse, QueryRequest

settings = get_settings()


# 用途：启动时建立外部客户端、创建知识库，并在服务退出时关闭客户端。
# 参数：application 为 FastAPI 应用实例，用于保存共享的 RAG 服务。产出：无值。
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


# 用途：读取启动阶段创建的共享 RAG 服务。
# 参数：无。返回：应用状态中的 RagService 实例。
def get_rag() -> RagService:
    return app.state.rag


# 用途：检查嵌入模型所需的 API 密钥，未配置时返回 HTTP 503。
# 参数：无（读取全局配置）。返回：无。
def require_openai_key() -> None:
    if not settings.openai_api_key or settings.openai_api_key == "replace-me":
        raise HTTPException(status_code=503, detail="请在 henan-rag/.env 配置 OPENAI_API_KEY")


# 用途：读取并返回学习项目的静态网页。
# 参数：无。返回：页面 HTML 字符串。
@app.get("/", response_class=HTMLResponse)
async def home() -> str:
    return Path(__file__).with_name("static").joinpath("index.html").read_text(encoding="utf-8")


# 用途：检查向量库集合是否存在，并报告嵌入密钥的配置状态。
# 参数：无。返回：服务状态、Qdrant 连通状态和密钥配置状态。
@app.get("/health")
async def health() -> dict[str, str | bool]:
    return {
        "status": "ok",
        "qdrant": await get_rag().qdrant.collection_exists(settings.qdrant_collection),
        "openai_configured": bool(settings.openai_api_key and settings.openai_api_key != "replace-me"),
    }


# 用途：列出指定知识库内的文档摘要。
# 参数：tenant_id 为知识库标识，默认使用 default。返回：文档摘要列表。
@app.get("/api/documents")
async def list_documents(tenant_id: str = "default"):
    return await get_rag().list_documents(tenant_id)


# 用途：检查文件名、大小后调用解析、切块与向量入库流程。
# 参数：file 为 multipart 上传的文件；tenant_id 为目标知识库标识。
# 返回：文档摘要；文件或解析错误分别转换为对应 HTTP 错误。
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


# 用途：删除指定知识库中的文档及其向量记录。
# 参数：document_id 为待删文档 ID；tenant_id 为知识库标识。返回：已删文档 ID。
@app.delete("/api/documents/{document_id}")
async def delete_document(document_id: str, tenant_id: str = "default") -> dict[str, str]:
    await get_rag().delete_document(document_id, tenant_id)
    return {"deleted": document_id}


# 用途：单独执行检索，方便观察 RAG 的召回效果。
# 参数：request 含用户问题、知识库标识 tenant_id 和检索上限 top_k。
# 返回：按相似度排序的文本块列表。
@app.post("/api/retrieve")
async def retrieve(request: QueryRequest):
    require_openai_key()
    return await get_rag().retrieve(request.question, request.tenant_id, request.top_k)


# 用途：运行检索工具和 Agent，完整返回回答，兼容非流式客户端。
# 参数：request 含用户问题、知识库标识 tenant_id 和检索上限 top_k。
# 返回：完整回答和引用文本块；模型调用失败时返回 HTTP 502。
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


# 用途：通过 SSE 实时返回 Agent 的状态、工具调用、文本增量及最终引用。
# 参数：request 为问答输入；http_request 为 HTTP 连接，用于检测页面断开。
# 返回：text/event-stream 响应；连接结束时关闭生成器，取消后台模型运行。
@app.post("/api/chat/stream")
async def chat_stream(request: QueryRequest, http_request: Request) -> StreamingResponse:
    require_openai_key()

    # 用途：编码服务层事件为标准 SSE，并在异常或断开连接时关闭 Agent 流。
    # 参数：无（读取当前 request 和 http_request）。产出：UTF-8 的 SSE 文本帧。
    async def stream_events() -> AsyncIterator[str]:
        try:
            async with aclosing(
                get_rag().answer_stream(request.question, request.tenant_id, request.top_k)
            ) as stream:
                async for name, payload in stream:
                    if await http_request.is_disconnected():
                        break
                    yield f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
        except Exception as exc:  # noqa: BLE001 - SSE 已开始，外部异常需转为流内错误。
            payload = {"message": f"模型或检索调用失败：{exc}"}
            yield f"event: error\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        stream_events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# 用途：启动 Uvicorn HTTP 服务，供本地命令行入口调用。
# 参数：无。返回：无（服务退出后函数结束）。
def run() -> None:
    import uvicorn

    uvicorn.run("henan_rag.app:app", host="0.0.0.0", port=8000)
