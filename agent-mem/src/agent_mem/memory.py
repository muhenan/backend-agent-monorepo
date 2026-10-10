"""Mem0 setup and the explicit long-term memory operations used by the demo."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from mem0 import Memory

@lru_cache(maxsize=1)
def get_memory() -> Memory:
    """Create Mem0 with DeepSeek extraction, OpenAI embeddings, and Qdrant."""
    deepseek_key = os.getenv("DEEPSEEK_API_KEY", "")
    deepseek_base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    return Memory.from_config(
        {
            "llm": {
                "provider": "openai",
                "config": {
                    "model": os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
                    "api_key": deepseek_key,
                    "openai_base_url": deepseek_base_url,
                    "temperature": 0.1,
                    "max_tokens": 2000,
                },
            },
            "embedder": {
                "provider": "openai",
                "config": {
                    "model": "text-embedding-3-small",
                    "api_key": os.getenv("OPENAI_API_KEY", ""),
                    "openai_base_url": "https://api.openai.com/v1",
                    "embedding_dims": 1536,
                },
            },
            "vector_store": {
                "provider": "qdrant",
                "config": {
                    "host": os.getenv("MEM0_QDRANT_HOST", "qdrant"),
                    "port": int(os.getenv("MEM0_QDRANT_PORT", "6333")),
                    "collection_name": os.getenv("MEM0_COLLECTION", "agent_mem"),
                    "embedding_model_dims": 1536,
                },
            },
            "version": "v1.1",
        }
    )


def search_memories(user_id: str, query: str, limit: int = 5) -> list[str]:
    """Retrieve semantically relevant memories for one user namespace."""
    response = get_memory().search(
        query, filters={"user_id": user_id}, top_k=limit
    )
    return [item["memory"] for item in response.get("results", []) if item.get("memory")]


def save_turn(user_id: str, user_message: str, assistant_message: str) -> Any:
    """Ask Mem0 to extract and persist useful facts from this exchange."""
    return get_memory().add(
        [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": assistant_message},
        ],
        user_id=user_id,
    )


def list_memories(user_id: str) -> list[dict[str, str]]:
    """List memory text and Mem0 record IDs for one user namespace."""
    response = get_memory().get_all(filters={"user_id": user_id})
    return [
        {"id": str(item["id"]), "memory": item["memory"]}
        for item in response.get("results", [])
        if item.get("id") and item.get("memory")
    ]


def delete_memory(memory_id: str, user_id: str) -> None:
    """Delete one memory after verifying it belongs to the given user namespace."""
    records = get_memory().get_all(filters={"user_id": user_id})
    if not any(str(item.get("id")) == memory_id for item in records.get("results", [])):
        raise LookupError("Memory not found for this user")
    get_memory().delete(memory_id)


def delete_all_memories(user_id: str) -> None:
    """Delete all memories for one user namespace."""
    get_memory().delete_all(user_id=user_id)
