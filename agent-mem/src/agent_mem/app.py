"""FastAPI application for the Mem0 learning chatbot."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agent_mem.agent import answer, stream_answer
from agent_mem.memory import delete_memory, list_memories, save_turn

logger = logging.getLogger("uvicorn.error")

load_dotenv()

app = FastAPI(
    title="Mem0 Memory Chatbot", description="A learning project for persistent AI memory"
)
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class HistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=10000)


class ChatRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=10000)
    history: list[HistoryMessage] = Field(default_factory=list, max_length=20)


class SearchActivity(BaseModel):
    query: str
    status: Literal["ok", "error"]
    memories: list[str]


class WriteEvent(BaseModel):
    id: str
    event: Literal["ADD", "UPDATE", "DELETE"]
    memory: str


class WriteActivity(BaseModel):
    status: Literal["saved", "no_change", "error", "unknown"]
    events: list[WriteEvent] = Field(default_factory=list)


class ChatResponse(BaseModel):
    answer: str
    recalled_memories: list[str]
    saved_memories: list[str]
    memory_searches: list[SearchActivity]
    memory_write: WriteActivity


def parse_write_result(result: object) -> WriteActivity:
    """解析 Mem0 写入结果，只将有记录 ID 的实际变更视为保存成功。

    Args:
        result: save_turn 返回的原始结果，结构可能因 Mem0 返回值而不同。

    Returns:
        写入状态和 ADD / UPDATE / DELETE 事件；NONE 或空结果表示无变更，
        无法识别的结果标记为 unknown，避免仅凭记忆文本声称保存成功。
    """
    if not isinstance(result, dict) or not isinstance(result.get("results"), list):
        return WriteActivity(status="unknown")
    events = []
    unknown = False
    for item in result["results"]:
        if not isinstance(item, dict):
            unknown = True
            continue
        event = str(item.get("event", "")).upper()
        if event in {"ADD", "UPDATE", "DELETE"} and item.get("id"):
            events.append(
                WriteEvent(id=str(item["id"]), event=event, memory=str(item.get("memory") or ""))
            )
        elif event != "NONE":
            unknown = True
    return WriteActivity(
        status="unknown" if unknown else ("saved" if events else "no_change"), events=events
    )


class MemoriesResponse(BaseModel):
    user_id: str
    memories: list[dict[str, str]]


def run() -> None:
    """启动 Demo 的 HTTP 服务。

    无参数；监听所有网卡，端口读取 APP_PORT 环境变量，默认 8000。
    """
    import uvicorn

    uvicorn.run("agent_mem.app:app", host="0.0.0.0", port=int(os.getenv("APP_PORT", "8000")))


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    """返回聊天首页的 HTML 文件；无参数。"""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
async def health() -> dict[str, str]:
    """返回 HTTP 服务的存活状态；无参数，不检查模型或记忆服务连通性。"""
    return {"status": "ok"}


@app.get("/api/memories", response_model=MemoriesResponse)
async def get_memories(user_id: str = Query(min_length=1, max_length=100)) -> MemoriesResponse:
    """读取指定用户的长期记忆，供前端刷新记忆面板。

    Args:
        user_id: URL 查询参数中的记忆身份，使用时去掉首尾空白。

    Returns:
        用户身份和记忆记录列表；读取失败时返回 HTTP 503。
    """
    try:
        return MemoriesResponse(user_id=user_id.strip(), memories=list_memories(user_id.strip()))
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Mem0 memory lookup failed: {exc}") from exc


@app.delete("/api/memories")
async def clear_memories(user_id: str = Query(min_length=1, max_length=100)) -> dict[str, str]:
    """删除指定用户的全部长期记忆。

    Args:
        user_id: URL 查询参数中的记忆身份。

    Returns:
        删除状态和用户身份；删除失败时返回 HTTP 503。
    """
    try:
        from agent_mem.memory import delete_all_memories

        delete_all_memories(user_id.strip())
        return {"status": "deleted", "user_id": user_id.strip()}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Mem0 memory deletion failed: {exc}") from exc


@app.delete("/api/memories/{memory_id}")
async def remove_memory(
    memory_id: str, user_id: str = Query(min_length=1, max_length=100)
) -> dict[str, str]:
    """校验记忆归属并删除单条记录。

    Args:
        memory_id: URL 路径中的 Mem0 记录 ID。
        user_id: URL 查询参数中的记忆身份，用于校验该记录归属。

    Returns:
        删除状态和记录 ID；记录不属于该用户时返回 404，服务失败时返回 503。
    """
    try:
        delete_memory(memory_id, user_id.strip())
        return {"status": "deleted", "id": memory_id}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Mem0 memory deletion failed: {exc}") from exc


@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """处理非流式聊天：先生成完整回答，再尝试保存本轮长期记忆。

    Args:
        request: 请求体，包含用户身份、当前问题和当前会话历史。

    Returns:
        完整回答、记忆查询记录和实际写入结果；记忆写入失败仍保留回答。
        空白身份或问题返回 422，回答生成失败返回 503。
    """
    message = request.message.strip()
    user_id = request.user_id.strip()
    if not user_id:
        raise HTTPException(status_code=422, detail="user_id must not be blank")
    if not message:
        raise HTTPException(status_code=422, detail="message must not be blank")
    try:
        result = await answer(message, user_id, [item.model_dump() for item in request.history])
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Agent response failed") from exc
    try:
        write_result = await asyncio.to_thread(save_turn, user_id, message, result.answer)
        write = parse_write_result(write_result)
    except Exception as exc:  # noqa: BLE001 -- Preserve the answer when memory storage fails.
        logger.warning("Memory write failed (%s)", type(exc).__name__)
        write = WriteActivity(status="error")
    logger.info("Memory write: status=%s events=%d", write.status, len(write.events))
    return ChatResponse(
        answer=result.answer,
        recalled_memories=list(
            dict.fromkeys(memory for search in result.searches for memory in search["memories"])
        ),
        saved_memories=[event.memory for event in write.events if event.event in {"ADD", "UPDATE"}],
        memory_searches=result.searches,
        memory_write=write,
    )


async def chat_events(request: ChatRequest) -> AsyncIterator[dict]:
    """编排一轮聊天事件，完整回答生成后再执行一次记忆提取和保存。

    Args:
        request: 已通过接口校验的用户身份、当前问题和会话历史。

    Yields:
        思考状态、文字和工具事件、answer_done、记忆写入开始及结果，
        最后输出 done 汇总。回答失败时输出 error 并跳过保存；
        保存失败时仍输出已完成的回答和失败状态。
    """
    yield {"type": "status", "phase": "thinking"}
    completed = None
    try:
        async for event in stream_answer(
            request.message.strip(),
            request.user_id.strip(),
            [item.model_dump() for item in request.history],
        ):
            if event["type"] == "answer_done":
                completed = event
            yield event
        if completed is None:
            raise RuntimeError("Agent stream ended without a final answer")
    except Exception as exc:  # noqa: BLE001 -- SSE failures need an explicit terminal event.
        logger.warning("Agent stream failed (%s)", type(exc).__name__)
        yield {"type": "error", "phase": "answer", "message": "回复生成中断，未执行记忆保存"}
        return

    yield {"type": "memory_write_start"}
    try:
        write_result = await asyncio.to_thread(
            save_turn, request.user_id.strip(), request.message.strip(), completed["answer"]
        )
        write = parse_write_result(write_result)
    except Exception as exc:  # noqa: BLE001 -- Keep the completed answer when storage fails.
        logger.warning("Memory write failed (%s)", type(exc).__name__)
        write = WriteActivity(status="error")
    logger.info("Memory write: status=%s events=%d", write.status, len(write.events))
    yield {"type": "memory_write_done", "memory_write": write.model_dump()}
    payload = ChatResponse(
        answer=completed["answer"],
        recalled_memories=completed["recalled_memories"],
        saved_memories=[event.memory for event in write.events if event.event in {"ADD", "UPDATE"}],
        memory_searches=completed["memory_searches"],
        memory_write=write,
    )
    yield {"type": "done", **payload.model_dump()}


async def sse_stream(request: ChatRequest) -> AsyncIterator[str]:
    """把聊天事件编码为 SSE，并在等待模型或工具时发送心跳。

    Args:
        request: 本轮聊天请求，转交 chat_events 执行。

    Yields:
        UTF-8 JSON 的 data 帧，或每 10 秒一次的 keep-alive 注释帧。
        连接断开时取消生产任务；已在线程中开始的同步 Mem0 操作无法强制停止。
    """
    queue: asyncio.Queue[dict | None] = asyncio.Queue()

    async def produce() -> None:
        """将聊天事件放入共享队列，供 SSE 消费者持续读取。

        无参数；使用外层 request 和 queue。未处理的异常转换为 error 事件，
        最后放入 None 通知消费者事件流结束。
        """
        try:
            async for event in chat_events(request):
                await queue.put(event)
        except Exception as exc:  # noqa: BLE001 -- Do not leave the client waiting indefinitely.
            logger.warning("Chat stream failed (%s)", type(exc).__name__)
            await queue.put(
                {"type": "error", "phase": "stream", "message": "连接中断，保存结果未确认"}
            )
        finally:
            await queue.put(None)

    task = asyncio.create_task(produce())
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=10)
            except TimeoutError:
                yield ": keep-alive\n\n"
                continue
            if event is None:
                break
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@app.post("/api/chat/stream")
async def chat_stream(request: ChatRequest) -> StreamingResponse:
    """校验请求并建立前端实时聊天使用的 SSE 响应。

    Args:
        request: 请求体，包含用户身份、当前问题和最多 20 条历史消息。

    Returns:
        禁用缓存和代理缓冲的流式响应；空白身份或问题在建立流前返回 422。
    """
    if not request.user_id.strip():
        raise HTTPException(status_code=422, detail="user_id must not be blank")
    if not request.message.strip():
        raise HTTPException(status_code=422, detail="message must not be blank")
    return StreamingResponse(
        sse_stream(request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
