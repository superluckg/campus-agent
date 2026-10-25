"""带敏感信息清理的日志辅助函数。"""

import logging
import re
from datetime import datetime
from typing import Any

from app.core.timezone import BEIJING


_SECRET_KEYS = {"api_key", "authorization", "cookie", "password", "token"}
_BEARER = re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)


def configure_beijing_logging() -> None:
    # 标准日志、uvicorn 日志中出现的 asctime 均按 UTC+8 转换。
    logging.Formatter.converter = staticmethod(
        lambda timestamp: datetime.fromtimestamp(timestamp, BEIJING).timetuple()
    )
    logging.basicConfig(format="%(asctime)s UTC+8 %(levelname)s %(name)s: %(message)s")
    for name in ("", "uvicorn", "uvicorn.error", "uvicorn.access"):
        for handler in logging.getLogger(name).handlers:
            formatter = handler.formatter
            if formatter and isinstance(formatter._style, logging.PercentStyle) and "%(asctime)" not in formatter._style._fmt:
                formatter._style._fmt = "%(asctime)s UTC+8 " + formatter._style._fmt


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "***" if key.lower() in _SECRET_KEYS else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return _BEARER.sub("Bearer ***", value)
    return value

