import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agents.tool_context import ToolContext
from fastapi.testclient import TestClient

from agent_mem.agent import AnswerResult, answer, build_agent
from agent_mem.app import app, parse_write_result


class MemoryTests(unittest.TestCase):
    def test_homepage_and_static_assets(self):
        client = TestClient(app)
        for path, expected in [("/", "<!doctype html>"), ("/static/app.js", "search_memory"), ("/static/styles.css", ".turn-memory-details")]:
            response = client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn(expected, response.text)

    def test_write_events_are_actual_mutations(self):
        result = parse_write_result(
            {
                "results": [
                    {"id": "a", "event": "ADD", "memory": "name"},
                    {"id": "b", "event": "UPDATE", "memory": "preference"},
                    {"id": "c", "event": "DELETE", "memory": "old"},
                    {"id": "d", "event": "NONE", "memory": "unchanged"},
                ]
            }
        )
        self.assertEqual(result.status, "saved")
        self.assertEqual([event.event for event in result.events], ["ADD", "UPDATE", "DELETE"])

    def test_empty_write_does_not_claim_saved(self):
        self.assertEqual(parse_write_result({"results": []}).status, "no_change")
        self.assertEqual(parse_write_result({"results": [{"memory": "text"}]}).status, "unknown")
        self.assertEqual(parse_write_result(None).status, "unknown")

    def test_search_is_not_executed_when_building_agent(self):
        with (
            patch("agent_mem.agent.configure_deepseek"),
            patch("agent_mem.agent.search_memories") as search,
        ):
            agent = build_agent("alice", [])
            self.assertEqual(agent.tools[0].name, "search_memory")
            search.assert_not_called()

    def test_tool_scopes_search_and_limits_calls(self):
        async def run():
            events = []
            with (
                patch("agent_mem.agent.configure_deepseek"),
                patch("agent_mem.agent.search_memories", return_value=["fact"]) as search,
            ):
                tool = build_agent("alice", events).tools[0]
                for _ in range(2):
                    result = await tool.on_invoke_tool(
                        ToolContext(
                            context=None,
                            tool_name="search_memory",
                            tool_call_id="test",
                            tool_arguments="{}",
                        ),
                        json.dumps({"query": "past decision"}),
                    )
                    self.assertEqual(result["memories"], ["fact"])
                result = await tool.on_invoke_tool(
                    ToolContext(
                        context=None,
                        tool_name="search_memory",
                        tool_call_id="test",
                        tool_arguments="{}",
                    ),
                    json.dumps({"query": "more"}),
                )
                self.assertEqual(result["status"], "limit_reached")
                self.assertEqual(search.call_count, 2)
                search.assert_called_with("alice", "past decision")
                self.assertEqual(len(events), 2)

        asyncio.run(run())

    def test_search_error_is_distinct_from_empty(self):
        async def run():
            for outcome, status in [([], "ok"), (RuntimeError("secret"), "error")]:
                events = []
                kwargs = (
                    {"side_effect": outcome}
                    if isinstance(outcome, Exception)
                    else {"return_value": outcome}
                )
                with (
                    patch("agent_mem.agent.configure_deepseek"),
                    patch("agent_mem.agent.search_memories", **kwargs),
                ):
                    tool = build_agent("alice", events).tools[0]
                    result = await tool.on_invoke_tool(
                        ToolContext(
                            context=None,
                            tool_name="search_memory",
                            tool_call_id="test",
                            tool_arguments="{}",
                        ),
                        '{"query":"name"}',
                    )
                    self.assertEqual(result["status"], status)
                    self.assertEqual(result["memories"], [])
                    self.assertNotIn("secret", str(result))

        asyncio.run(run())

    def test_history_and_final_output(self):
        async def run():
            async def fake_run(agent, inputs, **kwargs):
                self.assertEqual(
                    inputs,
                    [{"role": "user", "content": "previous"}, {"role": "user", "content": "now"}],
                )
                self.assertEqual(kwargs["max_turns"], 6)
                await agent.tools[0].on_invoke_tool(
                    ToolContext(
                        context=None,
                        tool_name="search_memory",
                        tool_call_id="test",
                        tool_arguments="{}",
                    ),
                    '{"query":"history"}',
                )
                return SimpleNamespace(final_output="reply")

            with (
                patch("agent_mem.agent.configure_deepseek"),
                patch("agent_mem.agent.search_memories", return_value=["fact"]),
                patch("agent_mem.agent.Runner.run", side_effect=fake_run),
            ):
                result = await answer("now", "alice", [{"role": "user", "content": "previous"}])
                self.assertEqual(result.answer, "reply")
                self.assertEqual(len(result.searches), 1)

        asyncio.run(run())

    def test_api_saves_only_final_exchange_once(self):
        client = TestClient(app)
        result = AnswerResult(
            "reply", [{"query": "history", "status": "ok", "memories": ["old fact"]}]
        )
        with (
            patch("agent_mem.app.answer", return_value=result) as generate,
            patch(
                "agent_mem.app.save_turn",
                return_value={"results": [{"id": "new", "event": "ADD", "memory": "new fact"}]},
            ) as save,
        ):
            response = client.post(
                "/api/chat",
                json={
                    "user_id": " alice ",
                    "message": " question ",
                    "history": [{"role": "assistant", "content": "previous"}],
                },
            )
            self.assertEqual(response.status_code, 200)
            generate.assert_called_once_with(
                "question", "alice", [{"role": "assistant", "content": "previous"}]
            )
            save.assert_called_once_with("alice", "question", "reply")
            data = response.json()
            self.assertEqual(data["memory_write"]["status"], "saved")
            self.assertEqual(data["saved_memories"], ["new fact"])
            self.assertEqual(data["recalled_memories"], ["old fact"])

    def test_write_failure_keeps_answer(self):
        with (
            patch("agent_mem.app.answer", return_value=AnswerResult("reply")),
            patch("agent_mem.app.save_turn", side_effect=RuntimeError("secret")),
        ):
            response = TestClient(app).post("/api/chat", json={"user_id": "alice", "message": "hi"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["answer"], "reply")
            self.assertEqual(response.json()["memory_write"]["status"], "error")
            self.assertEqual(response.json()["memory_searches"], [])

    def test_agent_failure_does_not_save(self):
        with (
            patch("agent_mem.app.answer", side_effect=RuntimeError("secret")),
            patch("agent_mem.app.save_turn") as save,
        ):
            response = TestClient(app).post("/api/chat", json={"user_id": "alice", "message": "hi"})
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("secret", response.text)
            save.assert_not_called()

    def test_history_validation(self):
        client = TestClient(app)
        for history in [
            [{"role": "system", "content": "x"}],
            [{"role": "user", "content": "x"}] * 21,
        ]:
            self.assertEqual(
                client.post(
                    "/api/chat", json={"user_id": "alice", "message": "hi", "history": history}
                ).status_code,
                422,
            )


if __name__ == "__main__":
    unittest.main()
