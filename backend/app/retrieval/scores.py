"""仅供Trace、阈值和消融观察使用的分数归一化。"""

from __future__ import annotations

from typing import Iterable


def min_max_normalize(values: Iterable[float]) -> list[float]:
    snapshot = [float(value) for value in values]
    if not snapshot:
        return []
    lower, upper = min(snapshot), max(snapshot)
    if upper == lower:
        return [1.0 if upper > 0 else 0.0 for _ in snapshot]
    return [(value - lower) / (upper - lower) for value in snapshot]

