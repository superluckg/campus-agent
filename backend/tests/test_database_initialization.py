"""最终结构初始化、当前摘要和官网缓存的隔离数据库验收。"""

import asyncio
import os
from types import SimpleNamespace

import pytest
from langchain_core.documents import Document
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.db.models import Conversation, ConversationSummary, User, WebSnapshot, WebSource
from app.db.repositories.conversations import ConversationRepository
from app.db.repositories.memory import MemoryRepository
from app.db.repositories.web_cache import WebCacheRepository
from app.db.repositories.users import UserRepository
from tests.test_conversation_database import initialize_environment, isolated_database


pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(
    os.getenv("RUN_UNIFIED_DB_TESTS") != "1", reason="显式启用隔离 PostgreSQL 验收"
)]


async def test_workspace_reuses_existing_owner_without_changing_any_records():
    async with isolated_database() as (database, _):
        async with database.session() as session:
            owner = User(username="developer", password_hash="existing-hash", role="retained-role", is_active=False)
            other = User(username="other-owner", password_hash="other-hash", role="other-role")
            session.add_all([owner, other])
            await session.flush()
            session.add_all([
                Conversation(user_id=owner.user_id, title="默认归属的会话"),
                Conversation(user_id=other.user_id, title="其他归属的会话"),
            ])
            identifier = owner.user_id
            unchanged = (owner.username, owner.password_hash, owner.role, owner.is_active, owner.updated_at)
        repository = UserRepository(database)
        assert await repository.ensure_workspace_user() == str(identifier)
        async with database.session() as session:
            owner = await session.get(User, identifier)
            assert (owner.username, owner.password_hash, owner.role, owner.is_active, owner.updated_at) == unchanged
            assert await session.scalar(select(func.count(User.user_id))) == 2
            assert await session.scalar(select(func.count(Conversation.conversation_id))) == 2


async def test_workspace_concurrent_initialization_creates_one_stable_owner():
    async with isolated_database() as (database, _):
        repository = UserRepository(database)
        identifiers = await asyncio.gather(*(repository.ensure_workspace_user() for _ in range(8)))
        assert len(set(identifiers)) == 1
        assert await repository.ensure_workspace_user() == identifiers[0]
        async with database.session() as session:
            owners = list((await session.scalars(select(User))).all())
            assert len(owners) == 1
            assert str(owners[0].user_id) == identifiers[0]
            assert owners[0].password_hash == "!"


async def test_existing_structure_validation_preserves_rows_and_sequence_and_rejects_drift():
    async with isolated_database() as (database, environment):
        async with database.session() as session:
            user = User(username="structure-test", password_hash="unused", role="developer")
            session.add(user)
            await session.flush()
            identifier = user.user_id
            number = await session.scalar(text("SELECT nextval('observability.eval_case_number_seq')"))
        await initialize_environment(environment)
        async with database.session() as session:
            assert (await session.get(User, identifier)).username == "structure-test"
            assert await session.scalar(text("SELECT last_value FROM observability.eval_case_number_seq")) == number
            assert await session.scalar(text(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' "
                "AND table_type='BASE TABLE'"
            )) == 0
            await session.execute(text("ALTER TABLE app.users ADD COLUMN unexpected_column text"))
        with pytest.raises(AssertionError, match="结构不符合"):
            await initialize_environment(environment)
        async with database.session() as session:
            assert await session.scalar(text(
                "SELECT count(*) FROM information_schema.columns WHERE table_schema='app' "
                "AND table_name='users' AND column_name='unexpected_column'"
            )) == 1
            assert await session.scalar(select(func.count(User.user_id))) == 1


async def test_each_conversation_keeps_one_current_summary():
    async with isolated_database() as (database, _):
        async with database.session() as session:
            user = User(username="summary-test", password_hash="unused", role="developer")
            session.add(user)
            await session.flush()
            user_id = str(user.user_id)
        conversation = await ConversationRepository(database).create(user_id)
        repository = MemoryRepository(database, SimpleNamespace())
        first = await repository.add_summary(str(conversation.conversation_id), "先讨论校园网", 20, None)
        second = await repository.add_summary(str(conversation.conversation_id), "当前讨论校园手续", 40, None)
        assert first.summary_id == second.summary_id
        assert await repository.latest_summary(str(conversation.conversation_id)) == "当前讨论校园手续"
        async with database.session() as session:
            assert await session.scalar(select(func.count(ConversationSummary.summary_id))) == 1


async def test_web_cache_replaces_the_complete_batch_and_failure_keeps_current_pages():
    async with isolated_database() as (database, _):
        async with database.session() as session:
            session.add(WebSource(source_key="cache-test", display_name="官网", url="https://example.com",
                                  adapter="static_html", cache_ttl_seconds=60, enabled=True, config_snapshot={}))
        cache = WebCacheRepository(database)
        def page(value, title="说明"):
            return Document(page_content=value, metadata={"title": title, "source_url": "https://example.com"})
        await cache.save("cache-test", [page("之前的内容")], 60)
        await cache.save("cache-test", [page("当前页面一"), page("当前页面二")], 60)
        assert {document.page_content for document in await cache.fresh("cache-test")} == {"当前页面一", "当前页面二"}
        with pytest.raises(DBAPIError):
            await cache.save("cache-test", [page("不完整的替换", "x" * 501)], 60)
        assert {document.page_content for document in await cache.latest_success("cache-test")} == {"当前页面一", "当前页面二"}
        async with database.session() as session:
            assert await session.scalar(select(func.count(WebSnapshot.snapshot_id))) == 2
