"""提取真实模型用量；未提供用量时返回空对象，避免将未知当作零。"""


def model_usage(message) -> dict[str, int]:
    usage = getattr(message, "usage_metadata", None) or {}
    if not usage:
        metadata = getattr(message, "response_metadata", None) or {}
        usage = metadata.get("token_usage") or {}
    result = {}
    for target, keys in {
        "prompt_tokens": ("input_tokens", "prompt_tokens"),
        "completion_tokens": ("output_tokens", "completion_tokens"),
        "total_tokens": ("total_tokens",),
    }.items():
        for key in keys:
            if isinstance(usage.get(key), int):
                result[target] = usage[key]
                break
    if "total_tokens" not in result and {"prompt_tokens", "completion_tokens"} <= result.keys():
        result["total_tokens"] = result["prompt_tokens"] + result["completion_tokens"]
    metadata = getattr(message, "response_metadata", None) or {}
    raw = metadata.get("token_usage") or {}
    details = usage.get("input_token_details") or raw.get("prompt_tokens_details") or {}
    cached = details.get("cache_read", details.get("cached_tokens", raw.get("prompt_cache_hit_tokens")))
    if isinstance(cached, int) and not isinstance(cached, bool) and cached >= 0:
        result["cached_input_tokens"] = cached
    missed = raw.get("prompt_cache_miss_tokens")
    if isinstance(missed, int) and not isinstance(missed, bool) and missed >= 0:
        result["uncached_input_tokens"] = missed
    return result
