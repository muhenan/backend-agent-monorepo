"""OpenAI Agents SDK integration.

This module keeps model orchestration out of the Django view so the Agent and
Runner flow is easy to find and experiment with.
"""

import os
from collections.abc import AsyncIterator

from agents import Agent, Runner
from agents.decorators import tool


@tool
def django_learning_tip(topic: str) -> str:
    """Return a short Django learning tip for a topic such as models or urls."""
    tips = {
        "models": "Django models describe your data in Python; migrations turn model changes into database changes.",
        "urls": "URL patterns connect a path to a view. Keep route definitions close to the app that owns them.",
        "views": "A view receives an HttpRequest and returns an HttpResponse, often rendered from a template.",
        "agents": "An Agent holds instructions and tools; Runner executes it and returns the final output.",
    }
    return tips.get(
        topic.strip().lower(),
        "Try asking about models, urls, views, or agents for a focused learning tip.",
    )


def _create_agent() -> Agent:
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("请先在 .env 中设置 OPENAI_API_KEY。")

    return Agent(
        name="Django 学习助手",
        instructions=(
            "你是一位耐心的 Python、Django 和 OpenAI Agents SDK 学习助手。"
            "优先用中文回答，先解释概念，再给出简短、可运行的示例。"
            "当用户询问 Django 学习建议时，可以使用 django_learning_tip 工具。"
        ),
        model=os.getenv("OPENAI_MODEL", "gpt-5.6-luna"),
        tools=[django_learning_tip],
    )


def run_agent(input_items: list[dict[str, str]]) -> str:
    """Run one agent turn with the saved conversation history plus the new turn."""
    agent = _create_agent()
    result = Runner.run_sync(agent, input_items)
    return str(result.final_output)


async def stream_agent(input_items: list[dict[str, str]]) -> AsyncIterator[dict[str, str]]:
    """Yield generated text deltas, then the completed assistant response."""
    result = Runner.run_streamed(_create_agent(), input=input_items)
    async for event in result.stream_events():
        if event.type != "raw_response_event":
            continue
        response_event = event.data
        if getattr(response_event, "type", None) == "response.output_text.delta":
            yield {"type": "delta", "text": response_event.delta}

    run_error = getattr(result, "run_loop_exception", None)
    if run_error is not None:
        raise run_error
    if result.final_output is None:
        raise RuntimeError("Agent 没有生成最终回复。")
    yield {"type": "complete", "text": str(result.final_output)}
