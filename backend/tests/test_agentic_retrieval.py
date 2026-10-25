"""检索的请求预算、来源边界、真实模型跨度与证据引用契约。"""

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from langchain_core.documents import Document

from app.agents.composer import ResponseComposer
from app.agents.evidence import EvidenceRegistry, normalize_answer
from app.agents.factory import AgentFactory
from app.core.observability import observation_scope, observed_tools
from app.retrieval.external.planned import PlannedKnowledgeRetriever, RetrievalSession
from app.retrieval.local.service import LocalRetrievalService
from app.schemas.agent import AgentAnswer, AgentResult
from app.schemas.enums import AgentName, IntentName, QueryStrategy, RetrievalSource, RunStatus
from app.schemas.retrieval import QueryPlan, RetrievalRequest
from app.tools.agentic_retrieval import create_agentic_retrieval_tool
from app.tools.document_reader import DocumentReaderService, DocumentReadRequest
from app.tools.document_reader_tool import create_document_reader_tool
from tests.test_streaming import QUERY, Observer, OfflineModel, output_call


def doc(text="政策正文", chunk=None, **meta):
    return Document(
        page_content=text,
        metadata={
            "title": "手册",
            "filename": "手册.pdf",
            "chunk_id": str(chunk or uuid4()),
            "page_numbers": [2, 3],
            **meta,
        },
    )


def plan(query="转专业", **fields):
    return QueryPlan(
        strategy=QueryStrategy.ORIGINAL,
        retrieval_query=query,
        selected_sources=[RetrievalSource.LOCAL_KNOWLEDGE],
        **fields,
    )


def request(agent=AgentName.CAMPUS):
    return RetrievalRequest(
        query="转专业", conversation_id=uuid4(), agent_name=agent, intent=IntentName.CAMPUS_AFFAIRS
    )


def setup(dense=None, bm25=None, **options):
    dense = dense or AsyncMock(return_value=[doc()])
    bm25 = bm25 or Mock(return_value=[])
    local = LocalRetrievalService(
        SimpleNamespace(dense_search=dense), SimpleNamespace(search=bm25), rrf_candidate_k=10
    )
    web = SimpleNamespace(retrieve_sources=AsyncMock(return_value=[]))
    selector = SimpleNamespace(select=Mock(return_value=[]))
    retriever = PlannedKnowledgeRetriever(
        local, web, selector, **options
    )
    return retriever, dense, bm25


@pytest.mark.asyncio
async def test_budget_cache_empty_results_and_failures_across_calls():
    dense = AsyncMock(side_effect=[[], OSError("失败"), [], []])
    engine, _, lexical = setup(dense=dense)
    session, req = RetrievalSession(), request()
    first = await engine.retrieve_with_diagnostics(req, plan=plan(), session=session)
    cached = await engine.retrieve_with_diagnostics(req, plan=plan(), session=session)
    assert first.diagnostics.evidence_sufficient is None
    assert cached.diagnostics.rounds == 0 and session.rounds == 1
    fail = await engine.retrieve_with_diagnostics(req, plan=plan("新查询"), session=session)
    assert fail.diagnostics.errors and session.rounds == 2
    retry = await engine.retrieve_with_diagnostics(req, plan=plan("新查询"), session=session)
    assert [e.status for e in retry.diagnostics.query_executions] == ["cached", "executed"]
    assert session.rounds == 3
    blocked = await engine.retrieve_with_diagnostics(req, plan=plan("第四批"), session=session)
    assert blocked.diagnostics.stop_reason == "max_rounds" and blocked.diagnostics.rounds == 0
    assert dense.await_count == 3 and lexical.call_count == 2
    await engine.retrieve_with_diagnostics(req, plan=plan(), session=RetrievalSession())
    assert dense.await_count == 4  # 新 Subagent 不共享缓存


@pytest.mark.asyncio
async def test_multi_query_one_batch_and_hyde_uses_distinct_dense_input():
    engine, dense, bm25 = setup()
    session, req = RetrievalSession(), request()
    multi = QueryPlan(
        strategy="multi_query",
        retrieval_query="转专业",
        generated_queries=["流程", "条件"],
        selected_sources=["local_knowledge"],
    )
    await engine.retrieve_with_diagnostics(req, plan=multi, session=session)
    assert session.rounds == 1 and dense.await_count == bm25.call_count == 3
    hyde = QueryPlan(
        strategy="hyde",
        retrieval_query="转专业",
        hypothetical_document="申请说明假设",
        selected_sources=["local_knowledge"],
    )
    await engine.retrieve_with_diagnostics(req, plan=hyde, session=session)
    assert session.rounds == 2 and bm25.call_count == 3
    assert dense.await_args.args[0] == "申请说明假设"


@pytest.mark.asyncio
async def test_timeout_and_cancellation_keep_started_batches_and_successful_route_cache():
    gate = asyncio.Event()

    async def wait(*args):
        await gate.wait()
        return []

    engine, _, bm25 = setup(dense=AsyncMock(side_effect=wait), source_timeout_seconds=0.01)
    session = RetrievalSession()
    sink = SimpleNamespace(event=AsyncMock())
    async with observation_scope("offline", sink) as observation:
        output = await engine.retrieve_with_diagnostics(request(), plan=plan(), session=session)
    assert output.diagnostics.errors and session.rounds == 1 and session.local_cache
    assert sum(e["event_type"] == "retrieval.round.started" for e in observation.events) == 1
    engine.source_timeout = 10
    task = asyncio.create_task(
        engine.retrieve_with_diagnostics(request(), plan=plan(), session=session)
    )
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert session.rounds == 2 and bm25.call_count == 1


@pytest.mark.asyncio
async def test_invalid_or_unauthorized_plan_does_not_execute_sources():
    engine, dense, bm25 = setup()
    engine.selector.select.side_effect = ValueError("越权官网")
    bad = QueryPlan(
        strategy="original",
        retrieval_query="通知",
        selected_sources=["local_knowledge", "fixed_web_sources"],
        web_source_keys=["not_allowed"],
    )
    session = RetrievalSession()
    with pytest.raises(ValueError, match="越权"):
        await engine.retrieve_with_diagnostics(request(), plan=bad, session=session)
    assert session.rounds == 0
    dense.assert_not_awaited()
    bm25.assert_not_called()


@pytest.mark.asyncio
async def test_web_failure_is_retriable_and_successful_empty_result_is_cached():
    engine, _, _ = setup()
    engine.selector.select.return_value = ["allowed"]
    engine.web.retrieve_sources.side_effect = [OSError("源失败"), []]
    selected = QueryPlan(
        strategy="original",
        retrieval_query="招聘",
        selected_sources=["fixed_web_sources"],
        web_source_keys=["allowed"],
    )
    session = RetrievalSession()
    failed = await engine.retrieve_with_diagnostics(request(), plan=selected, session=session)
    assert failed.diagnostics.errors and not session.source_cache
    await engine.retrieve_with_diagnostics(request(), plan=selected, session=session)
    cached = await engine.retrieve_with_diagnostics(request(), plan=selected, session=session)
    assert cached.diagnostics.rounds == 0 and session.rounds == 2
    assert engine.web.retrieve_sources.await_count == 2


@pytest.mark.asyncio
async def test_four_retrieval_tool_calls_respect_three_batches_and_total_tool_budget():
    engine, _, _ = setup()
    registry = EvidenceRegistry("CampusAgent")
    tool = create_agentic_retrieval_tool(
        engine, uuid4(), AgentName.CAMPUS, IntentName.CAMPUS_AFFAIRS, registry
    )
    responses = [
        output_call(
            "agentic_retrieval",
            {"query": str(i), "plan": plan(str(i)).model_dump(mode="json")},
            str(i),
        )
        for i in range(5)
    ]
    responses.append(
        output_call(
            "AgentAnswer",
            AgentAnswer(answer="已有证据可回答。", evidence_sufficient=True).model_dump(),
        )
    )
    model = OfflineModel(responses=responses)
    factory = AgentFactory(model, SimpleNamespace(assemble=Mock(return_value="offline")))
    async with observation_scope("offline", SimpleNamespace(event=AsyncMock())) as observation:
        await factory.create(
            AgentName.CAMPUS,
            observed_tools([tool], "CampusAgent"),
            "",
            "",
            [],
            [],
            [],
            evidence_registry=registry,
        ).ainvoke(QUERY)
    assert len(observation.tool_calls) == 4
    assert sum(e["event_type"] == "retrieval.round.started" for e in observation.events) == 3
    assert observation.tool_calls[-1]["artifact"]["diagnostics"]["stop_reason"] == "max_rounds"


@pytest.mark.asyncio
async def test_rrf_output_order_limit_and_successful_query_cache_are_preserved():
    hits = [doc(chunk=str(index)) for index in range(20)]
    engine, dense, lexical = setup(dense=AsyncMock(return_value=hits))
    session = RetrievalSession()
    req = request()
    first = await engine.retrieve_with_diagnostics(req, plan=plan(), session=session)
    cached = await engine.retrieve_with_diagnostics(req, plan=plan(), session=session)
    assert [item.metadata["chunk_id"] for item in first.documents] == [str(i) for i in range(10)]
    assert all("rrf_score" in item.metadata for item in first.documents)
    assert cached.documents == first.documents
    assert cached.diagnostics.rounds == 0 and session.rounds == 1
    dense.assert_awaited_once()
    lexical.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("agent", list(AgentName))
@pytest.mark.parametrize("strategy", list(QueryStrategy))
async def test_three_agents_four_strategies_have_two_model_calls(agent, strategy):
    engine, _, _ = setup()
    registry = EvidenceRegistry(agent.value)
    query_plan = QueryPlan(
        strategy=strategy,
        retrieval_query="政策",
        generated_queries=["流程"] if strategy == QueryStrategy.MULTI_QUERY else [],
        hypothetical_document="假设政策描述" if strategy == QueryStrategy.HYDE else None,
        selected_sources=["local_knowledge"],
    )
    tool = create_agentic_retrieval_tool(
        engine, uuid4(), agent, IntentName.CAMPUS_AFFAIRS, registry
    )
    model = OfflineModel(
        responses=[
            output_call(
                "agentic_retrieval",
                {"query": "政策", "plan": query_plan.model_dump(mode="json")},
                "read",
            ),
            output_call(
                "AgentAnswer",
                AgentAnswer(
                    answer=f"政策[[e:{agent.value}:1]]", evidence_sufficient=True
                ).model_dump(),
            ),
        ]
    )
    factory = AgentFactory(model, SimpleNamespace(assemble=Mock(return_value="offline")))
    async with observation_scope("offline", SimpleNamespace(event=AsyncMock())) as observation:
        result = await factory.create(
            agent,
            observed_tools([tool], agent.value),
            "",
            "",
            [],
            [],
            [],
            evidence_registry=registry,
        ).ainvoke(QUERY, observer=Observer())
    assert model.step == 2 and result.answer == "政策[1]" and len(result.citations) == 1
    completed = [
        e["payload"]
        for e in observation.events
        if e["event_type"] == "phase.completed" and e["payload"]["node"] == "agent.model"
    ]
    assert len(completed) == 2 and all(e["duration_ms"] > 0 for e in completed)
    assert completed[0]["actions"][0]["name"] == "agentic_retrieval"
    assert completed[1]["actions"][0]["action"] == "answer"


def test_identity_numbering_unknown_markers_and_tail_cleanup():
    registry = EvidenceRegistry("CampusAgent")
    first, second = doc("内容一"), doc("内容二")
    registered = registry.register([first, second, first])
    assert registered[0].metadata["evidence_id"] == registered[2].metadata["evidence_id"]
    text = (
        "先引用[[e:CampusAgent:2]]再引用[[e:CampusAgent:1]]重复[[e:CampusAgent:2]]"
        "未知[[e:CampusAgent:99]]。\n\n参考来源：\n[1] 模型写错的目录"
    )
    final = normalize_answer(text, registry.citations)
    assert final.content == "先引用[1]再引用[2]重复[1]未知。"
    assert [str(c.chunk_id) for c in final.citations] == [
        second.metadata["chunk_id"],
        first.metadata["chunk_id"],
    ]
    assert final.metadata["invalid_evidence_ids"] == ["CampusAgent:99"]
    assert final.citations[0].page_numbers == [2, 3]


def test_fragmented_stream_never_leaks_internal_marker_or_changes_known_numbers():
    registry = EvidenceRegistry("LearningAgent")
    registry.register([doc()])
    raw = "依据[[e:LearningAgent:1]]结束"
    previews = [
        normalize_answer(raw[:end], registry.citations, preview=True).content
        for end in range(len(raw) + 1)
    ]
    assert all("[[" not in value and "e:Learning" not in value for value in previews)
    assert previews[-1] == "依据[1]结束"


def test_web_fragments_with_same_url_and_prefix_keep_distinct_evidence():
    from app.retrieval.deduplication import deduplicate_documents

    meta = {"source_type": "web", "source_url": "https://example.org/policy", "title": "同一网页"}
    documents = [
        Document(page_content="共同前缀" * 500 + tail, metadata=meta)
        for tail in ["片段一", "片段二"]
    ]
    assert len(deduplicate_documents(documents)) == 2
    registry = EvidenceRegistry("CampusAgent")
    registry.register(documents)
    final = normalize_answer("一[[e:CampusAgent:1]]二[[e:CampusAgent:2]]", registry.citations)
    assert len(final.citations) == 2 and final.content == "一[1]二[2]"


def test_markdown_code_links_and_unrelated_brackets_are_preserved():
    text = "`[1]` [链接](https://example.org) [普通方括号]\n```text\n[99]\n```"
    final = normalize_answer(text, {})
    assert final.content == text and "citation_warning" not in final.metadata


def test_composer_fallback_reindexes_both_agents_and_deduplicates_same_chunk():
    shared = doc()
    campus, learning = EvidenceRegistry("CampusAgent"), EvidenceRegistry("LearningAgent")
    campus.register([shared])
    learning.register([doc("独有"), shared])
    results = [
        AgentResult(
            agent_name=AgentName.CAMPUS,
            answer="已编号[1]",
            evidence_answer="校园[[e:CampusAgent:1]]",
            evidence=campus.citations,
        ),
        AgentResult(
            agent_name=AgentName.LEARNING,
            answer="已编号[1][2]",
            evidence_answer="学习[[e:LearningAgent:1]][[e:LearningAgent:2]]",
            evidence=learning.citations,
        ),
    ]
    final = ResponseComposer.fallback(results)
    assert "校园[1]" in final.content and "学习[2][1]" in final.content
    assert len(final.citations) == 2 and final.status == RunStatus.SUCCESS


@pytest.mark.asyncio
async def test_upload_evidence_is_only_the_delivered_file_slice(tmp_path):
    first, second = tmp_path / "one.txt", tmp_path / "two.txt"
    first.write_text("第一份资料", encoding="utf-8")
    second.write_text("第二份资料", encoding="utf-8")
    service = DocumentReaderService(
        SimpleNamespace(resolve=lambda key: {"one": first, "two": second}[key]),
        max_output_characters=len("[one.txt]\n第一份资料"),
    )
    request_data = DocumentReadRequest(file_ids=["one", "two"], mode="full")
    result = service.read(request_data)
    assert result.truncated
    assert all(segment.metadata["file_id"] == "one" for segment in result.segments)
    request_data.continuation_token = result.continuation_token
    rest = service.read(request_data)
    assert any(s.metadata["file_id"] == "two" for s in rest.segments)
    for segment in [*result.segments, *rest.segments]:
        other = "第二份资料" if segment.metadata["file_id"] == "one" else "第一份资料"
        assert other not in segment.content
    tool = create_document_reader_tool(service, EvidenceRegistry("LearningAgent"))
    _, docs = await tool.coroutine(file_ids=["one", "two"], mode="full")
    assert all(d.metadata["file_id"] == "one" for d in docs)


@pytest.mark.asyncio
async def test_composer_success_keeps_namespaced_evidence_and_marks_unknown_without_retry():
    from pathlib import Path

    from app.schemas.agent import ComposedAnswer

    left, right = EvidenceRegistry("CampusAgent"), EvidenceRegistry("LearningAgent")
    left.register([doc("校园")])
    right.register([doc("学习")])
    results = [
        AgentResult(
            agent_name=AgentName.CAMPUS,
            answer="校园[1]",
            evidence_answer="校园[[e:CampusAgent:1]]",
            evidence=left.citations,
        ),
        AgentResult(
            agent_name=AgentName.LEARNING,
            answer="学习[1]",
            evidence_answer="学习[[e:LearningAgent:1]]",
            evidence=right.citations,
        ),
    ]
    model = OfflineModel(
        responses=[
            output_call(
                "ComposedAnswer",
                ComposedAnswer(
                    content="学习[[e:LearningAgent:1]]，校园[[e:CampusAgent:1]]，未知[[e:LearningAgent:9]]。"
                ).model_dump(),
            )
        ]
    )
    composer = ResponseComposer(
        model, Path(__file__).parents[1] / "app/agents/prompts/response_composer.md"
    )
    observer = Observer()
    result = await composer.compose(results, observer=observer)
    assert model.step == 1 and result.content == "学习[1]，校园[2]，未知。"
    assert result.partial and result.status == RunStatus.PARTIAL
    assert len(result.citations) == 2 and all("[[e:" not in text for text in observer.texts)


@pytest.mark.asyncio
async def test_model_span_usage_is_recorded_once_without_model_completed_duplicate():
    final = output_call(
        "AgentAnswer", AgentAnswer(answer="答复", evidence_sufficient=True).model_dump()
    )
    final.usage_metadata = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
    model = OfflineModel(responses=[final])
    factory = AgentFactory(model, SimpleNamespace(assemble=Mock(return_value="offline")))
    async with observation_scope("offline", SimpleNamespace(event=AsyncMock())) as observation:
        await factory.create(AgentName.CAMPUS, [], "", "", [], [], []).ainvoke(QUERY)
    uses = [e["payload"]["usage"] for e in observation.events if e["payload"].get("usage")]
    assert len(uses) == 1 and uses[0]["total_tokens"] == 15
    assert not any(e["event_type"] == "model.completed" for e in observation.events)


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_UNIFIED_DB_TESTS") != "1", reason="显式启用隔离数据库验收")
async def test_agentic_metadata_and_trace_counts_round_trip_only_in_isolated_database():
    from app.api.routes.conversations import list_messages
    from app.db.models import User
    from app.db.repositories.conversations import ConversationRepository
    from app.db.repositories.traces import TraceRepository
    from app.schemas.agent import Citation
    from tests.test_conversation_database import isolated_database

    async with isolated_database() as (database, _):
        async with database.session() as session:
            user = User(username="d-citations-test", password_hash="unused", role="developer")
            session.add(user)
            await session.flush()
            user_id = user.user_id
        messages = ConversationRepository(database)
        conversation = await messages.create(str(user_id))
        metadata = {
            "render_citations": True,
            "citation_warning": "引用需核验",
            "invalid_evidence_ids": ["CampusAgent:9"],
        }
        await messages.add_message(
            str(conversation.conversation_id),
            "assistant",
            "事实[1]",
            display_metadata=metadata,
            citations=[Citation(citation_index=1, title="上传资料", page_numbers=[2, 3])],
        )
        await messages.add_message(
            str(conversation.conversation_id), "assistant", "历史[7]\n参考来源：[7] 历史原文"
        )
        restored = await list_messages(
            conversation.conversation_id,
            SimpleNamespace(user_id=user_id),
            SimpleNamespace(conversations=messages),
        )
        assert restored[0]["display_metadata"]["render_citations"] is True
        assert restored[0]["display_metadata"]["citation_warning"] == "引用需核验"
        assert restored[0]["citations"][0]["page_numbers"] == [2, 3]
        assert "render_citations" not in restored[1]["display_metadata"]
        assert restored[1]["content"] == "历史[7]\n参考来源：[7] 历史原文"
        traces = TraceRepository(database)
        run_id = str(uuid4())
        await traces.start(run_id, str(user_id), str(conversation.conversation_id))
        await traces.append_event(run_id, "phase.started", {"node": "agent.model"})
        await traces.append_event(
            run_id,
            "phase.completed",
            {
                "node": "agent.model",
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
        )
        await traces.append_event(run_id, "retrieval.plan.selected", {"protocol": "agentic"})
        await traces.append_event(
            run_id, "retrieval.round.started", {"protocol": "agentic", "round_number": 1}
        )
        await traces.finish(run_id, "success", {})
        row = await traces.get_request(run_id)
        assert row.total_tokens == 15 and row.retrieval_rounds == 1
        zero_id = str(uuid4())
        await traces.start(zero_id, str(user_id), str(conversation.conversation_id))
        await traces.append_event(zero_id, "retrieval.plan.selected", {"protocol": "agentic"})
        await traces.finish(zero_id, "partial", {})
        assert (await traces.get_request(zero_id)).retrieval_rounds == 0
