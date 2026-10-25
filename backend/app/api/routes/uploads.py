"""当前对话的临时文档上传。"""

from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from app.api.dependencies import workspace_user, get_container
from app.api.schemas import WorkspaceIdentity
from app.container import AppContainer
from app.services.conversation_activity import ConversationBusyError


router = APIRouter(prefix="/conversations", tags=["uploads"])


@router.post("/{conversation_id}/files")
async def upload_file(
    conversation_id: UUID,
    file: UploadFile = File(...),
    user: WorkspaceIdentity = Depends(workspace_user),
    container: AppContainer = Depends(get_container),
):
    identifier = str(conversation_id)
    try:
        with container.conversation_activity.operation(identifier):
            if await container.conversations.get_owned(user.user_id, identifier) is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="对话不存在。")
            item = await container.upload_service.save(user.user_id, identifier, file)
    except ConversationBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {
        "file_id": str(item.file_id),
        "filename": item.original_filename,
        "extension": item.extension,
        "size_bytes": item.size_bytes,
        "parse_status": "parsed",
    }


@router.get("/{conversation_id}/files")
async def list_files(
    conversation_id: UUID,
    user: WorkspaceIdentity = Depends(workspace_user),
    container: AppContainer = Depends(get_container),
):
    identifier = str(conversation_id)
    if await container.conversations.get_owned(user.user_id, identifier) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="对话不存在。")
    items = await container.uploads.list_owned(user.user_id, identifier)
    return [
        {
            "file_id": str(item.file_id),
            "filename": item.original_filename,
            "extension": item.extension,
            "size_bytes": item.size_bytes,
            "parse_status": item.parse_status,
            "parse_error": item.parse_error,
        }
        for item in items
    ]
