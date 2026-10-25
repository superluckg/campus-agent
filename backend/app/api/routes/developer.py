"""开发者页面使用的 Trace、Skill 和公共资料入库接口。"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import workspace_user, get_container
from app.api.schemas import WorkspaceIdentity
from app.container import AppContainer
from app.schemas.ingestion import ConfirmIngestionRequest


router = APIRouter(prefix="/developer", tags=["developer"])


@router.get("/traces")
async def list_traces(
    _: WorkspaceIdentity = Depends(workspace_user),
    container: AppContainer = Depends(get_container),
):
    return await container.traces.list_views(source="chat")


@router.get("/traces/{trace_id}")
async def trace_detail(trace_id: str, _: WorkspaceIdentity = Depends(workspace_user),
                       container: AppContainer = Depends(get_container)):
    try:
        return await container.traces.detail(trace_id)
    except (LookupError, ValueError) as exc:
        raise HTTPException(404 if isinstance(exc, LookupError) else 422, str(exc)) from exc


@router.get("/traces/{trace_id}/events")
async def trace_events(
    trace_id: str,
    _: WorkspaceIdentity = Depends(workspace_user),
    container: AppContainer = Depends(get_container),
):
    return [
        {
            "event_id": str(item.event_id),
            "sequence_number": item.sequence_number,
            "node_name": item.node_name,
            "event_type": item.event_type,
            "status": item.status,
            "payload": item.payload,
            "duration_ms": item.duration_ms,
            "started_at": item.started_at,
        }
        for item in await container.traces.events(trace_id)
    ]


@router.get("/skills")
async def list_skills(
    _: WorkspaceIdentity = Depends(workspace_user),
    container: AppContainer = Depends(get_container),
):
    container.skill_manager.reload_if_changed()
    return [
        {
            "name": skill.name,
            "display_name": skill.display_name,
            "enabled": skill.enabled,
            "allowed_agents": [agent.value for agent in skill.allowed_agents],
            "intents": [intent.value for intent in skill.intents],
            "priority": skill.priority,
            "content_hash": skill.content_hash,
        }
        for skill in container.skill_manager.skills.values()
    ]


def _ingestion_view(job, items) -> dict:
    return {
        "job_id": str(job.job_id),
        "status": job.status,
        "total_items": job.total_items,
        "completed_items": job.completed_items,
        "failed_items": job.failed_items,
        "items": [
            {
                "item_id": str(item.item_id),
                "relative_path": item.relative_path,
                "status": item.status,
                "detected_metadata": item.detected_metadata,
                "confirmed_metadata": item.confirmed_metadata,
                "error_message": item.error_message,
            }
            for item in items
        ],
    }


@router.post("/ingestion/scan")
async def scan_knowledge(
    _: WorkspaceIdentity = Depends(workspace_user),
    container: AppContainer = Depends(get_container),
):
    job_id = await container.ingestion.scan()
    job = await container.ingestion_repository.get_job(job_id)
    items = await container.ingestion_repository.list_items(job_id)
    return _ingestion_view(job, items)


@router.get("/ingestion/current")
async def current_ingestion_job(
    _: WorkspaceIdentity = Depends(workspace_user),
    container: AppContainer = Depends(get_container),
):
    job = await container.ingestion_repository.current_job()
    if job is None:
        return None
    return _ingestion_view(
        job, await container.ingestion_repository.list_items(str(job.job_id))
    )


@router.get("/ingestion/{job_id}")
async def get_ingestion_job(
    job_id: str,
    _: WorkspaceIdentity = Depends(workspace_user),
    container: AppContainer = Depends(get_container),
):
    job = await container.ingestion_repository.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="入库任务不存在。")
    return _ingestion_view(
        job, await container.ingestion_repository.list_items(job_id)
    )


@router.post("/ingestion/{job_id}/confirm")
async def confirm_ingestion(
    job_id: str,
    body: ConfirmIngestionRequest,
    _: WorkspaceIdentity = Depends(workspace_user),
    container: AppContainer = Depends(get_container),
):
    await container.ingestion_repository.confirm(
        job_id,
        {
            str(item.item_id): item.metadata.model_dump(mode="json")
            for item in body.items
        },
    )
    return {"confirmed": len(body.items)}


@router.post("/ingestion/{job_id}/start", status_code=status.HTTP_202_ACCEPTED)
async def start_ingestion(
    job_id: str,
    _: WorkspaceIdentity = Depends(workspace_user),
    container: AppContainer = Depends(get_container),
):
    job = await container.ingestion_repository.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="入库任务不存在。")
    if job.status != "confirmed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="请先确认待入库文件。",
        )
    try:
        await container.ingestion_repository.mark_running(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    async def run_and_refresh():
        try:
            await container.ingestion.ingest_confirmed(job_id)
            container.bm25.rebuild(await container.knowledge.load_active_chunks())
            await container.ingestion_repository.finish_job(job_id)
        except BaseException:
            await container.ingestion_repository.fail_job(job_id)
            raise

    task = asyncio.create_task(run_and_refresh())
    container.chat.tasks.add(task)
    task.add_done_callback(container.chat.tasks.discard)
    return {"accepted": True, "job_id": job_id}
