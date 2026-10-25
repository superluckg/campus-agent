"""提前返回必须保留完整决策；复杂、冲突或失败信号升级而不猜测。"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from app.intent.catalog import load_intent_catalog
from app.intent.patterns import PatternIntentMatcher
from app.intent.service import IntentRecognitionService, LLMIntentOutput
from app.intent.signals import IntentSignal
from app.schemas.enums import IntentName

ROOT = Path(__file__).resolve().parents[2]


def case(query="你好~", reference=None):
    return SimpleNamespace(
        intent_case_id=uuid4(),
        external_id="T01",
        enabled=True,
        inputs={"query": query, "conversation_context": [], "uploaded_file_refs": []},
        reference_outputs=reference
        or {"expected_intent": "small_talk", "expected_route": "direct_response"},
    )


def service(output=None):
    model = Mock()
    structured = SimpleNamespace(
        ainvoke=AsyncMock(
            return_value=output or LLMIntentOutput(primary_intent="learning_plan", confidence=0.98)
        )
    )
    model.with_structured_output.return_value = structured
    embeddings = SimpleNamespace(aembed_query=AsyncMock(), aembed_documents=AsyncMock())
    result = IntentRecognitionService(
        load_intent_catalog(ROOT / "config/intents.yaml"),
        model,
        embeddings,
        PatternIntentMatcher(),
    )
    scores = {intent: 0.1 for intent in IntentName}
    scores[IntentName.LEARNING_PLAN] = 0.98
    result._embedding_signal = AsyncMock(return_value=IntentSignal("embedding", scores, {}))
    return result, structured
