"""FastAPI application for the Mem0 learning chatbot."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agent_mem.agent import answer
from agent_mem.memory import delete_memory, list_memories, save_turn, search_memories

load_dotenv()

app = FastAPI(title="Mem0 Memory Chatbot", description="A learning project for persistent AI memory")
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class ChatRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=10000)


class ChatResponse(BaseModel):
    answer: str
    recalled_memories: list[str]
    saved_memories: list[str]


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
async def remove_memory(memory_id: str, user_id: str = Query(min_length=1, max_length=100)) -> dict[str, str]:
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
        recalled = search_memories(user_id, message)
        response = await answer(message, recalled)
        result = save_turn(user_id, message, response)
        saved = [item.get("memory", "") for item in result.get("results", []) if item.get("memory")]
        return ChatResponse(answer=response, recalled_memories=recalled, saved_memories=saved)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Chat or memory operation failed: {exc}") from exc
