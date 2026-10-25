"""人类阅读的时间使用 UTC+8；数据库、JWT、缓存和流式协议保留 UTC。"""

from datetime import timedelta, timezone


BEIJING = timezone(timedelta(hours=8))
