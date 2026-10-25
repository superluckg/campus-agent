"""多 Agent 合并和降级拼接共用同一证据身份及编号协议。"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from langchain_core.language_models.chat_models import BaseChatModel

from app.core.model_usage import model_usage
from app.core.observability import current_observation, span
from app.core.response_stream import ResponseObserver, StructuredTextStream
from app.schemas.agent import AgentResult, ComposedAnswer, FinalResponse
from app.schemas.enums import RunStatus

from .evidence import normalize_answer, publish_answer


class ResponseComposer:
    def __init__(self, model: BaseChatModel, prompt_path: Path) -> None:
        self.model = model.with_structured_output(
            ComposedAnswer, method="function_calling", include_raw=True
        )
        self.streaming_model = model.bind_tools([ComposedAnswer], tool_choice="ComposedAnswer")
        self.prompt = prompt_path.read_text(encoding="utf-8")

    async def compose(
        self, results: Sequence[AgentResult], *, observer: ResponseObserver | None = None
    ) -> FinalResponse:
        successful = [r for r in results if r.status != RunStatus.FAILED]
        if len(results) == 1:
            result = results[0]
            return FinalResponse(
                content=result.answer,
                agents=[result.agent_name],
                citations=result.citations,
                partial=result.status != RunStatus.SUCCESS or not result.evidence_sufficient,
                unresolved_points=result.unresolved_points,
                status=result.status,
                metadata=result.metadata,
            )
        if not successful:
            return self.fallback(results)
        evidence = {key: citation for r in successful for key, citation in r.evidence.items()}
        payload = [
            {
                "agent": r.agent_name.value,
                "answer": r.evidence_answer if r.evidence_answer is not None else r.answer,
                "evidence_sufficient": r.evidence_sufficient,
                "unresolved_points": r.unresolved_points,
                "status": r.status.value,
            }
            for r in results
        ]
        try:
            prompt = self.prompt + "\n\nAgentResults:\n" + json.dumps(payload, ensure_ascii=False)
            async with span("compose.model", "回答合并模型调用") as details:
                if observer is None:
                    raw = await self.model.ainvoke(prompt)
                    details["usage"] = model_usage(raw["raw"])
                    if raw.get("parsing_error") or raw.get("parsed") is None:
                        raise ValueError("合并模型未形成有效的结构化结果。")
                    response = raw["parsed"]
                else:
                    extractor = StructuredTextStream("ComposedAnswer", "content")
                    assembled = None
                    async for chunk in self.streaming_model.astream(prompt, stream_usage=True):
                        assembled = chunk if assembled is None else assembled + chunk
                        content = extractor.feed(chunk)
                        if content is not None:
                            await publish_answer(
                                observer, normalize_answer(content, evidence, preview=True)
                            )
                    details["usage"] = model_usage(assembled)
                    calls = assembled.tool_calls if assembled is not None else []
                    answers = [call for call in calls if call["name"] == "ComposedAnswer"]
                    if len(answers) != 1:
                        raise ValueError("合并模型没有返回唯一完整结果。")
                    response = ComposedAnswer.model_validate(answers[0]["args"])
            response = self._final(
                response.content, results, response.unresolved_points, response.partial
            )
        except Exception as exc:
            observation = current_observation()
            if observation is not None:
                await observation.emit(
                    "compose.degraded",
                    {
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                        "reason": "合并模型失败，使用已完成Agent的原始回答。",
                        "status": "degraded",
                    },
                )
            response = self.fallback(results)
        if observer is not None:
            await publish_answer(observer, response)
        observation = current_observation()
        if observation and response.metadata.get("citation_warning"):
            await observation.emit("citation.invalid", response.metadata)
        return response

    @staticmethod
    def _final(content, results, unresolved=(), partial=False):
        evidence = {key: citation for r in results for key, citation in r.evidence.items()}
        normalized = normalize_answer(content, evidence)
        # 上游已发现的异常不能通过合并时删除标号而消失。
        invalid = list(
            dict.fromkeys(
                [key for r in results for key in r.metadata.get("invalid_evidence_ids", [])]
                + normalized.metadata.get("invalid_evidence_ids", [])
            )
        )
        if invalid:
            normalized.metadata.update(
                invalid_evidence_ids=invalid, citation_warning="部分引用无法定位，相关内容需核验。"
            )
        partial = (
            partial
            or bool(invalid)
            or any(r.status != RunStatus.SUCCESS or not r.evidence_sufficient for r in results)
        )
        points = list(
            dict.fromkeys([*unresolved, *(p for r in results for p in r.unresolved_points)])
        )
        return FinalResponse(
            content=normalized.content,
            citations=normalized.citations,
            metadata=normalized.metadata,
            agents=[r.agent_name for r in results],
            partial=partial,
            unresolved_points=points,
            status=RunStatus.PARTIAL if partial else RunStatus.SUCCESS,
        )

    @staticmethod
    def fallback(results: Sequence[AgentResult]) -> FinalResponse:
        blocks = [
            f"### {r.agent_name.value}\n"
            f"{r.evidence_answer if r.evidence_answer is not None else r.answer}"
            for r in results
            if r.status != RunStatus.FAILED
        ]
        return ResponseComposer._final("\n\n".join(blocks) or "本次请求未能生成有效回答。", results)
