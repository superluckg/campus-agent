"""离线流式契约测试：首字、内部输出隔离、工具额度、降级与终止。"""

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.tools import StructuredTool
from langgraph.errors import GraphRecursionError

from app.agents.composer import ResponseComposer
from app.agents.factory import AgentFactory
from app.api.dependencies import workspace_user, get_container
from app.api.routes.conversations import router
from app.api.schemas import WorkspaceIdentity
from app.core.response_stream import ChatRunContext, StructuredTextStream
from app.graph.builder import GlobalGraphDependencies, build_global_graph
from app.schemas.agent import AgentAnswer, AgentAssignment, AgentResult, ComposedAnswer, FinalResponse, RoutePlan
from app.schemas.enums import AgentName, IntentName
from app.schemas.events import SSEEvent
from app.schemas.intent import IntentResult
from app.services.chat_service import ChatRunService
from app.services.direct_reply import DirectReplyService
from app.services.response_stream import ResponseStreamPublisher
from app.services.trace_service import CompositeTraceSink
from app.skills.models import SelectedSkillResult


PROMPTS = Path(__file__).resolve().parents[1] / "app"
QUERY = [{"role": "user", "content": "测试"}]


class OfflineModel(BaseChatModel):
    responses: list[AIMessage]
    step: int = 0
    gate: Any = None
    fail_stream: bool = False

    @property
    def _llm_type(self):
        return "offline-stream-test"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        message = self.responses[min(self.step, len(self.responses) - 1)]
        self.step += 1
        return ChatResult(generations=[ChatGeneration(message=message)])

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        message = self._generate(messages).generations[0].message
        identity = "offline-%d" % self.step
        # 模型的准备文字不得成为正文。
        yield ChatGenerationChunk(message=AIMessageChunk(id=identity, content="内部准备文字"))
        for index, call in enumerate(message.tool_calls):
            raw = json.dumps(call["args"], ensure_ascii=True)
            for offset in range(0, len(raw), 5):
                yield ChatGenerationChunk(message=AIMessageChunk(
                    id=identity, content="", tool_call_chunks=[{
                        "index": index, "id": call["id"] if offset == 0 else None,
                        "name": call["name"] if offset == 0 else None,
                        "args": raw[offset:offset + 5],
                    }],
                ))
                if offset >= len(raw) // 2 and self.gate is not None:
                    await self.gate.wait()
                if offset >= len(raw) // 2 and self.fail_stream:
                    raise OSError("模拟连接失败")
        yield ChatGenerationChunk(message=AIMessageChunk(id=identity, content=""))


def output_call(name, payload, call_id="final"):
    return AIMessage(content="", tool_calls=[{"name": name, "args": payload, "id": call_id}])


def agent_payload(answer="这是依据资料生成的中文回答。"):
    return AgentAnswer(answer=answer, evidence_sufficient=True).model_dump(mode="json")


class Observer:
    def __init__(self):
        self.texts = []
        self.seen = asyncio.Event()

    async def update(self, content):
        self.texts.append(content)
        if content:
            self.seen.set()


class MemoryBus:
    def __init__(self):
        self.events = []

    async def publish(self, request_id, event_type, data, **kwargs):
        event = SSEEvent(
            event_id=len(self.events) + 1, request_id=request_id,
            event_type=event_type, data=data,
        )
        self.events.append(event)
        return event


@pytest.mark.asyncio
async def test_agent_emits_before_completion_and_returns_structured_result():
    gate = asyncio.Event()
    observer = Observer()
    model = OfflineModel(
        responses=[output_call("AgentAnswer", agent_payload())], gate=gate,
    )
    factory = AgentFactory(model, SimpleNamespace(assemble=Mock(return_value="测试")))
    runner = factory.create(AgentName.LEARNING, [], "", "", [], [], [])
    task = asyncio.create_task(runner.ainvoke(QUERY, observer=observer))
    await asyncio.wait_for(observer.seen.wait(), 2)
    assert not task.done()
    assert all("内部" not in text for text in observer.texts)
    gate.set()
    result = await task
    assert result.answer == observer.texts[-1]
    assert result.status.value == "success"
    assert not result.tool_records


@pytest.mark.asyncio
async def test_final_schema_does_not_consume_business_tool_limit():
    execute = AsyncMock(return_value="计算完成")

    async def gpa(value: str) -> str:
        return await execute(value)

    tool = StructuredTool.from_function(
        coroutine=gpa, name="gpa_calculator", description="离线计算工具",
    )
    model = OfflineModel(responses=[
        output_call("gpa_calculator", {"value": "测试"}, "business"),
        output_call("AgentAnswer", agent_payload()),
    ])
    factory = AgentFactory(
        model, SimpleNamespace(assemble=Mock(return_value="测试")),
        max_tool_calls=1, max_same_tool_calls=1,
    )
    progress = AsyncMock()
    result = await factory.create(AgentName.LEARNING, [tool], "", "", [], [], []).ainvoke(
        QUERY, observer=Observer(), on_progress=progress,
    )
    execute.assert_awaited_once()
    assert result.status.value == "success"
    assert len(result.tool_records) == 1
    assert [call.args[0] for call in progress.await_args_list] == ["tool.started", "tool.completed"]


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("limit", [12, 50])
async def test_career_agent_finishes_after_two_tools_with_configured_graph_limit(streaming, limit):
    execute = AsyncMock(return_value="离线测试资料")

    async def retrieve(query: str) -> str:
        return await execute(query)

    tools = [
        StructuredTool.from_function(coroutine=retrieve, name=name, description="离线工具")
        for name in ["fixed_web_search", "agentic_retrieval", "document_reader"]
    ]
    answer = "两次工具调用完成后的最终回答。"
    model = OfflineModel(responses=[
        output_call("fixed_web_search", {"query": "招聘会"}, "web"),
        output_call("agentic_retrieval", {"query": "招聘会"}, "local"),
        output_call("AgentAnswer", AgentAnswer(
            evidence_sufficient=True, answer=answer,
        ).model_dump(mode="json")),
    ])
    factory = AgentFactory(
        model, SimpleNamespace(assemble=Mock(return_value="测试")), recursion_limit=limit,
    )
    runner = factory.create(AgentName.CAREER, tools, "", "", [], [], [])
    observer = Observer() if streaming else None
    if limit == 12:
        with pytest.raises(GraphRecursionError):
            await runner.ainvoke(QUERY, observer=observer)
        assert model.step == 2
    else:
        result = await runner.ainvoke(QUERY, observer=observer)
        assert model.step == 3
        assert result.answer == answer
        assert result.status.value == "success"
        assert len(result.tool_records) == 2
        if observer:
            assert observer.texts[-1] == answer
    assert execute.await_count == 2


def test_structured_fragments_interleave_without_leaking_tools_or_escapes():
    parser = StructuredTextStream("AgentAnswer", "answer")
    expected = '中文\n引号"与反斜杠\\，emoji 😀'
    raw = json.dumps({"answer": expected}, ensure_ascii=True)
    snapshots = []
    parser.feed(AIMessageChunk(content="内部分析"))
    parser.feed(AIMessageChunk(content="", id="message", tool_call_chunks=[
        {"index": 1, "id": "tool", "name": "document_reader", "args": '{"secret":"不要展示"}'},
    ]))
    for index, char in enumerate(raw):
        value = parser.feed(AIMessageChunk(content="", id="message", tool_call_chunks=[
            {"index": 0, "id": "answer" if index == 0 else None,
             "name": "AgentAnswer" if index == 0 else None, "args": char},
        ]))
        if value is not None:
            snapshots.append(value)
    assert all(not any(0xD800 <= ord(char) <= 0xDFFF for char in value) for value in snapshots)
    assert snapshots[-1] == expected
    assert len(set(snapshots)) > 2
    assert all("secret" not in text and "不要展示" not in text for text in snapshots)


@pytest.mark.asyncio
async def test_direct_reply_streams_and_retains_usage():
    gate = asyncio.Event()

    async def chunks(*args, **kwargs):
        yield AIMessageChunk(content="你")
        await gate.wait()
        yield AIMessageChunk(content="好！", usage_metadata={
            "input_tokens": 2, "output_tokens": 3, "total_tokens": 5,
        })

    model = SimpleNamespace(astream=chunks)
    service = DirectReplyService(model, PROMPTS / "services/prompts", "offline")
    observer = Observer()
    task = asyncio.create_task(service.reply(
        "你好", IntentName.SMALL_TALK, [], "", observer=observer,
    ))
    await asyncio.wait_for(observer.seen.wait(), 2)
    assert not task.done()
    gate.set()
    result = await task
    assert result.content == "你好！"
    assert result.usage["total_tokens"] == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", [False, True])
async def test_composer_extracts_raw_content_and_replaces_failed_preview(failed):
    results = [
        AgentResult(agent_name=AgentName.LEARNING, answer="学习方面"),
        AgentResult(agent_name=AgentName.CAMPUS, answer="校园方面"),
    ]
    final = ComposedAnswer(content="合并后的统一答案。")
    model = OfflineModel(
        responses=[output_call("ComposedAnswer", final.model_dump(mode="json"))],
        fail_stream=failed,
    )
    observer = Observer()
    composer = ResponseComposer(model, PROMPTS / "agents/prompts/response_composer.md")
    result = await composer.compose(results, observer=observer)
    assert observer.texts[-1] == result.content
    assert any("合并" in text for text in observer.texts)
    assert "内部准备" not in result.content
    if failed:
        assert result.content.startswith("### LearningAgent")
    else:
        assert result.content == final.content


@pytest.mark.asyncio
async def test_publisher_flushes_first_text_timer_and_replacement_without_late_events():
    bus = MemoryBus()
    publisher = ResponseStreamPublisher(bus, str(uuid4()), interval_seconds=.01)
    await publisher.start()
    await publisher.update("首")
    assert bus.events[-1].data["delta"] == "首"
    await publisher.update("首段")
    await asyncio.sleep(.025)
    assert bus.events[-1].data["delta"] == "段"
    await publisher.update("纠正后的正文")
    assert bus.events[-1].event_type == "response.replace"
    await publisher.update("纠正后的正文。")
    await publisher.close()
    count = len(bus.events)
    await asyncio.sleep(.025)
    assert len(bus.events) == count
    assert publisher.metrics()["first_text_ms"] is not None


def initial_state():
    return {
        "request_id": str(uuid4()), "user_id": str(uuid4()),
        "conversation_id": str(uuid4()), "query": "测试",
        "recent_messages": [], "conversation_summary": "", "uploaded_files": [],
        "intent_result": None, "route_plan": None, "recalled_memories": [],
        "selected_skills": {}, "agent_results": {}, "final_response": None,
        "citations": [], "errors": [], "trace_context": {},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("agents", [1, 2])
async def test_global_graph_streams_only_single_or_composed_answer(agents):
    assignments = [
        AgentAssignment(agent_name=name, focus="测试")
        for name in [AgentName.LEARNING, AgentName.CAMPUS][:agents]
    ]
    seen_observers = []

    async def execute(assignment, observer=None, **kwargs):
        seen_observers.append(observer)
        answer = "子答案：" + assignment.agent_name.value
        if observer:
            await observer.update(answer)
        return AgentResult(agent_name=assignment.agent_name, answer=answer)

    async def compose(results, observer=None):
        content = results[0].answer if agents == 1 else "统一答案"
        if observer:
            await observer.update(content)
        return FinalResponse(content=content, agents=[item.agent_name for item in results])

    dependencies = GlobalGraphDependencies(
        context_loader=SimpleNamespace(
            load_recent=AsyncMock(return_value=[]), load_summary=AsyncMock(return_value=""),
            load_uploaded_files=AsyncMock(return_value=[]),
        ),
        intent_service=SimpleNamespace(recognize=AsyncMock(return_value=IntentResult(
            primary_intent=IntentName.LEARNING_PLAN, confidence=1,
        ))),
        router=SimpleNamespace(route=Mock(return_value=RoutePlan(route="agents", assignments=assignments))),
        memory_service=SimpleNamespace(recall=AsyncMock(return_value=[])),
        skill_manager=SimpleNamespace(select=Mock(return_value=SelectedSkillResult())),
        skill_score_service=SimpleNamespace(scores=AsyncMock(return_value={})),
        agent_service=SimpleNamespace(execute=execute),
        composer=SimpleNamespace(compose=compose),
        trace=SimpleNamespace(event=AsyncMock()),
    )
    observer = Observer()
    result = await build_global_graph(dependencies).ainvoke(
        initial_state(), context=ChatRunContext(observer=observer),
    )
    assert observer.texts[-1] == result["final_response"].content
    if agents == 2:
        assert seen_observers == [None, None]
        assert all("子答案" not in text for text in observer.texts)


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", [False, True])
async def test_chat_terminal_follows_persistence_and_deltas_do_not_write_trace(failed):
    bus = MemoryBus()
    traces = SimpleNamespace(
        start=AsyncMock(return_value=SimpleNamespace(trace_id=uuid4())),
        finish=AsyncMock(), append_event=AsyncMock(),
    )
    sink = CompositeTraceSink(traces, bus)

    async def invoke(state, context):
        await context.observer.update("生成中的正文")
        if failed:
            raise OSError("内部错误信息不能泄露")
        return {**state, "final_response": FinalResponse(content="最终正文")}

    async def persist(*args, **kwargs):
        assert not any(item.event_type == "response.completed" for item in bus.events)
        return SimpleNamespace(message_id=uuid4())

    @asynccontextmanager
    async def lock(*args):
        yield True

    conversations = SimpleNamespace(add_message=AsyncMock(side_effect=persist))
    memory = SimpleNamespace(maintain_after_turn=AsyncMock())
    cache = SimpleNamespace(append=AsyncMock())
    service = ChatRunService(
        SimpleNamespace(ainvoke=invoke), conversations, traces, sink,
        SimpleNamespace(conversation_lock=lock), memory, cache,
    )
    await service.start(str(uuid4()), str(uuid4()), "问题")
    while service.tasks:
        await asyncio.gather(*list(service.tasks))
    event_types = [item.event_type for item in bus.events]
    assert "response.delta" in event_types
    terminal = [name for name in event_types if name in {"response.completed", "request.failed"}]
    assert terminal == ["request.failed" if failed else "response.completed"]
    sql_events = [call.args[1] for call in traces.append_event.await_args_list]
    assert "response.delta" not in sql_events and "response.replace" not in sql_events
    if failed:
        assert conversations.add_message.await_count == 1
        memory.maintain_after_turn.assert_not_awaited()
        assert "内部错误" not in bus.events[-1].data["message"]
    else:
        assert conversations.add_message.await_count == 2
        memory.maintain_after_turn.assert_awaited_once()


def test_expired_and_foreign_streams_end_with_http_error():
    user = WorkspaceIdentity(user_id=str(uuid4()))
    conversation_id, request_id = str(uuid4()), str(uuid4())
    trace = SimpleNamespace(user_id=user.user_id, conversation_id=conversation_id)
    container = SimpleNamespace(
        conversations=SimpleNamespace(get_owned=AsyncMock(return_value=object())),
        traces=SimpleNamespace(get_request=AsyncMock(return_value=trace)),
        event_bus=SimpleNamespace(latest=AsyncMock(return_value=None), terminal=AsyncMock(return_value=None)),
    )
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[workspace_user] = lambda: user
    app.dependency_overrides[get_container] = lambda: container
    client = TestClient(app)
    url = "/api/conversations/%s/requests/%s/events" % (conversation_id, request_id)
    assert client.get(url).status_code == 410
    trace.user_id = str(uuid4())
    assert client.get(url).status_code == 404


def test_workspace_replay_includes_trace_events_and_ends_once():
    user = WorkspaceIdentity(user_id=str(uuid4()))
    conversation_id, request_id = str(uuid4()), str(uuid4())
    events = [
        SSEEvent(event_id=index + 1, request_id=request_id, event_type=kind,
                 data={"content": text})
        for index, (kind, text) in enumerate([
            ("internal.plan", "规划事件"), ("response.delta", "正文"),
            ("response.completed", "正文"),
        ])
    ]
    container = SimpleNamespace(
        conversations=SimpleNamespace(get_owned=AsyncMock(return_value=object())),
        traces=SimpleNamespace(get_request=AsyncMock(return_value=SimpleNamespace(
            user_id=user.user_id, conversation_id=conversation_id,
        ))),
        event_bus=SimpleNamespace(
            latest=AsyncMock(return_value=events[-1]), replay=AsyncMock(return_value=events),
            terminal=AsyncMock(return_value=events[-1]),
        ),
    )
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[workspace_user] = lambda: user
    app.dependency_overrides[get_container] = lambda: container
    response = TestClient(app).get(
        "/api/conversations/%s/requests/%s/events" % (conversation_id, request_id),
    )
    assert response.status_code == 200
    assert "规划事件" in response.text
    assert response.text.count("event: response.completed") == 1
    assert "id: 2" in response.text


def test_cached_event_transport_metadata_does_not_prevent_replay():
    from app.cache.event_bus import RedisEventBus

    payload = SSEEvent(
        event_id=1, request_id=uuid4(), event_type="response.delta", data={"delta": "正文"},
    ).model_dump(mode="json")
    payload["transport_metadata"] = {"source": "cache"}
    event = RedisEventBus._decode(("9-0", {"payload": json.dumps(payload)}))
    assert event.event_id == 9 and event.data == {"delta": "正文"}
    assert "transport_metadata" not in event.model_dump()


@pytest.mark.asyncio
async def test_missing_structured_response_cannot_expose_internal_model_text():
    from app.agents.factory import AgentRunner

    async def stream(*args, **kwargs):
        yield {"type": "updates", "data": {"model": {
            "messages": [AIMessage(content="内部检索规划，不能展示")]
        }}}

    observer = Observer()
    runner = AgentRunner(SimpleNamespace(astream=stream), AgentName.LEARNING)
    result = await runner.ainvoke(QUERY, observer=observer)
    assert "内部" not in result.answer
    assert observer.texts[-1] == result.answer
    assert result.status.value == "partial"


@pytest.mark.asyncio
async def test_clarification_streams_exact_question_without_model_call():
    dependencies = GlobalGraphDependencies(
        context_loader=SimpleNamespace(
            load_recent=AsyncMock(return_value=[]), load_summary=AsyncMock(return_value=""),
            load_uploaded_files=AsyncMock(return_value=[]),
        ),
        intent_service=SimpleNamespace(recognize=AsyncMock(return_value=IntentResult(
            primary_intent=IntentName.UNCLEAR, confidence=.2,
        ))),
        router=SimpleNamespace(route=Mock(return_value=RoutePlan(
            route="clarify", clarification_question="请说明具体需要什么帮助？",
        ))),
        memory_service=SimpleNamespace(recall=AsyncMock()),
        skill_manager=Mock(), skill_score_service=Mock(), agent_service=Mock(),
        composer=Mock(), trace=SimpleNamespace(event=AsyncMock()),
        direct_reply=Mock(),
    )
    observer = Observer()
    result = await build_global_graph(dependencies).ainvoke(
        initial_state(), context=ChatRunContext(observer=observer),
    )
    assert observer.texts == [result["final_response"].content]
    assert observer.texts[0] == "请说明具体需要什么帮助？"
    dependencies.direct_reply.reply.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_structured_preview_is_corrected_by_valid_retry():
    invalid = {"answer": "校验前的草稿。", "agent_name": "UnknownAgent"}
    valid = agent_payload("校验后的正式回答。")
    factory = AgentFactory(
        OfflineModel(responses=[
            output_call("AgentAnswer", invalid, "invalid"),
            output_call("AgentAnswer", valid, "valid"),
        ]),
        SimpleNamespace(assemble=Mock(return_value="测试")),
    )
    observer = Observer()
    result = await factory.create(AgentName.LEARNING, [], "", "", [], [], []).ainvoke(
        QUERY, observer=observer,
    )
    assert "校验前的草稿。" in observer.texts
    assert result.answer == observer.texts[-1] == valid["answer"]
    assert result.status.value == "success"


@pytest.mark.asyncio
async def test_agent_citations_are_validated_after_text_streaming():
    from langchain_core.documents import Document
    from app.agents.evidence import EvidenceRegistry

    registry = EvidenceRegistry("LearningAgent")
    document = Document(page_content="真实证据正文", metadata={"title": "真实资料", "filename": "source.md"})

    async def read(value: str):
        return "工具证据", registry.register([document])

    tool = StructuredTool.from_function(
        coroutine=read, name="document_reader", description="离线资料工具",
        response_format="content_and_artifact",
    )
    payload = agent_payload("资料说明[[e:LearningAgent:1]]。未知[[e:LearningAgent:99]]。")
    factory = AgentFactory(OfflineModel(responses=[
        output_call("document_reader", {"value": "source.md"}, "read"),
        output_call("AgentAnswer", payload),
    ]), SimpleNamespace(assemble=Mock(return_value="测试")))
    observer = Observer()
    result = await factory.create(AgentName.LEARNING, [tool], "", "", [], [], [],
                                  evidence_registry=registry).ainvoke(QUERY, observer=observer)
    assert result.answer == observer.texts[-1] == "资料说明[1]。未知。"
    assert result.status.value == "partial"
    assert all("[[e:" not in text for text in observer.texts)
    assert [(citation.citation_index, citation.filename) for citation in result.citations] == [(1, "source.md")]


@pytest.mark.asyncio
async def test_direct_reply_excludes_reasoning_blocks_from_preview_and_final():
    async def chunks(*args, **kwargs):
        yield AIMessageChunk(content=[
            {"type": "reasoning", "text": "隐藏的思考"},
            {"type": "text", "text": "公开正文"},
        ])

    model = SimpleNamespace(astream=chunks)
    observer = Observer()
    result = await DirectReplyService(model, PROMPTS / "services/prompts", "offline").reply(
        "你好", IntentName.SMALL_TALK, [], "", observer=observer,
    )
    assert result.content == observer.texts[-1] == "公开正文"
