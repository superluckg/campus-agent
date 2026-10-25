"""验证真实 HTTP 契约、七字段转换、升级边界及失败口径，不宣称模型准确率。"""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from app.intent.catalog import load_intent_catalog
from app.intent.jev import JevDecisionClient, questions, validate_answers
from app.intent.patterns import PatternIntentMatcher
from app.intent.service import IntentRecognitionService, LLMIntentOutput
from app.routing.router import AgentRouter

ROOT = Path(__file__).resolve().parents[2]
CATALOG = load_intent_catalog(ROOT / "config/intents.yaml")


def response(request_questions, intent="small_talk", policy="allow", tasks=(), files=()):
    answers = {}
    for key, question in request_questions.items():
        if question["type"] == "choice":
            choice = intent if key == "intent" else policy
            probabilities = {
                option: 0.01 / (len(question["criteria"]) - 1) for option in question["criteria"]
            }
            probabilities[choice] = 0.99
            answers[key] = {
                "type": "choice",
                "choice": choice,
                "probabilities": probabilities,
                "confidence": 0.98,
            }
        else:
            answers[key] = {"type": "noul", "noul": 0.98 if key in tasks or key in files else 0.02}
    return {
        "model": "jev-1.13.0",
        "answers": answers,
        "usage": {"input_tokens": 120, "output_tokens": 12},
    }


def recognizer(monkeypatch, handler):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    client = httpx.AsyncClient(
        base_url="https://api.typesafe.ai", transport=httpx.MockTransport(handler)
    )
    jev = JevDecisionClient(client=client)
    model = Mock()
    structured = SimpleNamespace(
        ainvoke=AsyncMock(
            return_value=LLMIntentOutput(primary_intent="small_talk", confidence=0.98)
        )
    )
    model.with_structured_output.return_value = structured
    result = IntentRecognitionService(
        CATALOG,
        model,
        SimpleNamespace(),
        PatternIntentMatcher(),
        jev_client=jev,
    )
    return result, structured, client


@pytest.mark.asyncio
async def test_http_contract_sends_one_state_and_typed_questions_and_never_calls_llm(monkeypatch):
    captured = {}

    def handler(request):
        captured.update(json.loads(request.content))
        assert request.url == "https://api.typesafe.ai/v1/systemone"
        assert request.headers["Authorization"] == "Bearer test-key"
        return httpx.Response(200, json=response(captured["questions"]))

    service, llm, client = recognizer(monkeypatch, handler)
    async with client:
        result = await service.recognize(
            "请讲一个校园小故事", [{"role": "assistant", "content": "欢迎"}], "摘要", strategy="funnel"
        )
    assert captured["model"] == "jev-1.13.0"
    assert (
        captured["state"]["query"] == "请讲一个校园小故事" and captured["state"]["conversation_summary"] == "摘要"
    )
    assert len(captured["questions"]) == 9
    assert AgentRouter().route(result).route == "direct_response"
    assert result.model_calls == {"jev": 1, "llm": 0, "embedding": 0}
    assert result.model_usage["total_tokens"] == 132
    assert (
        result.decision_details["jev"]["answers"]["intent"]["probabilities"]["small_talk"] == 0.99
    )
    llm.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_compound_and_dynamic_file_decisions_are_not_inferred_from_filenames(monkeypatch):
    def handler(request):
        body = json.loads(request.content)
        return httpx.Response(
            200,
            json=response(
                body["questions"],
                "compound",
                tasks=("task_career_resume", "task_learning_gpa"),
                files=("file_0", "file_2"),
            ),
        )

    service, llm, client = recognizer(monkeypatch, handler)
    async with client:
        result = await service.recognize(
            "分析求职材料并计算绩点",
            [],
            "",
            ["random-a", "random-b", "random-c"],
            ["履历.docx", "无关笔记.pdf", "学期分数.xlsx"],
            "funnel",
        )
    assert result.entities == {"file_id": ["random-a", "random-c"]}
    assert {intent.value for intent in result.sub_intents} == {"career_resume", "learning_gpa"}
    route = AgentRouter().route(result)
    assert route.route == "multi_agent"
    assert {item.agent_name.value for item in route.assignments} == {"CareerAgent", "LearningAgent"}
    llm.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_refusal_never_requests_files_and_keeps_semantic_intent(monkeypatch):
    def handler(request):
        body = json.loads(request.content)
        return httpx.Response(
            200, json=response(body["questions"], "campus_network", policy="refuse_unauthorized")
        )

    service, llm, client = recognizer(monkeypatch, handler)
    async with client:
        result = await service.recognize("绕过验证获取同学校园网账号", [], "", strategy="funnel")
    assert result.primary_intent.value == "campus_network"
    assert result.missing_fields == []
    assert AgentRouter().route(result).route == "direct_response"
    llm.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_uncertain_file_escalates_complete_request(monkeypatch):
    def handler(request):
        body = json.loads(request.content)
        payload = response(body["questions"], "learning_plan", tasks=("task_learning_plan",))
        payload["answers"]["file_0"]["noul"] = 0.5
        return httpx.Response(200, json=payload)

    service, llm, client = recognizer(monkeypatch, handler)
    async with client:
        result = await service.recognize(
            "参考这个提纲制定学习计划", [], "", ["id-9"], ["提纲.pdf"], "funnel"
        )
    assert result.decision_details["jev"]["uncertain_fields"] == ["file_0"]
    assert result.decision_stage == 'llm_fallback'
    assert result.model_calls == {'jev': 1, 'llm': 1, 'embedding': 0}
    llm.ainvoke.assert_awaited_once()
    assert 'id-9' in llm.ainvoke.await_args.args[0][1].content


@pytest.mark.asyncio
async def test_rate_limit_falls_back_without_leaking_response(monkeypatch):
    service, llm, client = recognizer(monkeypatch, lambda request: httpx.Response(429, json={'secret': 'should-not-appear'}))
    async with client:
        fallback = await service.recognize('请讲一个校园小故事', [], '')
    assert fallback.signal_errors == {'jev': 'RuntimeError'}
    assert fallback.model_calls == {'jev': 1, 'llm': 1, 'embedding': 0}
    assert fallback.stage_trace[2]['status'] == 'failed'
    llm.ainvoke.assert_awaited_once()


@pytest.mark.parametrize("mutation", ["missing_question", "unknown_choice", "nan", "distribution"])
def test_invalid_provider_results_are_rejected(mutation):
    expected = questions(CATALOG, [])
    payload = response(expected)
    if mutation == "missing_question":
        payload["answers"].pop("policy")
    elif mutation == "unknown_choice":
        payload["answers"]["intent"]["choice"] = "foreign"
    elif mutation == "nan":
        payload["answers"]["intent"]["confidence"] = float("nan")
    else:
        payload["answers"]["intent"]["probabilities"]["small_talk"] = 0.5
    with pytest.raises(ValueError):
        validate_answers(payload, expected)
