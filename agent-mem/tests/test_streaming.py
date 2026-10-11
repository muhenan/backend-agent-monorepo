"""Streaming contracts: real SDK deltas, tool lifecycle, final-only writes and cleanup."""

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agents.tool_context import ToolContext
from fastapi.testclient import TestClient
from openai.types.responses import ResponseTextDeltaEvent

from agent_mem.agent import build_agent, stream_answer
from agent_mem.app import ChatRequest, app, sse_stream


def sse_events(response):
    return [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def text_event(delta):
    return SimpleNamespace(
        type="raw_response_event",
        data=ResponseTextDeltaEvent(
            type="response.output_text.delta",
            delta=delta,
            content_index=0,
            item_id="message-1",
            output_index=0,
            sequence_number=0,
            logprobs=[],
        ),
    )


async def invoke_search(agent, query):
    return await agent.tools[0].on_invoke_tool(
        ToolContext(
            context=None,
            tool_name="search_memory",
            tool_call_id="sdk-test",
            tool_arguments="{}",
        ),
        json.dumps({"query": query}),
    )


class StreamingApiTests(unittest.TestCase):
    def test_sse_orders_live_activity_and_saves_final_exchange_once(self):
        with patch("agent_mem.app.save_turn") as save:
            save.return_value = {"results": [{"id": "new", "event": "ADD", "memory": "新事实"}]}

            async def generate(message, user_id, history):
                self.assertEqual((message, user_id), ("question", "alice"))
                self.assertEqual(history, [{"role": "assistant", "content": "previous"}])
                yield {
                    "type": "tool_start",
                    "id": "search-1",
                    "name": "search_memory",
                    "query": "历史",
                }
                yield {
                    "type": "tool_end",
                    "id": "search-1",
                    "name": "search_memory",
                    "query": "历史",
                    "status": "ok",
                    "memories": ["旧事实"],
                }
                yield {"type": "text_delta", "delta": "回"}
                yield {"type": "text_delta", "delta": "答"}
                save.assert_not_called()
                yield {
                    "type": "answer_done",
                    "answer": "回答",
                    "memory_searches": [{"query": "历史", "status": "ok", "memories": ["旧事实"]}],
                    "recalled_memories": ["旧事实"],
                }
                save.assert_not_called()

            with patch("agent_mem.app.stream_answer", side_effect=generate):
                response = TestClient(app).post(
                    "/api/chat/stream",
                    json={
                        "user_id": " alice ",
                        "message": " question ",
                        "history": [{"role": "assistant", "content": "previous"}],
                    },
                )
            self.assertEqual(response.status_code, 200)
            self.assertIn("text/event-stream", response.headers["content-type"])
            self.assertEqual(response.headers["cache-control"], "no-cache")
            self.assertEqual(response.headers["x-accel-buffering"], "no")
            events = sse_events(response)
            self.assertEqual(
                [event["type"] for event in events],
                [
                    "status",
                    "tool_start",
                    "tool_end",
                    "text_delta",
                    "text_delta",
                    "answer_done",
                    "memory_write_start",
                    "memory_write_done",
                    "done",
                ],
            )
            self.assertEqual(events[0]["phase"], "thinking")
            self.assertEqual(events[-1]["answer"], "回答")
            self.assertEqual(events[-1]["recalled_memories"], ["旧事实"])
            self.assertEqual(events[-1]["saved_memories"], ["新事实"])
            self.assertEqual(events[-2]["memory_write"]["status"], "saved")
            self.assertEqual(events[-2]["memory_write"], events[-1]["memory_write"])
            save.assert_called_once_with("alice", "question", "回答")

    def test_write_failure_is_distinct_and_preserves_completed_answer(self):
        async def generate(*args):
            yield {"type": "text_delta", "delta": "reply"}
            yield {
                "type": "answer_done",
                "answer": "reply",
                "memory_searches": [],
                "recalled_memories": [],
            }

        with (
            patch("agent_mem.app.stream_answer", side_effect=generate),
            patch("agent_mem.app.save_turn", side_effect=RuntimeError("secret")) as save,
        ):
            response = TestClient(app).post(
                "/api/chat/stream",
                json={"user_id": "alice", "message": "hi"},
            )
        events = sse_events(response)
        self.assertEqual(events[-2]["type"], "memory_write_done")
        self.assertEqual(events[-2]["memory_write"]["status"], "error")
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(events[-1]["answer"], "reply")
        self.assertEqual(events[-1]["saved_memories"], [])
        self.assertNotIn("secret", response.text)
        save.assert_called_once_with("alice", "hi", "reply")

    def test_answer_failure_keeps_partial_tokens_but_never_saves(self):
        async def generate(*args):
            yield {"type": "text_delta", "delta": "partial"}
            raise RuntimeError("secret")

        with (
            patch("agent_mem.app.stream_answer", side_effect=generate),
            patch("agent_mem.app.save_turn") as save,
        ):
            response = TestClient(app).post(
                "/api/chat/stream",
                json={"user_id": "alice", "message": "hi"},
            )
        events = sse_events(response)
        self.assertEqual([event["type"] for event in events], ["status", "text_delta", "error"])
        self.assertEqual(events[-1]["phase"], "answer")
        self.assertNotIn("secret", response.text)
        save.assert_not_called()

    def test_stream_without_final_answer_never_saves(self):
        async def generate(*args):
            yield {"type": "text_delta", "delta": "partial"}

        with (
            patch("agent_mem.app.stream_answer", side_effect=generate),
            patch("agent_mem.app.save_turn") as save,
        ):
            response = TestClient(app).post(
                "/api/chat/stream",
                json={"user_id": "alice", "message": "hi"},
            )
        self.assertEqual(sse_events(response)[-1]["type"], "error")
        save.assert_not_called()

    def test_invalid_request_is_rejected_before_stream_or_write(self):
        requests = [
            {"user_id": "   ", "message": "hi"},
            {"user_id": "alice", "message": "   "},
            {"user_id": "alice", "message": "hi", "history": [{"role": "system", "content": "x"}]},
            {
                "user_id": "alice",
                "message": "hi",
                "history": [{"role": "user", "content": "x"}] * 21,
            },
        ]
        with (
            patch("agent_mem.app.stream_answer") as generate,
            patch("agent_mem.app.save_turn") as save,
        ):
            client = TestClient(app)
            for payload in requests:
                with self.subTest(payload=payload):
                    response = client.post("/api/chat/stream", json=payload)
                    self.assertEqual(response.status_code, 422)
                    self.assertIn("application/json", response.headers["content-type"])
            generate.assert_not_called()
            save.assert_not_called()


class AgentStreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_sdk_deltas_and_tool_lifecycle_are_forwarded(self):
        captured = {}

        class FakeRun:
            final_output = "你好"
            is_complete = False
            cancel = Mock()

            async def stream_events(self):
                await invoke_search(captured["agent"], "history")
                yield SimpleNamespace(type="run_item_stream_event", data={"ignored": True})
                yield text_event("你")
                await invoke_search(captured["agent"], "follow up")
                yield text_event("好")
                self.is_complete = True

        result = FakeRun()

        def run(agent, inputs, **kwargs):
            captured["agent"] = agent
            self.assertEqual(
                inputs,
                [{"role": "assistant", "content": "past"}, {"role": "user", "content": "now"}],
            )
            self.assertEqual(kwargs["max_turns"], 6)
            return result

        with (
            patch("agent_mem.agent.configure_deepseek"),
            patch("agent_mem.agent.search_memories", return_value=["fact"]) as search,
            patch("agent_mem.agent.Runner.run_streamed", side_effect=run),
        ):
            events = [
                event
                async for event in stream_answer(
                    "now", "alice", [{"role": "assistant", "content": "past"}]
                )
            ]
        self.assertEqual(
            [event["type"] for event in events],
            [
                "tool_start",
                "tool_end",
                "text_delta",
                "tool_start",
                "tool_end",
                "text_delta",
                "answer_done",
            ],
        )
        self.assertEqual(events[0]["id"], events[1]["id"])
        self.assertNotEqual(events[0]["id"], events[3]["id"])
        self.assertEqual(events[1]["memories"], ["fact"])
        self.assertEqual(
            [event["delta"] for event in events if event["type"] == "text_delta"], ["你", "好"]
        )
        self.assertEqual(events[-1]["answer"], "你好")
        self.assertEqual(events[-1]["recalled_memories"], ["fact"])
        self.assertEqual(len(events[-1]["memory_searches"]), 2)
        self.assertEqual(search.call_count, 2)
        search.assert_called_with("alice", "follow up")
        result.cancel.assert_not_called()

    async def test_search_failure_and_limit_emit_terminal_tool_events(self):
        emitted = []
        searches = []
        with (
            patch("agent_mem.agent.configure_deepseek"),
            patch("agent_mem.agent.search_memories", side_effect=RuntimeError("secret")) as search,
        ):
            agent = build_agent("alice", searches, emitted.append)
            for _ in range(3):
                await invoke_search(agent, "history")
        self.assertEqual([event["type"] for event in emitted], ["tool_start", "tool_end"] * 3)
        self.assertEqual(
            [event["status"] for event in emitted if event["type"] == "tool_end"],
            ["error", "error", "limit_reached"],
        )
        self.assertEqual(search.call_count, 2)
        self.assertEqual(len(searches), 2)
        self.assertNotIn("secret", json.dumps(emitted))

    async def test_sdk_failure_propagates_after_partial_delta_and_cancels(self):
        class FakeRun:
            is_complete = False
            cancel = Mock()

            async def stream_events(self):
                yield text_event("partial")
                raise RuntimeError("upstream failed")

        result = FakeRun()
        events = []
        with (
            patch("agent_mem.agent.configure_deepseek"),
            patch("agent_mem.agent.Runner.run_streamed", return_value=result),
            self.assertRaisesRegex(RuntimeError, "upstream failed"),
        ):
            async for event in stream_answer("hi", "alice"):
                events.append(event)
        self.assertEqual(events, [{"type": "text_delta", "delta": "partial"}])
        result.cancel.assert_called_once()

    async def test_closing_agent_stream_cancels_pending_sdk_work(self):
        cleaned_up = asyncio.Event()
        pending = asyncio.Event()

        class FakeRun:
            is_complete = False
            cancel = Mock()

            async def stream_events(self):
                try:
                    yield text_event("partial")
                    await pending.wait()
                finally:
                    cleaned_up.set()

        result = FakeRun()
        with (
            patch("agent_mem.agent.configure_deepseek"),
            patch("agent_mem.agent.Runner.run_streamed", return_value=result),
        ):
            generator = stream_answer("hi", "alice")
            self.assertEqual(await anext(generator), {"type": "text_delta", "delta": "partial"})
            await asyncio.wait_for(generator.aclose(), timeout=1)
        self.assertTrue(cleaned_up.is_set())
        result.cancel.assert_called_once()

    async def test_closing_sse_stream_cancels_pending_answer(self):
        cleaned_up = asyncio.Event()
        pending = asyncio.Event()

        async def generate(request):
            try:
                yield {"type": "status", "phase": "thinking"}
                await pending.wait()
            finally:
                cleaned_up.set()

        with patch("agent_mem.app.chat_events", side_effect=generate):
            generator = sse_stream(ChatRequest(user_id="alice", message="hi"))
            first_frame = await anext(generator)
            self.assertEqual(
                json.loads(first_frame.removeprefix("data: ")),
                {"type": "status", "phase": "thinking"},
            )
            await asyncio.wait_for(generator.aclose(), timeout=1)
        self.assertTrue(cleaned_up.is_set())


if __name__ == "__main__":
    unittest.main()
