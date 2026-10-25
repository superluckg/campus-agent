"""查看、编辑和删除工作台的长期记忆。"""

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.api.dependencies import workspace_user, get_container
from app.api.schemas import WorkspaceIdentity, MemoryUpdate
from app.container import AppContainer


router = APIRouter(prefix="/memories", tags=["memories"])


@router.get("")
async def list_memories(
    user: WorkspaceIdentity = Depends(workspace_user),
    container: AppContainer = Depends(get_container),
):
    return [
        {
            "memory_id": str(item.memory_id),
            "memory_type": item.memory_type,
            "content": item.content,
            "importance": item.importance,
            "updated_at": item.updated_at,
        }
        for item in await container.memories.list_owned(user.user_id)
    ]


@router.patch("/{memory_id}")
async def update_memory(
    memory_id: str,
    body: MemoryUpdate,
    user: WorkspaceIdentity = Depends(workspace_user),
    container: AppContainer = Depends(get_container),
):
    if not await container.memories.update_owned(user.user_id, memory_id, body.content):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="记忆不存在。")
    return {"updated": True}


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(
    memory_id: str,
    user: WorkspaceIdentity = Depends(workspace_user),
    container: AppContainer = Depends(get_container),
):
    if not await container.memories.delete_owned(user.user_id, memory_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="记忆不存在。")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
