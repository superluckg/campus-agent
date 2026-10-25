"""只用实际执行产物评分；五项意图标准，检索调用与Case分别汇总。"""

import json
from collections import defaultdict
from statistics import mean

from app.evaluation.intent_metrics import summarize as intent_summary
from app.evaluation.retrieval.matching import OriginalEvidenceMatcher
from app.evaluation.retrieval.scoring import coverage, unit_key
from app.schemas.unified_evaluation import AGENT_FOR_INTENT, Evidence, decision_reference, validate_payload

GENERATION_KEYS = ("correctness", "faithfulness", "relevance", "completeness", "helpfulness", "refusal_quality")
RETRIEVAL_KEYS = ("hit", "precision", "unit_recall", "mrr", "evidence_item_recall", "evidence_coverage")
INTENT_FIELDS = {
    "expected_intent": ("primary_intent", "intent_accuracy"),
    "expected_route": ("route", "route_accuracy"),
    "expected_agents": ("agents", "agent_exact_match"),
    "expected_sub_intents": ("sub_intents", "sub_intents_exact_match"),
    "expected_policy": ("policy_decision", "policy_accuracy"),
}


def normalize_policy(value):
    return "refuse" if value in {"refuse", "refuse_sensitive", "refuse_unauthorized"} else value


def normalized_prediction(prediction):
    return {**prediction, "policy_decision": normalize_policy(prediction.get("policy_decision"))}


def decision_key(prediction):
    prediction = normalized_prediction(prediction)
    return json.dumps({actual: sorted(prediction[actual]) if isinstance(prediction.get(actual), list)
                       else prediction.get(actual) for actual, _ in INTENT_FIELDS.values()}, sort_keys=True)


def grade_intent(reference, prediction):
    prediction = normalized_prediction(prediction)
    metrics = {}
    for expected, (actual, metric) in INTENT_FIELDS.items():
        gold, value = reference[expected], prediction.get(actual)
        metrics[metric] = float(set(gold) == set(value) if isinstance(gold, list) and isinstance(value, list)
                                else gold == value)
    metrics["decision_exact_match"] = float(all(metrics.values()))
    return metrics


def parse_import(text):
    cases, errors = [], []
    lines = text.lstrip("\ufeff").splitlines()
    if len(lines) > 2000:
        return [], [{"line": 0, "message": "一次最多导入两千行。"}]

    def invalid_constant(value):
        raise ValueError("导入不允许非有限数值。")

    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            cases.append(validate_payload(json.loads(line, parse_constant=invalid_constant)))
        except (ValueError, TypeError) as exc:
            message = "格式不正确，每行必须是一个完整对象。" if isinstance(exc, json.JSONDecodeError) else str(exc)
            errors.append({"line": number, "message": message[:2000]})
    if not cases and not errors:
        errors.append({"line": 0, "message": "没有可导入用例。"})
    return cases, errors


def context_basis(payload, state=None):
    messages = state.get("recent_messages", []) if state is not None else payload.get("conversation_context", [])
    return [{"source_kind": "recent_message", "source_ref": f"message:{index}", "content": message["content"]}
            for index, message in enumerate(messages[-8:], 1)]


def evidence_items(payload):
    parts = payload.get("expected_sub_intents") or [payload["expected_intent"]]
    return {f"E{index:02d}": {
        **Evidence.model_validate(item).model_dump(mode="json"),
        "related_intents": item.get("related_intents") or parts,
    } for index, item in enumerate(payload["evidence"], 1)}


def prepare_case(case, corpus):
    # 每条证据独立验证；绝不以被测检索结果反推标准相关集。
    payload = validate_payload(case["payload"]).case_payload()
    matched = {}
    for ref, evidence in evidence_items(payload).items():
        pool = {**corpus, "units": [unit for unit in corpus["units"] if unit["source_kind"] ==
                                   ("local" if evidence["source_kind"] == "knowledge_file" else "web")]}
        standard = {"text": evidence["content"], **{key: evidence[key]
                     for key in ("filename", "source_key", "source_url", "page_number")}}
        keys = set()
        for intent in evidence["related_intents"]:
            agent = AGENT_FOR_INTENT.get(intent)
            agents = [agent] if agent else list({a for source in corpus.get("web_sources", {}).values()
                                                for a in source.get("allowed_agents", [])}) or [None]
            located = set()
            for allowed_agent in agents:
                located.update(OriginalEvidenceMatcher(pool).match({"agent_name": allowed_agent, "evidence": [standard]})["relevant_unit_keys"])
            if not located:
                raise ValueError(f"{case['case_label']}：第{int(ref[1:])}条标准原文未在对应固定资料或允许来源中完整定位。")
            keys.update(located)
        matched[ref] = sorted(keys)
    return {**case, "payload": payload, "matched_evidence": matched}


def retrieval_scores(case, calls, state):
    from app.evaluation.retrieval.scoring import serialize_unit

    evidence = evidence_items(case["payload"])
    rows, retrieved = [], []
    for call in calls:
        if call["tool"] not in {"agentic_retrieval", "fixed_web_search"}:
            continue
        artifact = call.get("artifact") or {}
        if isinstance(artifact, list):
            artifact = {"documents": artifact}
        documents = list(artifact.get("documents", []))[:5]
        failed = call.get("status") == "execution_failed"
        if not failed:
            retrieved.append((call["agent"], documents))
        refs = [ref for ref, item in evidence.items() if any(
            AGENT_FOR_INTENT.get(intent) == call["agent"] for intent in item["related_intents"])]
        gold = {key for ref in refs for key in case["matched_evidence"].get(ref, [])}
        keys = [unit_key(doc) for doc in documents]
        values = [coverage(evidence[ref]["content"], [doc.page_content for doc in documents
                  if unit_key(doc) in case["matched_evidence"].get(ref, [])]) for ref in refs]
        metrics = {key: None for key in RETRIEVAL_KEYS}
        if gold and not failed:
            found = set(keys) & gold
            first = next((rank for rank, key in enumerate(keys, 1) if key in gold), None)
            metrics.update(hit=float(bool(found)), precision=len(found) / 5, unit_recall=len(found) / len(gold),
                           mrr=1 / first if first else 0, evidence_item_recall=mean(value >= .70 for value in values),
                           evidence_coverage=mean(values))
        reason = "检索执行失败，本次调用未评分。" if failed else None if gold else "没有适用的标准相关集，本次调用未评分。"
        rows.append({"call_id": call["call_id"], "agent": call["agent"],
                     "related_intents": sorted({intent for ref in refs for intent in evidence[ref]["related_intents"]
                                                if AGENT_FOR_INTENT.get(intent) == call["agent"]}),
                     "query": call["inputs"].get("query"), "duration_ms": call["duration_ms"], "k": 5,
                     "documents": [serialize_unit(doc, include_scores=True) for doc in documents],
                     "diagnostics": artifact.get("diagnostics", {}), "metrics": metrics,
                     "status": "execution_failed" if failed else "completed" if gold else "not_applicable",
                     "error_message": call.get("error_message"), "reason": reason})
    details = []
    for ref, item in evidence.items():
        texts = [doc.page_content for agent, docs in retrieved
                 if any(AGENT_FOR_INTENT.get(intent) == agent for intent in item["related_intents"])
                 for doc in docs if unit_key(doc) in case["matched_evidence"].get(ref, [])]
        value = coverage(item["content"], texts)
        details.append({"evidence_ref": ref, "coverage": value, "obtained": value >= .70})
    measured = any(row["status"] == "completed" for row in rows)
    failed = any(row["status"] == "execution_failed" for row in rows)
    return {"calls": rows, "evidence_details": details,
            "status": "execution_failed" if failed else "completed" if measured else "not_applicable",
            "reason": None if measured or failed else "本次未调用知识检索。" if not rows else "没有适用的标准相关集，检索指标未评分。",
            "metrics": {key: mean(values) if (values := [row["metrics"][key] for row in rows
                                                        if row["metrics"][key] is not None]) else None
                        for key in RETRIEVAL_KEYS}}


def summarize(rows, total):
    # 中止的当前尝试只保留诊断，不把未完成执行当作模型错误或有效评分。
    rows = [row for row in rows if row.get("status") != "cancelled"]
    groups = defaultdict(list)
    for row in rows:
        groups[row["case"]["case_id"]].append(row)

    def equal_case(getter):
        values = []
        for group in groups.values():
            measured = [value for row in group if isinstance(value := getter(row), (int, float))]
            if measured:
                values.append(mean(measured))
        return mean(values) if values else None

    intent_rows = [
        {
            "case": {
                "id": row["case"]["case_id"],
                "reference_outputs": decision_reference(row["case"]["payload"]),
            },
            "prediction": normalized_prediction(row.get("prediction", {})),
            "metrics": row.get("intent_metrics", {}),
            "intent_duration_ms": row.get("intent_duration_ms"),
            "error_code": row.get("intent_error"),
        }
        for row in rows
    ]
    intent = intent_summary(intent_rows) if intent_rows else {}
    for key in {key for row in rows for key in row.get("intent_metrics", {})}:
        intent[key] = equal_case(lambda row: row.get("intent_metrics", {}).get(key))
    for key, eligible, hit in (
        (
            "false_rejection_rate",
            lambda g: (
                g["expected_intent"] not in {"out_of_scope", "unclear"}
                and g["expected_policy"] == "allow"
            ),
            lambda p: (
                p.get("primary_intent") == "out_of_scope"
                or normalize_policy(p.get("policy_decision")) == "refuse"
            ),
        ),
        (
            "false_acceptance_rate",
            lambda g: g["expected_intent"] == "out_of_scope",
            lambda p: p.get("route") in {"single_agent", "multi_agent"},
        ),
        (
            "unsafe_acceptance_rate",
            lambda g: g["expected_policy"] != "allow",
            lambda p: (
                p.get("route") in {"single_agent", "multi_agent"}
                or p.get("policy_decision") == "allow"
            ),
        ),
    ):
        intent[key] = equal_case(
            lambda row: (
                float(hit(row.get("prediction", {})))
                if eligible(decision_reference(row["case"]["payload"]))
                else None
            )
        )
    intent["degraded_rate"] = equal_case(
        lambda row: float(bool(row.get("prediction", {}).get("signal_errors")))
    )
    intent["failure_rate"] = equal_case(lambda row: float(bool(row.get("intent_error"))))
    case_durations = [
        mean(values)
        for group in groups.values()
        if (
            values := [
                row["intent_duration_ms"]
                for row in group
                if isinstance(row.get("intent_duration_ms"), (int, float))
            ]
        )
    ]
    if case_durations:
        import numpy as np

        intent.update(
            intent_mean_ms=mean(case_durations),
            intent_min_ms=min(case_durations),
            intent_max_ms=max(case_durations),
        )
        intent["intent_p50_ms"], intent["intent_p95_ms"] = map(
            float, np.percentile(case_durations, [50, 95])
        )
    from app.intent.diagnostics import normalize_trace

    for stage in intent.get("funnel_summary", []):

        def entry(row):
            return next(
                item
                for item in normalize_trace(row.get("prediction", {}), row.get("intent_error"))
                if item["stage"] == stage["stage"]
            )

        stage["exit_share"] = equal_case(
            lambda row: float(entry(row)["status"] == "returned" and not row.get("intent_error"))
        )
        stage["mean_duration_ms"] = equal_case(lambda row: entry(row)["duration_ms"])
    if rows:
        from sklearn.metrics import classification_report, confusion_matrix

        from app.schemas.enums import IntentName

        labels = [item.value for item in IntentName]
        gold = [decision_reference(row["case"]["payload"])["expected_intent"] for row in rows]
        actual = [row.get("prediction", {}).get("primary_intent") or "__error__" for row in rows]
        weights = [1 / len(groups[row["case"]["case_id"]]) for row in rows]
        report = classification_report(
            gold, actual, labels=labels, sample_weight=weights, output_dict=True, zero_division=0
        )
        intent["per_intent"] = {label: report[label] for label in labels}
        intent["macro_f1"] = mean(
            report[label]["f1-score"] for label in labels if report[label]["support"]
        )
        intent["confusion_labels"] = labels + ["__error__"]
        intent["confusion_matrix"] = confusion_matrix(
            gold, actual, labels=labels + ["__error__"], sample_weight=weights
        ).tolist()[:-1]
    repeated = [group for group in groups.values() if len(group) > 1]
    if repeated:
        intent["decision_agreement_rate"] = mean(
            not any(row.get("intent_error") for row in group)
            and len({decision_key(row.get("prediction", {})) for row in group}) == 1
            for group in repeated
        )
    return {
        "case_count": len(groups),
        "total_attempts": total,
        "completed_attempts": sum(row.get("status") != "cancelled" for row in rows),
        "execution_failure_count": sum(bool(row.get("error_code")) for row in rows),
        "judge_failure_count": sum(
            row.get("generation", {}).get("status") == "judge_failed" for row in rows
        ),
        "intent": intent,
        "retrieval": {
            **{
                key: equal_case(lambda row: row.get("retrieval", {}).get("metrics", {}).get(key))
                for key in RETRIEVAL_KEYS
            },
        },
        "generation": {
            key: equal_case(lambda row: row.get("generation", {}).get("metrics", {}).get(key))
            for key in GENERATION_KEYS
        },
        "duration_ms": equal_case(lambda row: row.get("duration_ms")),
    }
