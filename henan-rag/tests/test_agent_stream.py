import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from agents.tool_context import ToolContext
from fastapi.testclient import TestClient

from henan_rag import app as app_module
from henan_rag.errors import DomainError
from henan_rag.rag_service import RagService
from henan_rag.schemas import KnowledgeBaseReference, RetrievedChunk

KB_ONE = "00000000-0000-4000-8000-000000000001"
KB_TWO = "00000000-0000-4000-8000-000000000002"
SCOPE = [KnowledgeBaseReference(id=KB_ONE, name="服务资料"),
         KnowledgeBaseReference(id=KB_TWO, name="其他资料")]


# 用途：构造固定的检索证据，避免测试依赖外部模型或真实向量数据库。
# 参数：无。返回：包含来源、正文和分数的 RetrievedChunk。
def sample_chunk() -> RetrievedChunk:
    return RetrievedChunk(
        point_id="point-1", document_id="doc-1", source="demo.md", chunk_index=0,
        knowledge_base_id=KB_ONE, knowledge_base_name="服务资料",
        text="服务时间为 09:00 至 17:00。", score=0.9,
    )


class FakeAgentRun:
    # 用途：模拟 SDK 流对象，保留工具真实调用入口及取消状态。
    # 参数：self 为测试实例；agent 为待测 Agent；call_tool 控制是否调用工具；
    # wait_forever 控制工具后等待取消；queued_prelude 模拟旧旁白排队；
    # concurrent_tools 模拟供应商无视并行设置返回两个工具调用。返回：无。
    def __init__(
        self, agent, *, call_tool=True, wait_forever=False, queued_prelude=False,
        concurrent_tools=False,
    ):
        self.agent = agent
        self.call_tool = call_tool
        self.wait_forever = wait_forever
        self.queued_prelude = queued_prelude
        self.concurrent_tools = concurrent_tools
        self.final_output = "服务时间为 09:00 至 17:00。[证据 1]"
        self.cancelled = False
        self.stopped = asyncio.Event()

    # 用途：用标准 SDK 原始事件外形表示一个文本增量。
    # 参数：self 为测试实例；delta 为新生成的文本。返回：可供服务层消费的事件。
    def delta_event(self, delta):
        return SimpleNamespace(
            type="raw_response_event",
            data=SimpleNamespace(type="response.output_text.delta", delta=delta),
        )

    # 用途：模拟检索前文字、工具执行和最终回答增量，支持取消中的清理断言。
    # 参数：self 为测试实例。产出：SDK 风格的事件；工具异常原样抛出。
    async def stream_events(self):
        try:
            if not self.queued_prelude:
                yield self.delta_event("我先检索一下。")
            if self.call_tool:
                tool = self.agent.tools[0]
                context = ToolContext(
                    context=None, tool_name=tool.name, tool_call_id="call-123",
                    tool_arguments='{"question":"服务时间"}',
                )
                if self.concurrent_tools:
                    second = ToolContext(
                        context=None, tool_name=tool.name, tool_call_id="call-456",
                        tool_arguments='{"question":"服务时间"}',
                    )
                    calls = [
                        asyncio.create_task(tool.on_invoke_tool(item, item.tool_arguments))
                        for item in (context, second)
                    ]
                    try:
                        await asyncio.gather(*calls)
                    finally:
                        for call in calls:
                            call.cancel()
                        await asyncio.gather(*calls, return_exceptions=True)
                else:
                    await tool.on_invoke_tool(context, context.tool_arguments)
            if self.queued_prelude:
                # 模拟旧模型旁白还在事件队列里，但工具协程已经完成了检索。
                yield self.delta_event("我先检索一下。")
            if self.call_tool:
                yield SimpleNamespace(
                    type="run_item_stream_event", name="tool_output",
                    item=SimpleNamespace(raw_item={"call_id": "call-123"}),
                )
            if self.wait_forever:
                await asyncio.Event().wait()
            yield self.delta_event("服务时间为 09:00 至 17:00。")
            yield self.delta_event("[证据 1]")
        finally:
            self.stopped.set()

    # 用途：记录服务层是否主动取消了 SDK 后台运行。
    # 参数：self 为测试实例；mode 为取消策略。返回：无。
    def cancel(self, mode="immediate"):
        assert mode == "immediate"
        self.cancelled = True


# 用途：创建绕过文件解析初始化的服务，并替换 SDK 为受控流。
# 参数：monkeypatch 为 pytest 替换工具；call_tool 控制工具调用；wait_forever 控制阻塞；
# queued_prelude 控制是否模拟工具已完成、旧旁白仍排队的场景。
# concurrent_tools 控制供应商是否同时发起两个工具调用。
# 返回：(服务实例, 保存已创建 FakeAgentRun 的列表)。
def mock_service(
    monkeypatch, *, call_tool=True, wait_forever=False, queued_prelude=False,
    concurrent_tools=False,
):
    service = object.__new__(RagService)
    service.settings = SimpleNamespace(chat_model="test-model")
    service.resolve_scope = AsyncMock(return_value=SCOPE)
    service.retrieve = AsyncMock(return_value=[sample_chunk()])
    runs = []

    # 用途：替代 Runner.run_streamed，核实兼容的自动工具选择及工具参数隔离。
    # 参数：agent 为待运行 Agent；prompt 为问题输入；kwargs 为运行选项。
    # 返回：受控 SDK 流对象。
    def run_streamed(agent, prompt, **kwargs):
        assert agent.model_settings.tool_choice == "auto"
        assert agent.model_settings.parallel_tool_calls is False
        assert set(agent.tools[0].params_json_schema["properties"]) == {"question"}
        assert kwargs["max_turns"] == 3
        run = FakeAgentRun(
            agent, call_tool=call_tool, wait_forever=wait_forever,
            queued_prelude=queued_prelude, concurrent_tools=concurrent_tools,
        )
        runs.append(run)
        return run

    monkeypatch.setattr("henan_rag.rag_service.Runner.run_streamed", run_streamed)
    return service, runs


# 用途：验证工具确实调用绑定多库范围、事件顺序、引用及增量与最终答案的一致性。
# 参数：monkeypatch 为 pytest 替换工具。返回：无，断言失败时报告原因。
def test_stream_reports_real_tool_citations_and_text(monkeypatch):
    service, runs = mock_service(monkeypatch)

    # 用途：在一个事件循环内收集流式问答，供同步 pytest 测试断言。
    # 参数：无（读取 service）。返回：事件列表。
    async def collect():
        return [event async for event in service.answer_stream("什么时候开放？", [KB_ONE, KB_TWO], 2)]

    events = asyncio.run(collect())
    names = [name for name, _ in events]
    assert names == ["status", "tool_start", "tool_end", "citations", "text_delta", "text_delta", "done"]
    service.retrieve.assert_awaited_once_with("服务时间", [KB_ONE, KB_TWO], 2)
    assert events[1][1]["knowledge_bases"] == [item.model_dump() for item in SCOPE]
    assert events[2][1]["hits_by_knowledge_base"] == [
        {"id": KB_ONE, "name": "服务资料", "chunk_count": 1},
        {"id": KB_TWO, "name": "其他资料", "chunk_count": 0},
    ]
    assert events[-1][1]["knowledge_bases"] == [item.model_dump() for item in SCOPE]
    assert events[1][1]["call_id"] == events[2][1]["call_id"] == "call-123"
    assert events[2][1]["chunk_count"] == 1
    assert events[3][1]["citations"][0]["point_id"] == "point-1"
    text = "".join(payload["delta"] for name, payload in events if name == "text_delta")
    assert text == events[-1][1]["answer"] == runs[0].final_output
    assert "我先检索" not in text
    assert not runs[0].cancelled


# 用途：验证检索协程先完成时，尚未消费的旧模型旁白不会混入最终答案。
# 参数：monkeypatch 为 pytest 替换工具。返回：无。
def test_queued_prelude_is_hidden_until_sdk_tool_output(monkeypatch):
    service, runs = mock_service(monkeypatch, queued_prelude=True)

    # 用途：收集模拟队列竞争下的流式问答事件。
    # 参数：无（读取 service）。返回：事件列表。
    async def collect():
        return [event async for event in service.answer_stream("问题", [KB_ONE, KB_TWO], 2)]

    events = asyncio.run(collect())
    text = "".join(payload["delta"] for name, payload in events if name == "text_delta")
    assert text == runs[0].final_output
    assert "我先检索" not in text
    assert events[-1][0] == "done"
    assert not any(name == "error" for name, _ in events)


# 用途：验证检索失败立即报告 error，不伪造成功工具事件或完整答案。
# 参数：monkeypatch 为 pytest 替换工具。返回：无。
def test_tool_failure_emits_error_and_cancels_run(monkeypatch):
    service, runs = mock_service(monkeypatch)
    service.retrieve.side_effect = DomainError(503, "INDEX_OPERATION_FAILED", "向量库不可用", True)

    # 用途：收集检索异常对应的流事件。
    # 参数：无（读取 service）。返回：事件列表。
    async def collect():
        return [event async for event in service.answer_stream("问题", [KB_ONE, KB_TWO], 2)]

    events = asyncio.run(collect())
    assert [name for name, _ in events] == ["status", "tool_start", "error"]
    assert "向量库不可用" in events[-1][1]["message"]
    assert events[-1][1]["code"] == "INDEX_OPERATION_FAILED"
    assert runs[0].cancelled


# 用途：验证供应商无视并行限制时，首个 await 前认领工具调用，第二次调用不能进入检索。
# 参数：monkeypatch 为 pytest 替换工具。返回：无；并发冲突输出错误，后台调用被清理。
def test_concurrent_tool_calls_enter_retrieval_only_once(monkeypatch):
    service, runs = mock_service(monkeypatch, concurrent_tools=True)
    retrieval_cancelled = asyncio.Event()

    # 用途：让第一次检索停在 await，强制第二次调用发生在首次检索完成之前。
    # 参数：question 为检索词；knowledge_base_ids 为绑定库；top_k 为上限。
    # 返回：不正常返回；模拟 SDK 取消后设置确认事件。
    async def blocked_retrieve(question, knowledge_base_ids, top_k):
        try:
            await asyncio.Event().wait()
        finally:
            retrieval_cancelled.set()

    service.retrieve = AsyncMock(side_effect=blocked_retrieve)

    # 用途：限时收集并发工具场景，避免缺失调用认领保护时测试永久阻塞。
    # 参数：无（读取 service）。返回：已收集的 SSE 事件列表。
    async def collect():
        return [event async for event in service.answer_stream("问题", [KB_ONE, KB_TWO], 2)]

    # 用途：在同一事件循环下限制采集时间，保留所有后台清理断言。
    # 参数：无。返回：事件列表。
    async def run():
        return await asyncio.wait_for(collect(), timeout=2)

    events = asyncio.run(run())
    assert service.retrieve.await_count == 1
    assert [name for name, _ in events] == ["status", "tool_start", "error"]
    assert "一次回答只能调用一次" in events[-1][1]["message"]
    assert events[-1][1]["code"] == "MODEL_FAILED"
    assert retrieval_cancelled.is_set()
    assert runs[0].cancelled and runs[0].stopped.is_set()


# 用途：验证用户停止输出时会中止仍在等待的检索及 SDK 后台任务。
# 参数：monkeypatch 为 pytest 替换工具。返回：无。
def test_closing_stream_cancels_inflight_tool(monkeypatch):
    service, runs = mock_service(monkeypatch)
    retrieval_cancelled = asyncio.Event()

    # 用途：模拟一直等待的远端检索，记录是否收到取消。
    # 参数：question 为检索词；knowledge_base_ids 为绑定多库范围；top_k 为合计上限。
    # 返回：不正常返回，关闭客户端时收到取消。
    async def blocked_retrieve(question, knowledge_base_ids, top_k):
        try:
            await asyncio.Event().wait()
        finally:
            retrieval_cancelled.set()

    service.retrieve = blocked_retrieve

    # 用途：读取工具开始事件后关闭客户端流，等待取消传播到工具。
    # 参数：无（读取 service 和 retrieval_cancelled）。返回：无。
    async def close_early():
        stream = service.answer_stream("问题", [KB_ONE, KB_TWO], 2)
        assert (await anext(stream))[0] == "status"
        assert (await anext(stream))[0] == "tool_start"
        await stream.aclose()
        await asyncio.wait_for(retrieval_cancelled.wait(), timeout=1)

    asyncio.run(close_early())
    assert runs[0].cancelled
    assert runs[0].stopped.is_set()


# 用途：验证模型即使跳过必需工具，也不能直接把未检索的内容当成答案。
# 参数：monkeypatch 为 pytest 替换工具。返回：无。
def test_missing_tool_call_is_an_error(monkeypatch):
    service, runs = mock_service(monkeypatch, call_tool=False)

    # 用途：收集未执行检索时的流事件。
    # 参数：无（读取 service）。返回：事件列表。
    async def collect():
        return [event async for event in service.answer_stream("问题", [KB_ONE, KB_TWO], 2)]

    events = asyncio.run(collect())
    assert [name for name, _ in events] == ["status", "error"]
    assert "未调用知识库检索工具" in events[-1][1]["message"]
    assert runs[0].cancelled


# 用途：验证 HTTP SSE 帧格式、Unicode 内容、禁用缓冲头以及流内异常处理。
# 参数：monkeypatch 为 pytest 替换工具。返回：无。
def test_sse_endpoint_encodes_events_and_errors(monkeypatch):
    # 用途：模拟服务先推送状态，随后发生异常。
    # 参数：question 为问题；knowledge_base_ids 为多库范围；top_k 为合计上限；
    # knowledge_bases 为路由预校验的名称快照。
    # 产出：一个状态事件，随后抛出异常。
    async def failing_stream(question, knowledge_base_ids, top_k, *, knowledge_bases=None):
        yield "status", {"message": "正在检索"}
        raise DomainError(502, "MODEL_FAILED", "模型不可用", True)

    service = SimpleNamespace(answer_stream=failing_stream, resolve_scope=AsyncMock(return_value=SCOPE))

    # 用途：让 HTTP 路由读取当前测试服务。
    # 参数：无。返回：含模拟流方法的服务对象。
    def get_service():
        return service

    monkeypatch.setattr(app_module, "get_rag", get_service)
    monkeypatch.setattr(app_module.settings, "openai_api_key", "test-key")
    client = TestClient(app_module.app)
    response = client.post("/api/chat/stream", json={"question": "问题", "knowledge_base_ids": [KB_ONE, KB_TWO]})
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-accel-buffering"] == "no"
    assert 'event: status\ndata: {"message": "正在检索"}\n\n' in response.text
    assert "event: error\n" in response.text
    assert "模型不可用" in response.text
    assert "event: done" not in response.text


# 用途：验证原非流式接口使用同一工具流程并保留引用模型类型。
# 参数：monkeypatch 为 pytest 替换工具。返回：无。
def test_nonstream_answer_uses_same_tool_pipeline(monkeypatch):
    service, runs = mock_service(monkeypatch)
    response = asyncio.run(service.answer("问题", [KB_ONE, KB_TWO], 2))
    assert response.answer == runs[0].final_output
    assert response.citations == [sample_chunk()]
    assert response.knowledge_bases == SCOPE
    service.retrieve.assert_awaited_once_with("服务时间", [KB_ONE, KB_TWO], 2)
