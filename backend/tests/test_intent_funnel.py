"""四级漏斗行为契约：范围边界、缺文件、任务一致性及真实向量数值。"""

import json
from unittest.mock import AsyncMock

import httpx
import numpy as np
import pytest

from app.intent import funnel
from app.intent.signals import IntentSignal
from app.intent.service import LLMIntentOutput
from app.routing.router import AgentRouter
from app.schemas.enums import IntentName
from tests.intent_helpers import service
from tests.test_intent_jev import recognizer, response


@pytest.mark.parametrize(
    "query,intent",
    [
        ("你好！", "small_talk"),
        ("可以介绍一下你的身份吗？", "small_talk"),
        ("你能帮我做些什么？", "small_talk"),
        ("请问校园网怎么开通？", "campus_network"),
        ("校园网的收费标准是什么？", "campus_network"),
        ("我自己的校园网账号忘记密码怎么找回？", "campus_network"),
        ("下周学校有哪些企业宣讲？", "career_recruitment"),
        ("查询本周学校招聘会信息", "career_recruitment"),
        ("本科毕业的学分要求是什么？", "campus_affairs"),
        ("缓考申请需要什么材料？", "campus_affairs"),
        ("安排未来两周的复习计划", "learning_plan"),
        ("帮我制定数据结构复习计划", "learning_plan"),
    ],
)
def test_rules_cover_complete_supported_requests(query, intent):
    agent, _ = service()
    assert funnel.rule_decision(agent, query).value == intent
    assert funnel.fast_gate(query) is None or intent == "small_talk"


@pytest.mark.parametrize(
    "query",
    [
        "购买校园网运营商的股票",
        "投资一家学校网络服务企业",
        "开发校园网认证程序",
        "查本周招聘会并替我订票",
        "帮我处理那份学习计划",
        "不要分析简历只算绩点",
        "他说校园网怎么充值，请数一下他说了几次",
        "参考讲义制定复习计划",
        "查询学校招聘会并帮我规划复习日程",
        "给我他人的校园网凭证",
    ],
)
def test_boundary_or_dependency_requires_semantic_decision(query):
    assert funnel.fast_gate(query)


def test_calendar_is_not_a_reference_and_missing_file_contract_disables_rule():
    agent, _ = service()
    assert funnel.fast_gate("如何规划这个月的学习") is None
    from dataclasses import replace

    agent.catalog[IntentName.CAMPUS_NETWORK] = replace(
        agent.catalog[IntentName.CAMPUS_NETWORK], required_entities=("file_id",)
    )
    assert funnel.rule_decision(agent, "校园网怎么充值") is None


@pytest.mark.asyncio
async def test_unrelated_history_and_upload_do_not_change_self_contained_rule():
    agent, llm = service()
    result = await agent.recognize(
        "校园网的收费标准是什么？",
        [{"role": "user", "content": "昨天要求修改求职材料"}],
        "求职话题",
        ["resume"],
        ["履历.pdf"],
        "funnel",
    )
    assert result.decision_stage == "rule" and not result.entities and not result.missing_fields
    assert AgentRouter().route(result).route == "single_agent"
    llm.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_scope_veto_skips_vector_even_if_similarity_would_be_high(monkeypatch):
    probe = AsyncMock()
    monkeypatch.setattr(funnel, "embedding_decision", probe)
    agent, llm, client = recognizer(
        monkeypatch,
        lambda req: httpx.Response(
            200, json=response(json.loads(req.content)["questions"], "out_of_scope")
        ),
    )
    async with client:
        result = await agent.recognize(
            "购买校园网运营商的股票并给出买入价格", [], "", strategy="funnel"
        )
    assert result.primary_intent == IntentName.OUT_OF_SCOPE and result.decision_stage == "jev"
    assert AgentRouter().route(result).route == "direct_response"
    probe.assert_not_awaited()
    llm.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_high_vector_score_requires_independent_domain_evidence(monkeypatch):
    scores = {intent: 0.1 for intent in IntentName}
    scores[IntentName.CAMPUS_NETWORK] = 0.99
    monkeypatch.setattr(
        funnel,
        "embedding_decision",
        AsyncMock(
            return_value=(IntentSignal("embedding", scores, {}), {"campus_network": 0.1}, {})
        ),
    )
    agent, llm, client = recognizer(
        monkeypatch,
        lambda req: httpx.Response(
            200,
            json=response(
                json.loads(req.content)["questions"], "learning_plan", tasks=("task_learning_plan",)
            ),
        ),
    )
    async with client:
        result = await agent.recognize("我需要一个有条理的备考安排", [], "", strategy="funnel")
    assert result.decision_stage == "jev" and result.primary_intent == IntentName.LEARNING_PLAN
    assert result.stage_trace[1]["accepted"] is False
    llm.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "intent,tasks,reason",
    [
        ("unclear", ("task_learning_gpa",), "intent_tasks_conflict"),
        ("campus_network", ("task_career_recruitment",), "primary_task"),
        ("campus_network", ("task_campus_network", "task_learning_plan"), "cross_agent_tasks"),
    ],
)
async def test_task_contradictions_escalate_complete_input(monkeypatch, intent, tasks, reason):
    agent, llm, client = recognizer(
        monkeypatch,
        lambda req: httpx.Response(
            200, json=response(json.loads(req.content)["questions"], intent, tasks=tasks)
        ),
    )
    llm.ainvoke.return_value = LLMIntentOutput(
        primary_intent="learning_gpa", confidence=0.99, entities={"file_id": []}
    )
    async with client:
        result = await agent.recognize(
            "请算绩点，成绩表尚未上传",
            [{"role": "user", "content": "昨天的事情"}],
            "历史摘要",
            strategy="funnel",
        )
    assert result.decision_stage == "llm_fallback"
    assert reason in result.decision_details["jev"]["uncertain_fields"]
    assert result.primary_intent == IntentName.LEARNING_GPA and result.missing_fields == ["file_id"]
    assert AgentRouter().route(result).route == "clarify"
    sent = llm.ainvoke.await_args.args[0]
    assert "历史摘要" in sent[1].content and "决策补充规则" in sent[0].content


@pytest.mark.asyncio
async def test_missing_file_can_finish_at_jev_without_fallback(monkeypatch):
    agent, llm, client = recognizer(
        monkeypatch,
        lambda req: httpx.Response(
            200,
            json=response(
                json.loads(req.content)["questions"], "learning_gpa", tasks=("task_learning_gpa",)
            ),
        ),
    )
    async with client:
        result = await agent.recognize(
            "请计算绩点，成绩记录稍后发", [], "", strategy="funnel"
        )
    assert result.decision_stage == "jev" and result.missing_fields == ["file_id"]
    assert AgentRouter().route(result).route == "clarify"
    llm.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_same_agent_tasks_and_refusal_do_not_require_cross_agent_fallback(monkeypatch):
    mode = {"refuse": False}

    def handler(req):
        return httpx.Response(
            200,
            json=response(
                json.loads(req.content)["questions"],
                "campus_network",
                policy="refuse_unauthorized" if mode["refuse"] else "allow",
                tasks=() if mode["refuse"] else ("task_campus_network", "task_campus_affairs"),
            ),
        )

    agent, llm, client = recognizer(monkeypatch, handler)
    async with client:
        result = await agent.recognize(
            "介绍学校网络手续和学籍办理流程", [], "", strategy="funnel"
        )
        assert result.decision_stage == "jev" and not result.sub_intents
        mode["refuse"] = True
        denied = await agent.recognize("获取别人的校园网凭证", [], "", strategy="funnel")
        assert AgentRouter().route(denied).route == "direct_response"
        assert not denied.missing_fields
    llm.ainvoke.assert_not_awaited()




@pytest.mark.asyncio
async def test_vector_timeout_keeps_failure_diagnostic_and_upgrades(monkeypatch):
    monkeypatch.setattr(funnel, "embedding_decision", AsyncMock(side_effect=TimeoutError()))
    agent, llm, client = recognizer(
        monkeypatch,
        lambda req: httpx.Response(
            200,
            json=response(
                json.loads(req.content)["questions"], "learning_plan", tasks=("task_learning_plan",)
            ),
        ),
    )
    async with client:
        result = await agent.recognize("如何规划这个月的学习", [], "", strategy="funnel")
    assert result.decision_stage == "jev" and result.signal_errors["embedding"] == "TimeoutError"
    llm.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_vector_matrix_matches_cosine_and_reuses_only_prototypes():
    agent, _ = service()

    async def documents(items):
        return [[1.0, 2.0, 3.0] for _ in items]

    agent.embeddings = type("Vectors", (), {})()
    agent.embeddings.aembed_documents = AsyncMock(side_effect=documents)
    agent.embeddings.aembed_query = AsyncMock(return_value=[2.0, 1.0, 1.0])
    first, _, profile = await funnel.embedding_decision(agent, "查询招聘活动")
    second, _, _ = await funnel.embedding_decision(agent, "查询招聘活动")
    assert set(profile) == {"embedding_prototypes", "embedding_query", "embedding_compare"}
    expected = agent._cosine([2.0, 1.0, 1.0], [1.0, 2.0, 3.0])
    assert np.allclose(list(first.scores.values()), expected)
    assert second.scores == first.scores
    assert agent.embeddings.aembed_documents.await_count == 1
    assert agent.embeddings.aembed_query.await_count == 2
    agent.embeddings.aembed_query.return_value = [float("nan"), 0.0, 1.0]
    with pytest.raises(ValueError):
        await funnel.embedding_decision(agent, "查询招聘活动")


@pytest.mark.parametrize(
    "query",
    [
        "学习",
        "招聘",
        "网络",
        "这个申请怎么办",
        "前面的计划要调整",
        "别读求职履历，制定学习计划",
        "同样的申请怎么办",
    ],
)
def test_underspecified_or_dependent_input_never_uses_local_vector(query):
    assert funnel.fast_gate(query)


@pytest.mark.parametrize(
    "query",
    [
        "我考研英语67分，可以免修英语吗？",
        "这门课期末挂了，是等补考还是现在申请重修？",
    ],
)
def test_exam_background_is_not_a_cross_agent_deliverable(query):
    assert funnel.fast_gate(query) is None


@pytest.mark.asyncio
async def test_refused_request_does_not_select_an_uploaded_file_even_if_model_does():
    agent, llm = service()
    llm.ainvoke.return_value = LLMIntentOutput(
        primary_intent="learning_gpa",
        confidence=0.99,
        entities={"file_id": ["transcript"]},
        policy_decision="refuse_unauthorized",
    )
    result = await agent._deepseek_decision(
        "获取他人的成绩", [], "", ["transcript"], ["本科成绩单.xlsx"]
    )
    assert not result.entities.get("file_id") and not result.missing_fields
    assert AgentRouter().route(result).route == "direct_response"
