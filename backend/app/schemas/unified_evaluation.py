"""人工维护问题、意图、回答行为和原文；执行标准由程序推导。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.schemas.enums import IntentName

SUPPORTED_INTENTS = {
    "career_recruitment", "campus_network", "campus_affairs", "learning_plan",
    "compound", "unclear", "small_talk", "out_of_scope",
}
AGENT_FOR_INTENT = {
    "career_recruitment": "CareerAgent", "campus_network": "CampusAgent",
    "campus_affairs": "CampusAgent", "learning_plan": "LearningAgent",
}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ContextMessage(StrictModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=12000)

    @model_validator(mode="after")
    def nonblank(self):
        if not self.content.strip():
            raise ValueError("历史消息不能只包含空白。")
        return self


class Evidence(StrictModel):
    source_kind: Literal["knowledge_file", "official_web"] = "knowledge_file"
    filename: str = Field(default="", max_length=1000)
    source_url: str = Field(default="", max_length=2000)
    source_key: str = Field(default="", max_length=200)
    page_number: int | None = Field(default=None, ge=1)
    section_path: str = Field(default="", max_length=2000)
    content: str = Field(min_length=4, max_length=30000)
    related_intents: list[IntentName] = Field(default_factory=list, max_length=2)


class UnifiedCase(StrictModel):
    query: str = Field(min_length=1, max_length=4000)
    expected_intent: IntentName
    expected_behavior: Literal[
        "answer", "partial_answer", "clarify", "refuse", "explain_limitation", "small_talk"
    ]
    reference_answer: str = Field(min_length=1, max_length=30000)
    evidence: list[Evidence] = Field(max_length=50)
    conversation_context: list[ContextMessage] = Field(default_factory=list, max_length=8)
    expected_sub_intents: list[IntentName] = Field(default_factory=list, max_length=2)

    @model_validator(mode="after")
    def validate_evidence(self):
        from app.evaluation.retrieval.scoring import normalize

        if not self.query.strip() or not self.reference_answer.strip():
            raise ValueError("问题和参考回答不能只包含空白。")
        intent = self.expected_intent.value
        if intent not in SUPPORTED_INTENTS or any(
            part.value not in AGENT_FOR_INTENT for part in self.expected_sub_intents
        ):
            raise ValueError("评测不包含简历、附件资料阅读、绩点计算及含这些子任务的复合用例。")
        if intent == "compound":
            if len(self.expected_sub_intents) != 2 or len({
                AGENT_FOR_INTENT[p.value] for p in self.expected_sub_intents
            }) != 2:
                raise ValueError("复合任务需要两个不同助手领域的子意图。")
        elif self.expected_sub_intents:
            raise ValueError("非复合任务不填写子意图。")
        parts = self.expected_sub_intents or [self.expected_intent]
        for index, item in enumerate(self.evidence, 1):
            if len(normalize(item.content)) < 4:
                raise ValueError(f"第{index}条证据需要至少四个有效文字字符。")
            if item.source_kind == "knowledge_file" and not item.filename.strip():
                raise ValueError(f"第{index}条知识库证据需要选择文档。")
            if item.source_kind == "official_web" and not item.source_key.strip():
                raise ValueError(f"第{index}条官网证据需要选择已缓存来源。")
            if ((intent == "compound" and not item.related_intents)
                    or len(set(item.related_intents)) != len(item.related_intents)
                    or set(item.related_intents) - set(parts)):
                raise ValueError(f"第{index}条证据必须关联当前任务；修改子意图后请重新选择。")
        return self

    def case_payload(self):
        """存储与交换同一契约，省略未使用的条件字段。"""
        return self.model_dump(mode="json", exclude_defaults=True)


def decision_reference(payload):
    """拒答行为确定策略；意图和策略确定路由与助手，不调用模型。"""
    intent = payload["expected_intent"]
    parts = payload.get("expected_sub_intents", [])
    policy = "refuse" if payload["expected_behavior"] == "refuse" else "allow"
    route = (
        "direct_response" if policy == "refuse" or intent in {"small_talk", "out_of_scope"}
        else "clarify" if intent == "unclear"
        else "multi_agent" if intent == "compound" else "single_agent"
    )
    agents = (
        sorted({AGENT_FOR_INTENT[part] for part in parts}) if route == "multi_agent"
        else [AGENT_FOR_INTENT[intent]] if route == "single_agent" else []
    )
    return dict(expected_intent=intent, expected_route=route, expected_agents=agents,
                expected_sub_intents=parts, expected_policy=policy)


class UnifiedRunCreate(StrictModel):
    case_ids: list[str] = Field(min_length=1, max_length=2000)
    repeats: int = Field(default=1, ge=1, le=5)

    @model_validator(mode="after")
    def unique_ids(self):
        if len(set(self.case_ids)) != len(self.case_ids):
            raise ValueError("所选用例不能重复。")
        return self


class UnifiedImport(StrictModel):
    text: str = Field(min_length=1, max_length=8000000)


FIELD_LABELS = {
    "body": "表单", "query": "问题", "conversation_context": "历史消息",
    "role": "角色", "content": "原文或消息内容",
    "expected_intent": "主意图", "expected_route": "预期路由", "expected_agents": "预期助手",
    "expected_sub_intents": "复合子意图", "expected_policy": "处理策略",
    "expected_behavior": "预期回答行为", "reference_answer": "参考回答",
    "evidence": "标准原文证据", "source_kind": "资料类型", "filename": "文件名",
    "source_url": "来源网址", "source_key": "官网来源", "page_number": "页码",
    "section_path": "章节", "related_intents": "关联子任务",
    "case_ids": "所选用例", "repeats": "重复次数", "text": "导入文本",
}


def validation_message(errors):
    """避免在创建表单和逐行导入错误中泄漏英文内部字段。"""
    messages = []
    for error in errors:
        loc = "／".join(str(p + 1) if isinstance(p, int) else FIELD_LABELS.get(p, "未知字段")
                       for p in error["loc"])
        kind, context = error["type"], error.get("ctx", {})
        if kind == "extra_forbidden":
            message = "不支持该字段，请使用当前标注模板。"
        elif kind == "missing":
            message = "该项必填。"
        elif kind == "value_error":
            message = str(context.get("error", "填写内容不符合要求。"))
        elif kind in {"too_long", "string_too_long"}:
            message = f"最多允许{context.get('max_length', '')}项或字符。"
        elif kind in {"too_short", "string_too_short"}:
            message = f"至少填写{context.get('min_length', '')}项或字符。"
        else:
            message = "类型或选项不正确，请按当前模板填写。"
        messages.append(f"{loc or '用例'}：{message}")
    return "；".join(messages)[:2000]


def validate_payload(value):
    try:
        return UnifiedCase.model_validate(value)
    except ValidationError as exc:
        raise ValueError(validation_message(exc.errors())) from exc
