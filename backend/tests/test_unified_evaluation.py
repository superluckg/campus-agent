"""标注协议、共享业务图隔离、真实跨度与证据组合的验收。"""

import asyncio
import copy
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.documents import Document
from langchain_core.tools import StructuredTool
from pydantic import ValidationError

from app.core.observability import observation_scope, span, observed_tools
from app.evaluation.unified_service import FixedInputs, UnifiedEvaluationService, UnifiedJudge
from app.evaluation.unified_scoring import parse_import, prepare_case, retrieval_scores, summarize
from app.evaluation.retrieval.scoring import serialize_unit, local_fingerprint
from app.schemas.unified_evaluation import UnifiedCase
from app.schemas.agent import FinalResponse, AgentResult
from app.schemas.intent import IntentResult
from app.graph.builder import GlobalGraphDependencies, build_global_graph
from app.routing.router import AgentRouter


def case_payload(intent="small_talk"):
    agent = {"campus_network": "CampusAgent", "campus_affairs": "CampusAgent",
             "career_recruitment": "CareerAgent", "learning_plan": "LearningAgent"}.get(intent)
    return {
        "query": "你好", "expected_intent": intent,
        "expected_behavior": "answer" if agent else "clarify" if intent == "unclear" else "small_talk",
        "reference_answer": "你好，有什么需要帮助的？", "evidence": [],
    }


def view(payload=None, number=1):
    return {
        "case_id": str(uuid4()),
        "case_number": number,
        "case_label": f"Case {number:03d}",
        "payload": payload or case_payload(),
    }


def corpus(docs=()):
    units = [serialize_unit(doc) for doc in docs]
    return {
        "units": units,
        "web_sources": {},
        "local_fingerprint": local_fingerprint(units),
        "automatic": True,
    }


def test_protocol_required_gold_excluded_intents_and_atomic_import():
    payload = case_payload()
    assert len(UnifiedCase.model_validate(payload).case_payload()) == 5
    for field in payload:
        invalid = copy.deepcopy(payload)
        del invalid[field]
        with pytest.raises(ValidationError):
            UnifiedCase.model_validate(invalid)
    for intent in ("learning_gpa", "learning_document", "career_resume"):
        invalid = copy.deepcopy(payload)
        invalid["expected_intent"] = intent
        with pytest.raises(ValidationError):
            UnifiedCase.model_validate(invalid)
    values, errors = parse_import(
        json.dumps(payload) + "\n" + "{invalid}" + "\n" + json.dumps(payload)
    )
    assert len(values) == 2 and errors[0]["line"] == 2
    invalid = copy.deepcopy(payload)
    invalid["expected_missing_fields"] = ["file_id"]
    with pytest.raises(ValidationError):
        UnifiedCase.model_validate(invalid)


def supported_case():
    payload = case_payload("campus_network")
    payload["query"] = "校园网如何开户、充值？"
    payload["evidence"] = [
        {
            "source_kind": "knowledge_file",
            "filename": "校园网手册.pdf",
            "content": text,
        }
        for key, text in [
            ("E01", "学校校园网开户后使用支付宝充值"),
            ("E02", "学校校园网需要先进行开户"),
            ("E03", "开户后可以使用支付宝充值"),
        ]
    ]
    payload = UnifiedCase.model_validate(payload).model_dump(mode="json")
    docs = [
        Document(
            id=key,
            page_content=item["content"],
            metadata={
                "chunk_id": key,
                "filename": "校园网手册.pdf",
                "document_id": key,
                "chunk_index": 0,
                "source_type": "local",
            },
        )
        for key, item in enumerate(payload["evidence"], 1)
    ]
    return payload, docs


def test_flat_evidence_and_multi_call_scoring():
    payload, docs = supported_case()
    prepared = prepare_case(view(payload), corpus(docs))

    def call(documents):
        return {"tool": "agentic_retrieval", "call_id": str(uuid4()), "agent": "CampusAgent",
                "inputs": {"query": "充值"}, "duration_ms": 17, "artifact": {"documents": documents}}

    scored = retrieval_scores(prepared, [call(docs)], {})
    assert scored["metrics"]["evidence_item_recall"] == 1
    assert scored["calls"][0]["k"] == 5
    assert scored["metrics"]["precision"] == .6
    assert "point_coverage" not in scored and "missing_tasks" not in scored
    scored = retrieval_scores(prepared, [call([docs[1]]), call([docs[2]])], {})
    assert len(scored["calls"]) == 2
    assert scored["metrics"]["unit_recall"] == pytest.approx(1 / 3)
    assert {d["evidence_ref"] for d in scored["evidence_details"] if d["obtained"]} == {"E02", "E03"}
    with pytest.raises(ValueError):
        prepare_case(view(payload), corpus())


def test_unanswerable_preflight_and_equal_case_weight():
    payload = case_payload("campus_network")
    payload["expected_behavior"] = "explain_limitation"
    prepared = prepare_case(
        view(UnifiedCase.model_validate(payload).model_dump(mode="json")), corpus()
    )
    assert prepared["matched_evidence"] == {}
    a, b = view(), view(number=3)

    def row(c, value):
        return {
            "case": c,
            "status": "success",
            "prediction": {"primary_intent": "small_talk"},
            "intent_metrics": {"intent_accuracy": value},
            "generation": {"metrics": {"correctness": value * 5}},
            "retrieval": {"metrics": {"hit": value}},
        }

    summary = summarize([row(a, 1), row(a, 1), row(a, 1), row(b, 0)], 4)
    assert summary["intent"]["intent_accuracy"] == 0.5
    assert summary["generation"]["correctness"] == 2.5
    cancelled = {**row(view(number=4), 0), "status": "cancelled", "error_code": "cancelled"}
    assert summarize([row(a, 1), cancelled], 2)["intent"]["intent_accuracy"] == 1


@pytest.mark.asyncio
async def test_parallel_spans_real_durations_snapshots_and_failures():
    sink = SimpleNamespace(event=AsyncMock())

    async def branch(name):
        async with span(name, name):
            await asyncio.sleep(0.01)

    async with observation_scope("request", sink) as observer:
        async with span("parent", "并行阶段"):
            await asyncio.gather(branch("a"), branch("b"))
        with pytest.raises(RuntimeError):
            async with span("broken", "失败阶段"):
                raise RuntimeError("明确原因")
    completed = [e["payload"] for e in observer.events if e["event_type"] == "phase.completed"]
    children = [s for s in completed if s["node"] in {"a", "b"}]
    assert children[0]["parent_span_id"] == children[1]["parent_span_id"]
    assert all(s["duration_ms"] >= 9 for s in children)
    assert observer.events[-1]["payload"]["error_message"] == "明确原因"


@pytest.mark.asyncio
async def test_wrapped_tools_preserve_schema_and_artifacts():
    from pydantic import BaseModel

    class Params(BaseModel):
        query: str

    async def run(query):
        await asyncio.sleep(0.01)
        return query, {"documents": [Document(page_content="实际证据", metadata={})]}

    tool = StructuredTool.from_function(
        coroutine=run,
        name="agentic_retrieval",
        description="模拟检索",
        args_schema=Params,
        response_format="content_and_artifact",
    )
    async with observation_scope("request", SimpleNamespace(event=AsyncMock())) as observer:
        wrapped = observed_tools([tool], "CampusAgent")[0]
        await wrapped.ainvoke(
            {"name": tool.name, "type": "tool_call", "id": "test", "args": {"query": "实际查询"}}
        )
    assert wrapped.args_schema is Params
    assert observer.tool_calls[0]["artifact"]["documents"][0].page_content == "实际证据"
    assert observer.tool_calls[0]["duration_ms"] >= 9


@pytest.mark.asyncio
async def test_real_shared_graph_fixed_inputs_memory_and_no_gold_leak():
    fixture = FixedInputs(
        {
            "query": "校园网怎么充值",
            "conversation_context": [{"role": "user", "content": "我在崂山校区"}],
            "conversation_summary": "用户在校",
            "injected_memories": ["用户偏好步骤说明"],
        }
    )
    recognizer = SimpleNamespace(
        recognize=AsyncMock(
            return_value=IntentResult(
                primary_intent="campus_network", confidence=1, policy_decision="allow"
            )
        )
    )
    service = SimpleNamespace(
        execute=AsyncMock(
            return_value=AgentResult(
                agent_name="CampusAgent", answer="实际生成", evidence_sufficient=True
            )
        )
    )
    graph = build_global_graph(
        GlobalGraphDependencies(
            fixture,
            recognizer,
            AgentRouter(),
            fixture,
            SimpleNamespace(
                select=lambda **kwargs: SimpleNamespace(selected=[], model_dump=lambda **kwargs: {})
            ),
            SimpleNamespace(scores=AsyncMock(return_value={})),
            service,
            SimpleNamespace(compose=AsyncMock(return_value=FinalResponse(content="实际生成"))),
            SimpleNamespace(event=AsyncMock()),
        )
    )
    initial = {
        "request_id": "r",
        "user_id": "u",
        "conversation_id": "c",
        "query": fixture.inputs["query"],
        "recent_messages": [],
        "conversation_summary": "",
        "uploaded_files": [],
        "recalled_memories": [],
        "intent_result": None,
        "route_plan": None,
        "selected_skills": {},
        "agent_results": {},
        "errors": [],
        "final_response": None,
        "citations": [],
        "trace_context": {},
    }
    result = await graph.ainvoke(initial)
    assert result["recalled_memories"] == [] and result["conversation_summary"] == ""
    assert result["uploaded_files"] == [] and result["final_response"].content == "实际生成"
    assert (
        recognizer.recognize.await_args.kwargs["recent_messages"]
        == fixture.inputs["conversation_context"]
    )
    assert "reference_answer" not in str(service.execute.await_args)


@pytest.mark.asyncio
async def test_judge_does_not_fill_nonrefusal_or_unavailable_evidence():
    fake = SimpleNamespace(
        ainvoke=AsyncMock(
            return_value=SimpleNamespace(
                model_dump=lambda **kwargs: dict(
                    correctness=4,
                    faithfulness=5,
                    relevance=4,
                    completeness=4,
                    helpfulness=4,
                    refusal_quality=5,
                    reasons={"correctness": "符合要求"},
                )
            )
        )
    )
    judge = UnifiedJudge(SimpleNamespace(with_structured_output=lambda *args, **kwargs: fake))
    result = await judge.evaluate(case_payload(), "你好", [])
    assert (
        result["metrics"]["refusal_quality"] is None and result["metrics"]["faithfulness"] is None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "intent,policy,parts,route",
    [
        ("compound", "allow", ["campus_network", "learning_plan"], "multi_agent"),
        ("unclear", "allow", [], "clarify"),
        ("campus_network", "refuse_unauthorized", [], "direct_response"),
    ],
)
async def test_multi_agent_clarification_and_refusal_execute_real_routes(
    intent, policy, parts, route
):
    fixture = FixedInputs({"query": "处理用户需求", "injected_memories": ["已选记忆"]})
    recognizer = SimpleNamespace(
        recognize=AsyncMock(
            return_value=IntentResult(
                primary_intent=intent, confidence=1, policy_decision=policy, sub_intents=parts
            )
        )
    )

    async def execute(**kwargs):
        await asyncio.sleep(0.01)
        return AgentResult(
            agent_name=kwargs["assignment"].agent_name,
            answer="原子任务回答",
            evidence_sufficient=True,
        )

    agent = SimpleNamespace(execute=AsyncMock(side_effect=execute))
    sink = SimpleNamespace(event=AsyncMock())
    graph = build_global_graph(
        GlobalGraphDependencies(
            fixture,
            recognizer,
            AgentRouter(),
            fixture,
            SimpleNamespace(
                select=lambda **kwargs: SimpleNamespace(selected=[], model_dump=lambda **kwargs: {})
            ),
            SimpleNamespace(scores=AsyncMock(return_value={})),
            agent,
            SimpleNamespace(compose=AsyncMock(return_value=FinalResponse(content="合并任务回答"))),
            sink,
            direct_reply=SimpleNamespace(
                reply=AsyncMock(return_value=SimpleNamespace(content="拒绝越权请求", usage={}))
            ),
        )
    )
    initial = {
        "request_id": "r",
        "user_id": "u",
        "conversation_id": "c",
        "query": fixture.inputs["query"],
        "recent_messages": [],
        "conversation_summary": "",
        "uploaded_files": [],
        "recalled_memories": [],
        "intent_result": None,
        "route_plan": None,
        "selected_skills": {},
        "agent_results": {},
        "errors": [],
        "final_response": None,
        "citations": [],
        "trace_context": {},
    }
    async with observation_scope("r", sink) as observer:
        state = await graph.ainvoke(initial)
    assert state["route_plan"].route == route
    assert agent.execute.await_count == (2 if route == "multi_agent" else 0)
    if route == "multi_agent":
        assert set(state["agent_results"]) == {"CampusAgent", "LearningAgent"}
        assert state["recalled_memories"] == []
    else:
        assert state["recalled_memories"] == []
    assert any(event["payload"].get("node") == "route_request" for event in observer.events)


class MemoryRepository:
    def __init__(self, run):
        self.value, self.rows = run, []

    async def update_run(self, run_id, **values):
        self.value.update(copy.deepcopy(values))

    async def run(self, run_id):
        return copy.deepcopy(self.value)

    async def results(self, run_id):
        return copy.deepcopy(self.rows)

    async def add_result(self, run_id, row):
        self.rows.append(copy.deepcopy(row))

    async def request_stop(self, run_id):
        self.value["stop_requested"] = True
        return copy.deepcopy(self.value)


@pytest.mark.asyncio
async def test_stop_cancels_active_graph_and_preserves_completed_results():
    c = prepare_case(view(), corpus())
    run = {
        "run_id": str(uuid4()),
        "user_id": str(uuid4()),
        "status": "queued",
        "repeats": 3,
        "stop_requested": False,
        "snapshot": {"cases": [c], "corpus": corpus()},
        "progress": {"total_attempts": 3, "completed_attempts": 0, "stage": "排队中"},
    }
    repository = MemoryRepository(run)
    entered = asyncio.Event()
    count = 0

    async def invoke(initial):
        nonlocal count
        count += 1
        if count == 2:
            entered.set()
            await asyncio.sleep(30)
        return {
            **initial,
            "intent_result": IntentResult(
                primary_intent="small_talk", confidence=1, policy_decision="allow"
            ),
            "route_plan": AgentRouter().route(
                IntentResult(primary_intent="small_talk", confidence=1, policy_decision="allow")
            ),
            "final_response": FinalResponse(content="你好"),
        }

    traces = SimpleNamespace(
        start=AsyncMock(side_effect=lambda *args: SimpleNamespace(trace_id=uuid4())),
        append_event=AsyncMock(),
        finish=AsyncMock(),
    )
    def graph_factory(inputs, fixed_corpus, sink):
        assert set(inputs.inputs) <= {"query", "conversation_context"}
        assert inputs.inputs["query"] == c["payload"]["query"]
        return SimpleNamespace(ainvoke=invoke)

    service = UnifiedEvaluationService(
        repository,
        traces,
        SimpleNamespace(load_evaluation_chunks=AsyncMock(return_value=[])),
        None,
        None,
        graph_factory,
        SimpleNamespace(evaluate=AsyncMock(return_value={"status": "completed", "metrics": {}})),
        {},
    )
    task = asyncio.create_task(service.execute(copy.deepcopy(run)))
    service.tasks[run["run_id"]] = task
    await asyncio.wait_for(entered.wait(), 3)
    await service.stop(run["run_id"])
    assert repository.value["status"] == "stopped"
    assert repository.value["progress"]["completed_attempts"] == 1
    assert len(repository.rows) == 2 and repository.rows[0]["generation"]["answer"] == "你好"
    assert repository.rows[1]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_stop_before_first_scheduling_releases_global_run():
    run = {"run_id": str(uuid4()), "status": "queued", "progress": {}, "stop_requested": False}
    repo = MemoryRepository(run)
    service = UnifiedEvaluationService(repo, None, None, None, None, None, None, {})
    task = asyncio.create_task(asyncio.sleep(10))
    service.tasks[run["run_id"]] = task
    await service.stop(run["run_id"])
    assert repo.value["status"] == "stopped"


@pytest.mark.asyncio
async def test_agentic_production_retrieval_preserves_configured_rrf_candidate_limit():
    from tests.test_agentic_retrieval import setup, plan, request
    from app.retrieval.external.planned import RetrievalSession

    docs = [Document(page_content=f"实际资料{i}") for i in range(12)]
    retriever, _, _ = setup(dense=AsyncMock(return_value=docs))
    result = await retriever.retrieve_with_diagnostics(request(), plan=plan(), session=RetrievalSession())
    # 检索沿用现有 RRF 候选10条；统一评测自身仍按前5条计算 @5。
    assert len(result.documents) == 10


@pytest.mark.asyncio
async def test_stop_during_judging_keeps_answer_without_counting_as_completed():
    c = prepare_case(view(), corpus())
    run = {
        "run_id": str(uuid4()),
        "user_id": str(uuid4()),
        "status": "queued",
        "repeats": 1,
        "stop_requested": False,
        "snapshot": {"cases": [c], "corpus": corpus()},
        "progress": {"total_attempts": 1, "completed_attempts": 0, "stage": "排队中"},
    }
    repo = MemoryRepository(run)
    judging = asyncio.Event()

    async def invoke(initial):
        return {
            **initial,
            "intent_result": IntentResult(
                primary_intent="small_talk", confidence=1, policy_decision="allow"
            ),
            "final_response": FinalResponse(content="已生成回答"),
        }

    async def evaluate(*args):
        judging.set()
        await asyncio.sleep(30)

    traces = SimpleNamespace(
        start=AsyncMock(return_value=SimpleNamespace(trace_id=uuid4())),
        append_event=AsyncMock(),
        finish=AsyncMock(),
    )
    service = UnifiedEvaluationService(
        repo,
        traces,
        SimpleNamespace(load_evaluation_chunks=AsyncMock(return_value=[])),
        None,
        None,
        lambda *args: SimpleNamespace(ainvoke=invoke),
        SimpleNamespace(evaluate=evaluate),
        {},
    )
    task = asyncio.create_task(service.execute(copy.deepcopy(run)))
    service.tasks[run["run_id"]] = task
    await asyncio.wait_for(judging.wait(), 3)
    await service.stop(run["run_id"])
    assert repo.rows[0]["generation"]["answer"] == "已生成回答"
    assert repo.rows[0]["generation"]["status"] == "cancelled"
    assert (
        repo.value["summary_metrics"]["completed_attempts"]
        == repo.value["progress"]["completed_attempts"]
        == 0
    )


def test_api_workspace_boundary_and_atomic_import():
    from app.api.routes.unified_evaluation import router
    from app.api.dependencies import workspace_user, get_container
    from fastapi import HTTPException

    app = FastAPI()
    app.include_router(router)
    repo = SimpleNamespace(save=AsyncMock(return_value={"saved_count": 1}))
    app.dependency_overrides[get_container] = lambda: SimpleNamespace(unified_evaluations=repo)

    def blocked():
        raise HTTPException(403, "仅接受本机请求")

    app.dependency_overrides[workspace_user] = blocked
    with TestClient(app) as client:
        assert client.get("/developer/evaluation/cases").status_code == 403
        app.dependency_overrides[workspace_user] = lambda: SimpleNamespace(user_id=str(uuid4()))
        response = client.post(
            "/developer/evaluation/import", json={"text": json.dumps(case_payload()) + "\n{bad}"}
        )
        assert response.status_code == 422 and response.json()["detail"]["errors"][0]["line"] == 2
        repo.save.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_UNIFIED_DB_TESTS") != "1", reason="显式启用隔离PostgreSQL验收")
async def test_database_sequence_holes_snapshots_single_run_and_recovery():
    from tests.test_conversation_database import isolated_database, initialize_environment
    from app.db.models import User
    from app.db.repositories.unified_evaluation import UnifiedEvaluationRepository
    from app.core.errors import EvaluationConflict
    from sqlalchemy import text

    async with isolated_database() as (database, environment):
        repo = UnifiedEvaluationRepository(database)
        async with database.session() as session:
            user = User(username="unified-temp", password_hash="unused", role="developer")
            session.add(user)
            await session.flush()
            user_id = str(user.user_id)
            assert (
                await session.scalar(
                    text(
                        "SELECT count(*) FROM information_schema.columns WHERE table_schema='knowledge' AND table_name='documents' AND column_name='department'"
                    )
                )
                == 0
            )
        await initialize_environment(environment)
        schema = UnifiedCase.model_validate(case_payload())
        saved = (await repo.save([schema, schema, schema]))["cases"]
        edited = schema.model_copy(deep=True)
        edited.query = "另一个有效提问"
        assert (await repo.save([edited], saved[0]["case_id"]))["cases"][0]["payload"]["query"] == "另一个有效提问"
        saved[0] = (await repo.selected([saved[0]["case_id"]]))[0]
        run = await repo.create_run(user_id, 1, [saved[1]], corpus(), {})
        with pytest.raises(EvaluationConflict):
            await repo.create_run(user_id, 1, [saved[0]], corpus(), {})
        await repo.add_result(
            run["run_id"],
            {"case": saved[1], "trial_index": 1, "generation": {"answer": "保留的原回答"}},
        )
        await repo.remove(saved[1]["case_id"])
        next_case = (await repo.save([schema]))["cases"][0]
        assert next_case["case_number"] == 4
        selected = await repo.selected(
            [saved[0]["case_id"], saved[2]["case_id"], next_case["case_id"]]
        )
        assert [c["case_number"] for c in selected] == [1, 3, 4]
        assert (await repo.results(run["run_id"]))[0]["case"]["case_label"] == "Case 002"
        await repo.recover_interrupted()
        assert (await repo.current())["status"] == "interrupted"
        assert (await repo.current())["progress"]["completed_attempts"] == 1
        assert "snapshot" not in await repo.current()
        run2 = await repo.create_run(user_id, 1, selected, corpus(), {})
        assert (await repo.current())["run_id"] == run2["run_id"]
        from app.db.repositories.traces import TraceRepository

        traces = TraceRepository(database)
        request_id = str(uuid4())
        trace = await traces.start(request_id, user_id, str(uuid4()), "evaluation", "完整输入")

        async def record_event(request, event, payload, **kwargs):
            await traces.append_event(request, event, payload)

        sink = SimpleNamespace(event=record_event)
        async with observation_scope(request_id, sink):
            async with span("execute_agents", "Agent阶段"):
                async with span("agent", "CampusAgent", agent="CampusAgent"):
                    await asyncio.sleep(0.005)
                    async with span("tool", "检索工具", tool="agentic_retrieval") as details:
                        details["output"] = ("实际返回", {"documents": [], "diagnostics": {"rounds": 3}})
                    for _ in range(3):
                        await record_event(request_id, "retrieval.round.started", {})
        await traces.finish(request_id, "success", {})
        detail = await traces.detail(str(trace.trace_id))
        assert detail["trace"]["query"] == "完整输入" and len(detail["stages"]) == 3
        assert detail["trace"]["tool_call_count"] == 1
        assert detail["trace"]["retrieval_rounds"] == 3
        assert detail["stages"][1]["parent_span_id"] == detail["stages"][0]["span_id"]
        assert (await traces.list_views(source="chat")) == []
        cancelled_request = str(uuid4())
        cancelled_trace = await traces.start(cancelled_request, user_id, str(uuid4()), "evaluation", "超时计数验收")
        await traces.append_event(cancelled_request, "retrieval.round.started", {"round_number": 1})
        await traces.append_event(cancelled_request, "phase.completed", {
            "node": "tool", "tool": "agentic_retrieval",
            "output": ["第一次工具已返回", {"diagnostics": {"rounds": 1}}],
        })
        await traces.append_event(cancelled_request, "retrieval.round.started", {"round_number": 2})
        await traces.finish(cancelled_request, "failed", {})
        cancelled_detail = await traces.detail(str(cancelled_trace.trace_id))
        assert cancelled_detail["trace"]["retrieval_rounds"] == 2
