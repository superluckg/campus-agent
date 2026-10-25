"""把 API 字符串边界统一转换为 SQLAlchemy 使用的 UUID。"""

from uuid import UUID


def as_uuid(value) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))
