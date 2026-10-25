"""对话、消息启动和 SSE 事件。"""

from __future__ import annotations

import asyncio
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from app.api.dependencies import workspace_user, get_container
from app.api.schemas import ChatAccepted, ChatRequest, ConversationCreate, WorkspaceIdentity
from app.cache.event_bus import format_sse
from app.container import AppContainer
from app.services.conversation_activity import ConversationBusyError
from app.services.conversation_management import ConversationFileError


router = APIRouter(prefix="/conversations", tags=["conversations"])


def _conversation(item, last_user_message_at=None, is_busy=False) -> dict:
    return {
        "conversation_id": str(item.conversation_id),
        "title": item.title,
        "status": item.status,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        "last_user_message_at": last_user_message_at,
        "title_status": item.title_status,
        "is_busy": is_busy,
    }


@router.post("")
async def create_conversation(
    body: ConversationCreate,
    user: WorkspaceIdentity = Depends(workspace_user),
    container: AppContainer = Depends(get_container),
):
    return _conversation(await container.conversations.create(user.user_id, body.title))


@router.get("")
async def list_conversations(
    user: WorkspaceIdentity = Depends(workspace_user),
    container: AppContainer = Depends(get_container),
):
    return [_conversation(item, timestamp, container.conversation_activity.is_busy(str(item.conversation_id)))
            for item, timestamp in await container.conversations.list_overviews(user.user_id)]


@router.delete("/{conversation_id}")
async def delete_conversation(
    conversation_id: UUID,
    user: WorkspaceIdentity = Depends(workspace_user),
    container: AppContainer = Depends(get_container),
):
    try:
        await container.conversation_management.delete(user.user_id, str(conversation_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="对话不存在。") from exc
    except ConversationBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ConversationFileError, OSError) as exc:
        raise HTTPException(status_code=503, detail="文件无法安全清理，未完成删除，请稍后重试。") from exc
    return {"deleted": True, "conversation_id": str(conversation_id)}


@router.get("/{conversation_id}/messages")
async def list_messages(
    conversation_id: UUID,
    user: WorkspaceIdentity = Depends(workspace_user),
    container: AppContainer = Depends(get_container),
):
    identifier = str(conversation_id)
    if await container.conversations.get_owned(user.user_id, identifier) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="对话不存在。")
    items = await container.conversations.list_messages(identifier)
    citation_map = await container.conversations.citations_for_messages(
        [str(item.message_id) for item in items]
    )
    return [
        {
            "message_id": str(item.message_id),
            "request_id": str(item.request_id) if item.request_id else None,
            "trace_id": str(item.trace_id) if item.trace_id else None,
            "role": item.role,
            "content": item.content,
            "status": item.status,
            "agent_names": item.agent_names,
            "display_metadata": item.display_metadata,
            "citations": [
                {
                    "citation_index": citation.citation_index,
                    "title": citation.title,
                    "filename": citation.filename,
                    "page_number": citation.page_number,
                    "page_numbers": (item.display_metadata or {}).get("citation_page_numbers", {}).get(
                        str(citation.citation_index), []),
                    "section_path": citation.section_path,
                    "source_url": citation.source_url,
                    "quoted_excerpt": citation.quoted_excerpt,
                }
                for citation in citation_map.get(str(item.message_id), [])
            ],
            "created_at": item.created_at,
        }
        for item in items
    ]


@router.post("/{conversation_id}/messages", response_model=ChatAccepted)
async def start_message(
    conversation_id: UUID,
    body: ChatRequest,
    user: WorkspaceIdentity = Depends(workspace_user),
    container: AppContainer = Depends(get_container),
) -> ChatAccepted:
    identifier = str(conversation_id)
    try:
        with container.conversation_activity.operation(identifier):
            if await container.conversations.get_owned(user.user_id, identifier) is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="对话不存在。")
            request_id = await container.chat.start(user.user_id, identifier, body.content.strip())
    except ConversationBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ChatAccepted(request_id=request_id, conversation_id=identifier)


@router.get("/{conversation_id}/requests/{request_id}/events")
async def stream_events(
    conversation_id: UUID,
    request_id: str,
    request: Request,
    after: int = 0,
    user: WorkspaceIdentity = Depends(workspace_user),
    container: AppContainer = Depends(get_container),
):
    identifier = str(conversation_id)
    if await container.conversations.get_owned(user.user_id, identifier) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="对话不存在。")
    trace = await container.traces.get_request(request_id)
    if (
        trace is None
        or str(trace.user_id) != user.user_id
        or str(trace.conversation_id) != identifier
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="请求不存在。")

    terminal = {"response.completed", "request.failed"}
    latest = await container.event_bus.latest(request_id)
    if latest is None:
        raise HTTPException(status_code=410, detail={
            "code": "STREAM_EXPIRED", "message": "事件重放已过期，请读取已保存的消息。",
        })
    settled = await container.event_bus.terminal(request_id)
    if settled is not None and after >= settled.event_id:
        raise HTTPException(status_code=410, detail={
            "code": "REQUEST_SETTLED", "message": "请求已结束，请读取已保存的消息。",
        })

    async def stream():
        cursor = max(0, after)
        last_heartbeat = asyncio.get_running_loop().time()
        while not await request.is_disconnected():
            events = await container.event_bus.replay(request_id, cursor, block_ms=1000)
            if not events:
                if not await container.event_bus.available(request_id):
                    yield 'event: stream.expired\ndata: {"message":"事件重放已过期。"}\n\n'
                    return
                now = asyncio.get_running_loop().time()
                if now - last_heartbeat >= 15:
                    yield ": heartbeat\n\n"
                    last_heartbeat = now
                continue
            for event in events:
                cursor = event.event_id
                yield format_sse(event)
                if event.event_type in terminal:
                    return

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
