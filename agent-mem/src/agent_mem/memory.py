"""Mem0 setup and the explicit long-term memory operations used by the demo."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from mem0 import Memory


@lru_cache(maxsize=1)
def get_memory() -> Memory:
    """创建并缓存 Mem0 客户端，供所有长期记忆操作共用。

    无参数；从环境变量读取 DeepSeek、OpenAI 和 Qdrant 配置。
    DeepSeek 提取记忆，OpenAI 生成向量，Qdrant 存储和检索向量。

    Returns:
        配置完成的 Mem0 客户端；进程内后续调用复用同一实例。
    """
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
    """在指定用户的长期记忆中进行语义搜索。

    Args:
        user_id: 记忆所属的用户身份，用于隔离不同用户的数据。
        query: 要查找的历史事实、偏好或决定的自然语言描述。
        limit: 最多返回的记忆条数，默认 5 条。

    Returns:
        匹配的记忆文本列表；没有匹配结果时返回空列表。
    """
    response = get_memory().search(query, filters={"user_id": user_id}, top_k=limit)
    return [item["memory"] for item in response.get("results", []) if item.get("memory")]


def save_turn(user_id: str, user_message: str, assistant_message: str) -> Any:
    """让 Mem0 从本轮完整对话中提取值得长期保存的信息。

    每轮尝试提取不代表一定写入；Mem0 会判断是否新增、更新或删除记忆。

    Args:
        user_id: 本轮记忆所属的用户身份。
        user_message: 本轮用户消息，不包含此前的会话历史。
        assistant_message: 本轮助手的最终完整回答，不使用流式片段。

    Returns:
        Mem0 的原始写入结果，交由接口层解析实际变更事件。
    """
    return get_memory().add(
        [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": assistant_message},
        ],
        user_id=user_id,
    )


def list_memories(user_id: str) -> list[dict[str, str]]:
    """列出指定用户的长期记忆，供前端记忆面板展示。

    Args:
        user_id: 要读取记忆的用户身份。

    Returns:
        包含记录 ID（id）和记忆文本（memory）的列表。
    """
    response = get_memory().get_all(filters={"user_id": user_id})
    return [
        {"id": str(item["id"]), "memory": item["memory"]}
        for item in response.get("results", [])
        if item.get("id") and item.get("memory")
    ]


def delete_memory(memory_id: str, user_id: str) -> None:
    """先检查记忆归属，再删除指定的一条长期记忆。

    Args:
        memory_id: 要删除的 Mem0 记录 ID。
        user_id: 发起删除的用户身份，必须与记录所属用户一致。

    Raises:
        LookupError: 指定用户的记忆中不存在该记录。
    """
    records = get_memory().get_all(filters={"user_id": user_id})
    if not any(str(item.get("id")) == memory_id for item in records.get("results", [])):
        raise LookupError("Memory not found for this user")
    get_memory().delete(memory_id)


def delete_all_memories(user_id: str) -> None:
    """清空指定用户的全部长期记忆。

    Args:
        user_id: 要清空记忆的用户身份；其他用户的数据不受影响。
    """
    get_memory().delete_all(user_id=user_id)
