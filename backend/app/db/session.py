"""异步数据库连接；导入模块不会主动连接数据库。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from functools import lru_cache
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings


class Database:
    def __init__(self, url: str) -> None:
        self.engine: AsyncEngine = create_async_engine(
            url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=5,
            hide_parameters=True,
        )
        self.session_factory = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def close(self) -> None:
        await self.engine.dispose()


@lru_cache(maxsize=1)
def get_database() -> Database:
    return Database(get_settings().database_url)

