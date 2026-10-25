"""漏斗计时、异常链、单策略 HTTP 契约；不调用真实供应商。"""

from unittest.mock import AsyncMock

import pytest

from app.intent import funnel
from app.intent.signals import IntentSignal
from app.schemas.enums import IntentName
from tests.test_intent_jev import recognizer


@pytest.mark.asyncio
async def test_vector_return_completes_four_stage_trace(monkeypatch):
    agent, llm, client = recognizer(monkeypatch, lambda req: pytest.fail("Jev must not run"))
    scores = {intent: 0.01 for intent in IntentName}
    scores[IntentName.CAMPUS_NETWORK] = 0.99
    monkeypatch.setattr(
        funnel,
        "embedding_decision",
        AsyncMock(
            return_value=(
                IntentSignal("embedding", scores, {}),
                {},
                {"embedding_query": 2, "embedding_compare": 1},
            )
        ),
    )
    async with client:
        result = await agent.recognize("学校网络怎样查询个人资费", [], "")
    assert [item["status"] for item in result.stage_trace] == [
        "continued",
        "returned",
        "not_reached",
        "not_reached",
    ]
    assert result.stage_trace[1]["substeps_ms"]["embedding_query"] == 2
    llm.ainvoke.assert_not_awaited()
