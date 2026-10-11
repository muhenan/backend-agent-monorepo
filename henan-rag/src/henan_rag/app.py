import json
import logging
from collections.abc import AsyncIterator
from contextlib import aclosing, asynccontextmanager
from pathlib import Path
from typing import Annotated
from uuid import UUID

from agents import set_default_openai_api, set_default_openai_client, set_tracing_disabled
from fastapi import FastAPI, File, Query, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from openai import AsyncOpenAI
from qdrant_client import AsyncQdrantClient

from henan_rag.config import get_settings
from henan_rag.errors import DomainError
from henan_rag.metadata_repository import MetadataRepository
from henan_rag.rag_service import RagService
from henan_rag.schemas import (
    ChatResponse,
    CreateKnowledgeBaseRequest,
    DocumentChunkPage,
    DocumentPage,
    DocumentSummary,
    KnowledgeBasePage,
    KnowledgeBaseSummary,
    PatchKnowledgeBaseRequest,
    QueryRequest,
    RetrievedChunk,
)

settings = get_settings()
logger = logging.getLogger(__name__)
PageLimit = Annotated[int, Query(ge=1, le=200)]
PageOffset = Annotated[int, Query(ge=0)]
ChunkPageLimit = Annotated[int, Query(ge=1, le=100)]


# 用途：启动时初始化 SQLite、新向量集合与一个共享服务，恢复未完成记录。
# 参数：application 为 FastAPI 应用。产出：无值；退出或启动失败时关闭全部外部客户端。
@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    qdrant = AsyncQdrantClient(url=settings.qdrant_url)
    embeddings = AsyncOpenAI(
        api_key=settings.openai_api_key or "missing-api-key", base_url=settings.openai_base_url
    )
    chat_client = AsyncOpenAI(
        api_key=settings.chat_api_key or settings.openai_api_key or "missing-api-key",
        base_url=settings.chat_base_url,
    )
    try:
        metadata = MetadataRepository(settings.metadata_db_path)
        await metadata.initialize()
        rag = RagService(settings, qdrant, embeddings, chat_client, metadata)
        await rag.ensure_collection()
        await rag.recover_pending()
        set_default_openai_client(chat_client, use_for_tracing=False)
        set_default_openai_api(settings.chat_api_mode)
        set_tracing_disabled(True)
        application.state.rag = rag
        yield
    finally:
        await qdrant.close()
        await embeddings.close()
        await chat_client.close()


app = FastAPI(
    title="Henan RAG Lab",
    description="Manage knowledge bases and inspect a self-built RAG pipeline",
    lifespan=lifespan,
)


# 用途：统一输出服务层领域错误，不把底层异常或文件路径直接传给网页。
# 参数：request 为当前请求；exc 为领域异常。返回：带 detail 对象的 JSON 错误响应。
@app.exception_handler(DomainError)
async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.to_detail()})


# 用途：统一参数验证错误结构，同时保留出错字段位置。
# 参数：request 为请求；exc 为 FastAPI/Pydantic 验证异常。返回：422 JSON 响应。
@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": {
        "code": "VALIDATION_ERROR", "message": "输入参数不符合要求，请检查字段后重试。",
        "retryable": False,
        "errors": [{"field": ".".join(str(part) for part in error["loc"]), "message": error["msg"]}
                   for error in exc.errors()],
    }})


# 用途：记录未预期异常，给客户端可重试的安全提示。
# 参数：request 为请求；exc 为未处理异常。返回：503 JSON 响应。
@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error("接口发生未处理异常", exc_info=(type(exc), exc, exc.__traceback__))
    return JSONResponse(status_code=503, content={"detail": {
        "code": "METADATA_UNAVAILABLE", "message": "服务暂时不可用，请稍后重试。", "retryable": True,
    }})


# 用途：读取生命周期创建的唯一共享 RAG 服务。
# 参数：无。返回：应用状态中的 RagService。
def get_rag() -> RagService:
    return app.state.rag


# 用途：确认嵌入模型密钥已配置；知识库管理本身不依赖模型密钥。
# 参数：无（读取配置）。返回：无；缺少配置抛 503 领域错误。
def require_openai_key() -> None:
    if not settings.openai_api_key or settings.openai_api_key == "replace-me":
        raise DomainError(503, "MODEL_NOT_CONFIGURED", "请在 henan-rag/.env 配置 OPENAI_API_KEY。")


# 用途：返回管理知识库和流式问答的静态页面。
# 参数：无。返回：HTML 字符串。
@app.get("/", response_class=HTMLResponse)
async def home() -> str:
    return Path(__file__).with_name("static").joinpath("index.html").read_text(encoding="utf-8")


# 用途：检查元数据数据库及新向量集合，同时报告嵌入密钥配置状态。
# 参数：无。返回：健康状态 JSON；数据库或 Qdrant 故障返回 503。
@app.get("/health")
async def health() -> dict[str, str | bool]:
    rag = get_rag()
    metadata_ok = await rag.metadata.health()
    qdrant_ok = await rag.qdrant.collection_exists(settings.qdrant_collection)
    if not metadata_ok or not qdrant_ok:
        raise DomainError(503, "METADATA_UNAVAILABLE", "元数据或向量库尚未就绪。", True)
    return {
        "status": "ok", "metadata": metadata_ok, "qdrant": qdrant_ok,
        "openai_configured": bool(settings.openai_api_key and settings.openai_api_key != "replace-me"),
    }


# 用途：分页列出知识库，默认库排第一，删除失败的库也可见。
# 参数：limit 为每页条数 1~200；offset 为跳过的条数。返回：知识库分页对象。
@app.get("/api/knowledge-bases", response_model=KnowledgeBasePage)
async def list_knowledge_bases(limit: PageLimit = 50, offset: PageOffset = 0) -> KnowledgeBasePage:
    return await get_rag().metadata.list_knowledge_bases(limit, offset)


# 用途：创建带稳定 UUID 的知识库，名称标准化后必须唯一。
# 参数：request 包含名称和可选描述。返回：201 知识库摘要。
@app.post("/api/knowledge-bases", response_model=KnowledgeBaseSummary, status_code=201)
async def create_knowledge_base(request: CreateKnowledgeBaseRequest) -> KnowledgeBaseSummary:
    return await get_rag().metadata.create_knowledge_base(request.name, request.description)


# 用途：查看知识库信息及 ready 文档、chunk 数量。
# 参数：knowledge_base_id 为路径 UUID。返回：知识库摘要；未知 ID 返回 404。
@app.get("/api/knowledge-bases/{knowledge_base_id}", response_model=KnowledgeBaseSummary)
async def get_knowledge_base(knowledge_base_id: UUID) -> KnowledgeBaseSummary:
    return await get_rag().metadata.get_knowledge_base(str(knowledge_base_id))


# 用途：修改知识库名称或描述，稳定 ID 和文档归属保持不变。
# 参数：knowledge_base_id 为路径 UUID；request 至少提供名称或描述。返回：更新后摘要。
@app.patch("/api/knowledge-bases/{knowledge_base_id}", response_model=KnowledgeBaseSummary)
async def update_knowledge_base(
    knowledge_base_id: UUID, request: PatchKnowledgeBaseRequest
) -> KnowledgeBaseSummary:
    return await get_rag().metadata.update_knowledge_base(
        str(knowledge_base_id), name=request.name, description=request.description
    )


# 用途：删除知识库及所有关联资料，默认库不可删，删除失败可以重复请求。
# 参数：knowledge_base_id 为路径 UUID。返回：204 空响应。
@app.delete("/api/knowledge-bases/{knowledge_base_id}", status_code=204)
async def delete_knowledge_base(knowledge_base_id: UUID) -> Response:
    await get_rag().delete_knowledge_base(str(knowledge_base_id))
    return Response(status_code=204)


# 用途：分页查看指定知识库的全部文档状态，包括失败记录。
# 参数：knowledge_base_id 为库 UUID；limit 为页大小；offset 为跳过条数。返回：文档分页。
@app.get("/api/knowledge-bases/{knowledge_base_id}/documents", response_model=DocumentPage)
async def list_documents(
    knowledge_base_id: UUID, limit: PageLimit = 50, offset: PageOffset = 0
) -> DocumentPage:
    await get_rag().metadata.get_knowledge_base(str(knowledge_base_id))
    return await get_rag().metadata.list_documents(str(knowledge_base_id), limit, offset)


# 用途：查看指定 ready 文档实际存入向量库的分块内容和向量摘要，无需调用模型。
# 参数：knowledge_base_id/document_id 为归属 UUID；limit 为每页 1~100 条；
# offset 为 chunk_index 起始游标（包含该索引）。返回：按索引排序的实际存储分页。
@app.get(
    "/api/knowledge-bases/{knowledge_base_id}/documents/{document_id}/chunks",
    response_model=DocumentChunkPage,
)
async def list_document_chunks(
    knowledge_base_id: UUID,
    document_id: UUID,
    limit: ChunkPageLimit = 12,
    offset: PageOffset = 0,
) -> DocumentChunkPage:
    return await get_rag().list_document_chunks(
        str(knowledge_base_id), str(document_id), limit, offset
    )


# 用途：把上传的资料入库到一个明确选择的知识库。
# 参数：knowledge_base_id 为库 UUID；file 为 multipart 文件。返回：201 文档摘要；
# 超限、格式错误、解析失败分别返回结构化错误，失败记录可在文档列表查看。
@app.post(
    "/api/knowledge-bases/{knowledge_base_id}/documents",
    response_model=DocumentSummary, status_code=201,
)
async def ingest_file(
    knowledge_base_id: UUID, file: Annotated[UploadFile, File()]
) -> DocumentSummary:
    try:
        await get_rag().resolve_scope([str(knowledge_base_id)])
        require_openai_key()
        if not file.filename:
            raise DomainError(422, "VALIDATION_ERROR", "文件名不能为空。")
        max_bytes = settings.max_upload_mb * 1024 * 1024
        content = await file.read(max_bytes + 1)
        if len(content) > max_bytes:
            raise DomainError(413, "UPLOAD_TOO_LARGE", f"文件超过 {settings.max_upload_mb} MB 限制。")
        return await get_rag().ingest_file(file.filename, content, str(knowledge_base_id))
    finally:
        await file.close()


# 用途：按知识库和文档两个 ID 校验归属后删除资料。
# 参数：knowledge_base_id 为库 UUID；document_id 为文档 UUID。返回：204 空响应。
@app.delete("/api/knowledge-bases/{knowledge_base_id}/documents/{document_id}", status_code=204)
async def delete_document(knowledge_base_id: UUID, document_id: UUID) -> Response:
    await get_rag().delete_document(str(knowledge_base_id), str(document_id))
    return Response(status_code=204)


# 用途：只召回所选一个或多个库的文档片段，方便观察检索质量。
# 参数：request 包含问题、必填 knowledge_base_ids 和全局 top_k。返回：引用数组。
@app.post("/api/retrieve", response_model=list[RetrievedChunk])
async def retrieve(request: QueryRequest) -> list[RetrievedChunk]:
    knowledge_base_ids = [str(value) for value in request.knowledge_base_ids]
    await get_rag().resolve_scope(knowledge_base_ids)
    require_openai_key()
    return await get_rag().retrieve(request.question, knowledge_base_ids, request.top_k)


# 用途：完整运行同一 Agent 工具流程，返回所选知识库快照与答案、引用。
# 参数：request 为问题及多库范围。返回：ChatResponse；模型异常转换为安全的 502 错误。
@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: QueryRequest) -> ChatResponse:
    knowledge_base_ids = [str(value) for value in request.knowledge_base_ids]
    scope = await get_rag().resolve_scope(knowledge_base_ids)
    require_openai_key()
    try:
        return await get_rag().answer(
            request.question, knowledge_base_ids, request.top_k, knowledge_bases=scope
        )
    except DomainError:
        raise
    except Exception as exc:
        logger.exception("非流式模型调用失败")
        raise DomainError(502, "MODEL_FAILED", "模型调用失败，请稍后重试。", True) from exc


# 用途：先验证全部知识库，再以 SSE 展示检索范围、工具状态和回答增量。
# 参数：request 为问答输入；http_request 用于检测连接断开。返回：流式响应。
@app.post("/api/chat/stream")
async def chat_stream(request: QueryRequest, http_request: Request) -> StreamingResponse:
    knowledge_base_ids = [str(value) for value in request.knowledge_base_ids]
    scope = await get_rag().resolve_scope(knowledge_base_ids)
    require_openai_key()

    # 用途：编码服务事件为 SSE，并在异常或页面关闭时释放 Agent 流。
    # 参数：无（读取 request/http_request/scope）。产出：UTF-8 SSE 帧；错误不会再发送 done。
    async def stream_events() -> AsyncIterator[str]:
        try:
            async with aclosing(get_rag().answer_stream(
                request.question, knowledge_base_ids, request.top_k, knowledge_bases=scope
            )) as stream:
                async for name, payload in stream:
                    if await http_request.is_disconnected():
                        break
                    yield f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
        except Exception as exc:  # noqa: BLE001 - SSE 已开始，统一日志/公开错误由 stream_error 处理。
            payload = RagService.stream_error(exc)
            yield f"event: error\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        stream_events(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# 用途：启动单 worker 服务，保持进程内写锁与共享解析器的约束。
# 参数：无。返回：无（服务退出后结束）。
def run() -> None:
    import uvicorn

    uvicorn.run("henan_rag.app:app", host="0.0.0.0", port=8000, workers=1)
