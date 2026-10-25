"""显式启用的隔离 PostgreSQL 验收，只清理本次创建的数据库及测试副本。"""

import asyncio
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from dotenv import load_dotenv
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.db.models import (
    Conversation, ConversationSummary, LongTermMemory, Message, Trace, TraceEvent,
    UploadedFile, UploadedFileContent, User,
)
from app.db.repositories.conversations import ConversationRepository
from app.db.session import Database
from app.schemas.agent import Citation
from app.services.conversation_activity import ConversationActivity, ConversationBusyError
from app.services.conversation_management import ConversationManagementService


ROOT = Path(__file__).parents[2]
pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(
    os.getenv("RUN_CONVERSATION_DB_TESTS") != "1", reason="显式启用隔离数据库验收")]


async def initialize_environment(environment):
    result = await asyncio.to_thread(subprocess.run,
        [sys.executable, "-B", "-m", "app.db.initialize"],
        cwd=ROOT / "backend", env=environment, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr[-4000:]


@asynccontextmanager
async def isolated_database():
    load_dotenv(ROOT / ".env")
    url = make_url(get_settings(require_secrets=False).database_url)
    name = "conversation_test_" + uuid4().hex
    admin = create_async_engine(url, isolation_level="AUTOCOMMIT", hide_parameters=True)
    database = None
    created = False
    try:
        async with admin.connect() as connection:
            assert await connection.scalar(text("SELECT 1 FROM pg_database WHERE datname=:name"), {"name": name}) is None
            await connection.execute(text('CREATE DATABASE "' + name + '"'))
            created = True
        environment = {**os.environ, "POSTGRES_DB": name, "POSTGRES_HOST": url.host,
                       "POSTGRES_PORT": str(url.port), "POSTGRES_USER": url.username,
                       "POSTGRES_PASSWORD": url.password}
        await initialize_environment(environment)
        database = Database(url.set(database=name).render_as_string(hide_password=False))
        yield database, environment
    finally:
        if database:
            await database.close()
        if created:
            assert name.startswith("conversation_test_") and name != url.database
            async with admin.connect() as connection:
                await connection.execute(text('DROP DATABASE "' + name + '"'))
        await admin.dispose()


async def test_titles_latest_user_order_and_transactional_deletion(tmp_path, monkeypatch):
    async with isolated_database() as (database, environment):
        repository = ConversationRepository(database)
        activity = ConversationActivity()
        redis = SimpleNamespace(client=SimpleNamespace(delete=AsyncMock()))
        management = ConversationManagementService(database, activity, tmp_path / "uploads", redis)
        async with database.session() as session:
            user = User(username="temporary-chat-test", password_hash="unused", role="developer")
            other = User(username="temporary-other-user", password_hash="unused", role="developer")
            session.add_all([user, other])
            await session.flush()
            user_id, other_id = str(user.user_id), str(other.user_id)
            custom_saved = Conversation(user_id=user.user_id, title="自定义会话名称", title_status="custom", status="active")
            session.add(custom_saved)
            await session.flush()
            custom_saved_id = str(custom_saved.conversation_id)

        await initialize_environment(environment)
        custom_view = await repository.get_owned(user_id, custom_saved_id)
        assert (custom_view.title, custom_view.title_status) == ("自定义会话名称", "custom")
        await repository.add_message(custom_saved_id, "user", "新消息不覆盖自定义名称")
        assert (await repository.get_owned(user_id, custom_saved_id)).title == "自定义会话名称"

        conversation = await repository.create(user_id)
        conversation_id = str(conversation.conversation_id)
        other_chat = await repository.create(user_id, "自定义名称")
        other_chat_id = str(other_chat.conversation_id)
        empty_chat = await repository.create(user_id)
        request_id, trace_id = uuid4(), uuid4()
        instant = datetime(2026, 10, 10, 16, 0, tzinfo=timezone.utc)
        async with database.session() as session:
            session.add(Trace(trace_id=trace_id, request_id=request_id, user_id=user.user_id,
                              conversation_id=conversation.conversation_id, status="success", started_at=instant))
        first = await repository.add_message(conversation_id, "user", "你好", request_id=str(request_id), trace_id=str(trace_id))
        assert (await repository.get_owned(user_id, conversation_id)).title == "你好"
        await repository.add_message(conversation_id, "assistant", "你好！", citations=[Citation(citation_index=1, title="临时测试引用")],
            request_id=str(request_id), trace_id=str(trace_id))
        newer = await repository.add_message(other_chat_id, "user", "校园网络")
        await repository.add_message(conversation_id, "user", "校园网怎么充值")
        await repository.set_generated_title(user_id, conversation_id, "校园网充值说明")
        await repository.set_generated_title(user_id, conversation_id, "不应覆盖已生成标题")
        await repository.set_generated_title(user_id, other_chat_id, "不应覆盖自定义标题")
        assert (await repository.get_owned(user_id, conversation_id)).title == "校园网充值说明"
        assert (await repository.get_owned(user_id, other_chat_id)).title == "自定义名称"

        directory = management.upload_root / conversation_id
        directory.mkdir(parents=True)
        copy_a, copy_b = directory / "a.txt", directory / "b.txt"
        copy_a.write_text("temporary upload A", encoding="utf-8")
        copy_b.write_text("temporary orphan copy B", encoding="utf-8")
        original = tmp_path / "original-source.txt"
        original.write_text("original stays intact", encoding="utf-8")
        outside_copy = management.upload_root / other_chat_id / "keep.txt"
        outside_copy.parent.mkdir()
        outside_copy.write_text("other chat stays intact", encoding="utf-8")
        async with database.session() as session:
            messages = (await session.scalars(select(Message).where(Message.conversation_id == conversation.conversation_id))).all()
            for item in messages:
                item.created_at = instant if item.role == "user" else datetime(2099, 1, 1, tzinfo=timezone.utc)
            row = await session.get(Message, newer.message_id)
            row.created_at = datetime(2026, 10, 10, 17, 0, tzinfo=timezone.utc)
            for identifier in (custom_saved.conversation_id, empty_chat.conversation_id):
                row = await session.get(Conversation, identifier)
                row.created_at = datetime(2026, 10, 1, tzinfo=timezone.utc)
            custom_saved_message = await session.scalar(select(Message).where(Message.conversation_id == custom_saved.conversation_id))
            custom_saved_message.created_at = datetime(2026, 10, 1, tzinfo=timezone.utc)
            session.add(ConversationSummary(conversation_id=conversation.conversation_id, content="测试摘要", message_count=2))
            session.add(LongTermMemory(user_id=user.user_id, memory_type="preference", content="保留测试记忆", embedding=[0.0] * 1024,
                                       source_conversation_id=conversation.conversation_id, source_message_ids=[str(first.message_id)]))
            session.add(TraceEvent(trace_id=trace_id, sequence_number=1, node_name="test", event_type="test", status="success", started_at=instant))
            upload = UploadedFile(user_id=user.user_id, conversation_id=conversation.conversation_id,
                original_filename="original-source.txt", relative_path=conversation_id + "/a.txt", extension="txt",
                size_bytes=18, content_hash="a" * 64, parse_status="parsed")
            session.add(upload)
            await session.flush()
            session.add(UploadedFileContent(file_id=upload.file_id, extracted_text="测试抽取文本"))
        overview = await repository.list_overviews(user_id)
        assert [str(item.conversation_id) for item, _ in overview][:2] == [other_chat_id, conversation_id]
        assert dict((str(item.conversation_id), timestamp) for item, timestamp in overview)[conversation_id] == instant
        assert dict((str(item.conversation_id), timestamp) for item, timestamp in overview)[str(empty_chat.conversation_id)] is None

        with activity.operation(conversation_id):
            with pytest.raises(LookupError):
                await management.delete(other_id, conversation_id)
            with pytest.raises(ConversationBusyError):
                await management.delete(user_id, conversation_id)
        with pytest.raises(LookupError):
            await management.delete(user_id, str(uuid4()))

        unlink = Path.unlink
        def fail_second(path, *args, **kwargs):
            if path == copy_b:
                raise PermissionError("test file lock")
            return unlink(path, *args, **kwargs)
        with monkeypatch.context() as patch:
            patch.setattr(Path, "unlink", fail_second)
            with pytest.raises(PermissionError):
                await management.delete(user_id, conversation_id)
        assert copy_a.read_text(encoding="utf-8") == "temporary upload A"
        assert await repository.get_owned(user_id, conversation_id) is not None
        redis.client.delete.assert_not_awaited()

        original_session = database.session
        calls = 0
        @asynccontextmanager
        async def fail_commit():
            nonlocal calls
            calls += 1
            async with original_session() as session:
                yield session
                if calls == 2:
                    raise RuntimeError("simulated commit failure")
        with monkeypatch.context() as patch:
            patch.setattr(database, "session", fail_commit)
            with pytest.raises(RuntimeError, match="commit failure"):
                await management.delete(user_id, conversation_id)
        assert copy_a.exists() and copy_b.exists()
        assert await repository.get_owned(user_id, conversation_id) is not None

        await management.delete(user_id, conversation_id)
        assert not directory.exists()
        assert original.read_text(encoding="utf-8") == "original stays intact"
        assert outside_copy.read_text(encoding="utf-8") == "other chat stays intact"
        assert await repository.get_owned(user_id, conversation_id) is None
        async with database.session() as session:
            for table in ("app.messages", "app.uploaded_files", "memory.conversation_summaries"):
                assert await session.scalar(text("SELECT count(*) FROM " + table + " WHERE conversation_id=:id"), {"id": conversation.conversation_id}) == 0
            for table in ("app.message_citations", "app.uploaded_file_contents"):
                assert await session.scalar(text("SELECT count(*) FROM " + table)) == 0
            assert await session.scalar(text("SELECT count(*) FROM memory.long_term_memories")) == 1
            assert await session.scalar(text("SELECT count(*) FROM observability.traces")) == 1
            assert await session.scalar(text("SELECT count(*) FROM observability.trace_events")) == 1
            retained = await session.get(Trace, trace_id)
            assert retained.conversation_id == conversation.conversation_id
        cleared = redis.client.delete.await_args.args
        assert "recent_messages:" + conversation_id in cleared
        assert "sse:{%s}:events" % request_id in cleared
        assert not activity.is_busy(conversation_id)
