"""OpenAI Agents SDK response generation with retrieved Mem0 context."""

from __future__ import annotations

import os
from functools import lru_cache

from agents import Agent, Runner, set_default_openai_client
from openai import AsyncOpenAI


@lru_cache(maxsize=1)
def configure_deepseek() -> None:
    """Configure the Agents SDK to use DeepSeek's OpenAI-compatible Responses API."""
    client = AsyncOpenAI(
        api_key=os.getenv("DEEPSEEK_API_KEY", ""),
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
    )
    set_default_openai_client(client, use_for_tracing=False)


def build_agent(memories: list[str]) -> Agent:
    configure_deepseek()
    memory_context = "\n".join(f"- {memory}" for memory in memories) or "（目前没有相关长期记忆）"
    return Agent(
        name="Memory Chatbot",
        instructions=(
            "你是一个友好、简洁的中文助手。你会使用提供的长期记忆个性化回答。"
            "记忆只是过去对话中提取的背景；如果它和用户当前明确说法冲突，以当前说法为准。"
            "不要声称自己记得上下文中没有的事。\n\n"
            f"与当前问题相关的 Mem0 记忆：\n{memory_context}"
        ),
        model=os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
    )


async def answer(user_message: str, memories: list[str]) -> str:
    result = await Runner.run(build_agent(memories), user_message)
    return str(result.final_output)
