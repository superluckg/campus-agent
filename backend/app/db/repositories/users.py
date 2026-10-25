"""复用默认工作台的数据归属记录，保留已有编号与字段。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.db.models import User
from app.db.session import Database


class UserRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def ensure_workspace_user(self) -> str:
        async with self.database.session() as session:
            lookup = select(User.user_id).where(User.username == "developer")
            identifier = await session.scalar(lookup)
            if identifier is None:
                identifier = await session.scalar(
                    insert(User).values(
                        username="developer", password_hash="!", role="developer", is_active=True,
                    ).on_conflict_do_nothing(index_elements=[User.username]).returning(User.user_id)
                )
                if identifier is None:
                    identifier = await session.scalar(lookup)
            if identifier is None:
                raise RuntimeError("无法准备工作台默认数据归属记录。")
            return str(identifier)
