"""代码评分与标准分类报告；不会调用裁判LLM。"""

from collections import defaultdict

import numpy as np
from sklearn.metrics import classification_report, confusion_matrix

from app.intent.diagnostics import funnel_summary
from app.schemas.enums import IntentName


def duration_summary(rows: list[dict]) -> dict:
    """仅汇总已采集的意图耗时；失败尝试的耗时同样保留。"""
    durations = [row["intent_duration_ms"] for row in rows
                 if row.get("intent_duration_ms") is not None]
    result = {"intent_duration_count": len(durations)}
    if durations:
        result.update(intent_min_ms=float(np.min(durations)),
                      intent_max_ms=float(np.max(durations)),
                      intent_mean_ms=float(np.mean(durations)))
    return result


def summarize(rows: list[dict]) -> dict:
    summary = {"attempt_count": len(rows), "case_count": len({row["case"]["id"] for row in rows})}
    metrics = defaultdict(list)
    for row in rows:
        for key, value in row["metrics"].items():
            metrics[key].append(value)
    summary["metric_counts"] = {key: len(values) for key, values in metrics.items()}
    summary.update({key: float(np.mean(values)) for key, values in metrics.items()})
    summary["failure_count"] = sum(bool(row.get("error_code")) for row in rows)
    summary["failure_rate"] = summary["failure_count"] / len(rows) if rows else None
    summary["degraded_count"] = sum(bool(row["prediction"].get("signal_errors")) for row in rows)
    summary["degraded_rate"] = summary["degraded_count"] / len(rows) if rows else None
    labelled = [row for row in rows if row["case"]["reference_outputs"].get("expected_intent")]
    labels = [intent.value for intent in IntentName]
    gold = [row["case"]["reference_outputs"]["expected_intent"] for row in labelled]
    actual = [row["prediction"].get("primary_intent") or "__error__" for row in labelled]
    if labelled:
        report = classification_report(gold, actual, labels=labels, output_dict=True, zero_division=0)
        summary["per_intent"] = {label: report[label] for label in labels}
        supported = [label for label in labels if report[label]["support"] > 0]
        summary["macro_f1"] = float(np.mean([report[label]["f1-score"] for label in supported]))
        summary["confusion_matrix"] = confusion_matrix(gold, actual, labels=labels + ["__error__"]).tolist()[:-1]
        summary["confusion_labels"] = labels + ["__error__"]
    durations = [row["intent_duration_ms"] for row in rows if row.get("intent_duration_ms") is not None]
    if durations:
        summary["intent_p50_ms"], summary["intent_p95_ms"] = map(float, np.percentile(durations, [50, 95]))
    summary.update(duration_summary(rows))
    usages = [row["prediction"].get("model_usage", {}) for row in rows]
    summary["usage_measured_count"] = sum(bool(usage) for usage in usages)
    summary["model_usage"] = {key: sum(usage.get(key, 0) for usage in usages)
                             for key in {key for usage in usages for key in usage}}
    supported_rows = [row for row in labelled if row["case"]["reference_outputs"]["expected_intent"]
                      not in {"out_of_scope", "unclear"} and not
                      str(row["case"]["reference_outputs"].get("expected_policy", "")).startswith("refuse_")]
    if supported_rows:
        summary["false_rejection_rate"] = sum(row["prediction"].get("primary_intent") == "out_of_scope"
            or str(row["prediction"].get("policy_decision", "")).startswith("refuse_")
            for row in supported_rows) / len(supported_rows)
    outside = [row for row in labelled if row["case"]["reference_outputs"]["expected_intent"] == "out_of_scope"]
    if outside:
        summary["false_acceptance_rate"] = sum(row["prediction"].get("route") in
            {"single_agent", "multi_agent"} for row in outside) / len(outside)
    refused = [row for row in rows if str(row["case"]["reference_outputs"].get("expected_policy", "")).startswith("refuse_")]
    if refused:
        summary["unsafe_acceptance_rate"] = sum(row["prediction"].get("route") in
            {"single_agent", "multi_agent"} or row["prediction"].get("policy_decision") == "allow"
            for row in refused) / len(refused)
    groups = defaultdict(list)
    for row in rows:
        groups[row["case"]["id"]].append(row)
    repeated = [group for group in groups.values() if len(group) > 1]
    if repeated:
        summary["agreement_rate"] = sum(not any(row.get("error_code") for row in group) and
            len({row["prediction"].get("primary_intent") for row in group}) == 1
            for group in repeated) / len(repeated)
    summary['funnel_summary'] = funnel_summary(rows)
    return summary
