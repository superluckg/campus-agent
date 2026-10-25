"""四层诊断、真实耗时及异常信息；缺失时间不补零。"""

from __future__ import annotations

import math

STAGES = ("rule", "embedding", "jev", "llm")
STATUSES = {"returned", "continued", "skipped", "not_reached", "failed", "unrecorded"}


def measured(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def normalize_trace(prediction, error_code=None):
    raw = {
        item["stage"]: item
        for item in prediction.get("stage_trace", [])
        if isinstance(item, dict) and item.get("stage") in STAGES
    }
    durations = prediction.get("signal_durations_ms") or {}
    errors = prediction.get("signal_errors") or {}
    exit_stage = {"llm_fallback": "llm"}.get(
        prediction.get("decision_stage"), prediction.get("decision_stage")
    )
    proven_exit = exit_stage in STAGES and bool(prediction.get("primary_intent")) and not error_code
    normalized = []
    for index, stage in enumerate(STAGES):
        item = dict(raw.get(stage, {}))
        duration = item.get("duration_ms", durations.get(stage))
        status = item.get("status")
        if status not in STATUSES:
            if item.get("skipped"):
                status = "skipped"
            elif stage in errors and (item or measured(duration)):
                status = "failed"
            elif item.get("accepted") is True:
                status = "returned" if stage == exit_stage else "continued"
            elif item or measured(duration):
                status = "continued"
            elif proven_exit and index > STAGES.index(exit_stage):
                status = "not_reached"
            else:
                status = "unrecorded"
        if error_code == "INTENT_FAILED" and status == "returned":
            status = "failed"
        duration = (
            float(duration)
            if measured(duration) and status not in {"skipped", "not_reached", "unrecorded"}
            else None
        )
        default_reason = {
            "returned": "决策字段已确定，返回路由层。",
            "continued": "未达到返回条件，继续下一层。",
            "skipped": "当前输入不适用此阶段。",
            "not_reached": "前面的阶段已返回。",
            "failed": "阶段执行失败。",
            "unrecorded": "未采集此阶段信息。",
        }[status]
        item.update(
            stage=stage,
            status=status,
            duration_ms=duration,
            reason=item.get("reason") or default_reason,
        )
        if stage in (prediction.get("source_scores") or {}):
            item.setdefault("source_score", prediction["source_scores"][stage])
        if stage == "embedding":
            substeps = {
                key: value
                for key, value in durations.items()
                if key.startswith("embedding_") and measured(value)
            }
            if substeps:
                item["substeps_ms"] = substeps
        normalized.append(item)
    return normalized


def complete_result(result):
    result.stage_trace = normalize_trace(result.model_dump(mode="json"))
    return result


def attach_diagnostics(exc, trace=(), durations=None, errors=None, calls=None):
    prior = getattr(exc, "intent_diagnostics", {})
    value = {
        **prior,
        "stage_trace": [
            *(item for item in prior.get("stage_trace", []) if item.get("status") != "unrecorded"),
            *trace,
        ],
        "signal_durations_ms": {**(durations or {}), **prior.get("signal_durations_ms", {})},
        "signal_errors": {**(errors or {}), **prior.get("signal_errors", {})},
        "model_calls": {**prior.get("model_calls", {}), **(calls or {})},
    }
    value["stage_trace"] = normalize_trace(value, "INTENT_FAILED")
    failed_indices = [
        index for index, item in enumerate(value["stage_trace"]) if item["status"] == "failed"
    ]
    if failed_indices:
        for item in value["stage_trace"][max(failed_indices) + 1 :]:
            if item["status"] == "unrecorded":
                item.update(status="not_reached", reason="前面阶段被终止，未执行此阶段。")
    exc.intent_diagnostics = value


def funnel_summary(rows):
    result = []
    for stage in STAGES:
        entries = [
            (
                row,
                next(
                    item
                    for item in normalize_trace(row.get("prediction") or {}, row.get("error_code"))
                    if item["stage"] == stage
                ),
            )
            for row in rows
        ]
        executed = [
            (row, item)
            for row, item in entries
            if item["status"] in {"returned", "continued", "failed"}
        ]
        returned = sum(
            item["status"] == "returned" and not row.get("error_code") for row, item in entries
        )
        timings = [item["duration_ms"] for _, item in executed if measured(item["duration_ms"])]
        result.append(
            {
                "stage": stage,
                "executed_count": len(executed),
                "returned_count": returned,
                "exit_share": returned / len(rows) if rows else None,
                "measured_count": len(timings),
                "mean_duration_ms": sum(timings) / len(timings) if timings else None,
            }
        )
    return result
