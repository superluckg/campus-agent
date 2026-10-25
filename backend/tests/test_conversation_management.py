"""会话命名与删除边界；文件检查只使用本轮测试新建的临时文件。"""

import asyncio
import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import workspace_user, get_container
from app.api.routes.conversations import router
from app.api.schemas import WorkspaceIdentity
from app.core.logging import configure_beijing_logging
from app.schemas.agent import FinalResponse
from app.services.chat_service import ChatRunService
from app.services.conversation_activity import ConversationActivity, ConversationBusyError
from app.services.conversation_management import ConversationFileError, ConversationManagementService
from app.services.conversation_title import ConversationTitle, ConversationTitleService


@pytest.mark.asyncio
async def test_greetings_and_model_failure_remain_retryable_then_title_is_fixed():
    conversation = SimpleNamespace(title_status="pending")
    repository = SimpleNamespace(
        get_owned=AsyncMock(return_value=conversation),
        list_messages=AsyncMock(return_value=[SimpleNamespace(role="user", content="你好")]),
        set_generated_title=AsyncMock(),
    )
    structured = SimpleNamespace(ainvoke=AsyncMock(side_effect=[
        ConversationTitle(has_topic=False), OSError("temporary provider failure"),
        ConversationTitle(has_topic=True, title="“标题：校园网充值与连接”"),
    ]))
    service = ConversationTitleService(repository, Mock(with_structured_output=Mock(return_value=structured)))
    await service.generate("user", "conversation")
    repository.set_generated_title.assert_not_awaited()
    await service.generate("user", "conversation")
    repository.set_generated_title.assert_not_awaited()
    repository.list_messages.return_value.append(SimpleNamespace(role="user", content="校园网怎么充值？"))
    await service.generate("user", "conversation")
    repository.set_generated_title.assert_awaited_once_with("user", "conversation", "校园网充值与连接")
    conversation.title_status = "generated"
    await service.generate("user", "conversation")
    assert structured.ainvoke.await_count == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["custom", "generated", None])
async def test_existing_titles_never_call_the_naming_model(state):
    repository = SimpleNamespace(get_owned=AsyncMock(
        return_value=SimpleNamespace(title_status=state) if state else None), list_messages=AsyncMock())
    model = SimpleNamespace(ainvoke=AsyncMock())
    await ConversationTitleService(repository, Mock(with_structured_output=Mock(return_value=model))).generate("u", "c")
    repository.list_messages.assert_not_awaited()
    model.ainvoke.assert_not_awaited()


def test_activity_excludes_deletion_and_new_work_and_releases_after_exceptions():
    activity = ConversationActivity()
    with pytest.raises(ValueError), activity.operation("c"):
        with pytest.raises(ConversationBusyError), activity.deletion("c"):
            pass
        raise ValueError("failed upload")
    assert not activity.is_busy("c")
    with activity.deletion("c"):
        with pytest.raises(ConversationBusyError), activity.operation("c"):
            pass
    release = activity.reserve("c")
    release()
    release()
    assert not activity.is_busy("c")


@pytest.mark.asyncio
async def test_queued_chat_and_post_response_title_and_memory_all_block_deletion():
    activity = ConversationActivity()
    title_started, memory_started, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def title(*args):
        title_started.set()
        await finish.wait()

    async def memory(*args):
        memory_started.set()
        await finish.wait()

    @asynccontextmanager
    async def lock(*args):
        yield True

    async def invoke(state):
        return {**state, "final_response": FinalResponse(content="回答")}

    service = ChatRunService(
        graph=SimpleNamespace(ainvoke=invoke),
        conversations=SimpleNamespace(add_message=AsyncMock(return_value=SimpleNamespace(message_id=uuid4()))),
        traces=SimpleNamespace(start=AsyncMock(return_value=SimpleNamespace(trace_id=uuid4())), finish=AsyncMock()),
        trace_sink=SimpleNamespace(event=AsyncMock()), redis=SimpleNamespace(conversation_lock=lock),
        memory=SimpleNamespace(maintain_after_turn=memory), recent_cache=SimpleNamespace(append=AsyncMock()),
        max_active_requests=1, activity=activity, title_service=SimpleNamespace(generate=title),
    )
    await service.semaphore.acquire()
    await service.start("u", "c", "校园问题")
    with pytest.raises(ConversationBusyError), activity.deletion("c"):
        pass
    service.semaphore.release()
    await asyncio.wait_for(asyncio.gather(title_started.wait(), memory_started.wait()), 2)
    with pytest.raises(ConversationBusyError), activity.deletion("c"):
        pass
    finish.set()
    while service.tasks:
        await asyncio.gather(*service.tasks)
    assert not activity.is_busy("c")


@pytest.mark.parametrize("kind", ["symlink", "junction"])
def test_upload_path_reparse_points_are_rejected_before_touching_files(tmp_path, monkeypatch, kind):
    conversation_id = str(uuid4())
    directory = tmp_path / conversation_id
    directory.mkdir()
    safe = directory / "safe.txt"
    safe.write_text("temporary copy", encoding="utf-8")
    service = ConversationManagementService(None, ConversationActivity(), tmp_path, None)
    if kind == "symlink":
        original = type(directory).is_symlink
        monkeypatch.setattr(type(directory), "is_symlink", lambda path: path == safe or original(path))
    else:
        original = type(directory).lstat
        monkeypatch.setattr(type(directory), "lstat", lambda path: SimpleNamespace(
            st_file_attributes=0x400, st_mode=original(path).st_mode) if path == safe else original(path))
    with pytest.raises(ConversationFileError):
        service._paths(conversation_id)
    assert safe.read_text(encoding="utf-8") == "temporary copy"


@pytest.mark.parametrize("error,code", [
    (LookupError(), 404), (ConversationBusyError("busy"), 409),
    (ConversationFileError(), 503), (PermissionError(), 503), (None, 200),
])
def test_delete_api_maps_errors_and_canonicalizes_uuid(error, code):
    identifier = str(uuid4())
    user = WorkspaceIdentity(user_id=str(uuid4()))
    deletion = AsyncMock(side_effect=error)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[workspace_user] = lambda: user
    app.dependency_overrides[get_container] = lambda: SimpleNamespace(
        conversation_management=SimpleNamespace(delete=deletion))
    response = TestClient(app).delete("/api/conversations/" + identifier.upper())
    assert response.status_code == code
    deletion.assert_awaited_once_with(user.user_id, identifier)
    if not error:
        assert response.json() == {"deleted": True, "conversation_id": identifier}


def test_log_formatter_uses_beijing_even_if_host_timezone_differs():
    original = logging.Formatter.converter
    try:
        configure_beijing_logging()
        record = logging.LogRecord("test", logging.INFO, "", 1, "message", (), None)
        record.created = 1791648184  # 2026-10-10 16:03:04 UTC
        assert logging.Formatter("%(asctime)s", "%Y-%m-%d %H:%M:%S").format(record) == "2026-10-11 00:03:04"
    finally:
        logging.Formatter.converter = original
