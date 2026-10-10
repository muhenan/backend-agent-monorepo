"""FastAPI application for the Mem0 learning chatbot."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agent_mem.agent import answer
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
    """Only count actual Mem0 mutation events; text alone is not a saved memory."""
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
    import uvicorn

    uvicorn.run("agent_mem.app:app", host="0.0.0.0", port=int(os.getenv("APP_PORT", "8000")))


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/memories", response_model=MemoriesResponse)
async def get_memories(user_id: str = Query(min_length=1, max_length=100)) -> MemoriesResponse:
    try:
        return MemoriesResponse(user_id=user_id.strip(), memories=list_memories(user_id.strip()))
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Mem0 memory lookup failed: {exc}") from exc


@app.delete("/api/memories")
async def clear_memories(user_id: str = Query(min_length=1, max_length=100)) -> dict[str, str]:
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
    try:
        delete_memory(memory_id, user_id.strip())
        return {"status": "deleted", "id": memory_id}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Mem0 memory deletion failed: {exc}") from exc


@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
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
