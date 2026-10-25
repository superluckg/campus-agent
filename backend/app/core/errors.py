"""供应商终止信号、额度识别与业务冲突错误。"""

import json


class ProviderStopped(RuntimeError):
    """不可被模型降级吞掉的供应商终止信号。"""


class QuotaExhausted(ProviderStopped):
    pass


class EvaluationConflict(ValueError):
    pass


def quota_failure(status, payload):
    # 普通429表示限流，只有明确耗尽信息才停止整轮。
    if status == 402:
        return True
    serialized = json.dumps(payload, ensure_ascii=False, default=str).casefold()
    return any(
        code in serialized
        for code in (
            "insufficient_quota",
            "insufficient_balance",
            "insufficient_credits",
            "credits_exhausted",
            "quota_exhausted",
            "credit_limit_exceeded",
            "payment_required",
            "余额不足",
            "额度耗尽",
        )
    )
