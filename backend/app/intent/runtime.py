"""聊天与统一评测共享的意图服务装配。"""

from app.intent.catalog import load_intent_catalog
from app.intent.jev import JevDecisionClient
from app.intent.patterns import PatternIntentMatcher
from app.intent.service import IntentRecognitionService

PRODUCTION_STRATEGIES = ("funnel",)


def build_intent_service(settings, model_factory, embeddings, intent_config):
    strategy = intent_config.get("default_strategy", "funnel")
    if strategy not in PRODUCTION_STRATEGIES:
        raise ValueError("正式聊天策略无效：" + strategy)
    config = settings.load_yaml(intent_config.get("hybrid_config_file", "intent_hybrid.yaml"))
    jev = JevDecisionClient(config=config.get("jev", {}))
    if not jev.configured:
        raise ValueError("默认 funnel 需要配置 Jev 凭据：" + jev.config["api_key_env"])
    return IntentRecognitionService(
        catalog=load_intent_catalog(settings.config_dir / "intents.yaml"),
        model=model_factory.chat_model(
            "intent_model",
            max_retries=int(intent_config.get("llm_max_retries", 0)),
            max_output_tokens=int(intent_config.get("llm_max_output_tokens", 1536)),
        ),
        embeddings=embeddings,
        clarification_threshold=float(intent_config["clarification_threshold"]),
        ambiguity_gap=float(intent_config["ambiguity_gap"]),
        pattern_matcher=PatternIntentMatcher(),
        jev_client=jev,
        default_strategy=strategy,
        hybrid_config=config,
    )
