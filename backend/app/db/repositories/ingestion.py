"""入库任务状态仓库。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select

from app.db.models import IngestionItem, IngestionJob
from app.db.identifiers import as_uuid
from app.db.session import Database


class IngestionRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def create_job(self, items: list[dict], config_snapshot: dict) -> IngestionJob:
        job = IngestionJob(
            status="awaiting_confirmation",
            total_items=len(items),
            config_snapshot=config_snapshot,
        )
        async with self.database.session() as session:
            session.add(job)
            await session.flush()
            for item in items:
                session.add(
                    IngestionItem(
                        job_id=job.job_id,
                        relative_path=item["relative_path"],
                        status="pending",
                        detected_metadata=item["detected_metadata"],
                    )
                )
        return job

    async def get_job(self, job_id: str) -> Optional[IngestionJob]:
        async with self.database.session() as session:
            return await session.get(IngestionJob, as_uuid(job_id))

    async def current_job(self) -> Optional[IngestionJob]:
        """优先恢复运行中的任务，否则恢复最近一次扫描或入库的任务。"""
        async with self.database.session() as session:
            result = await session.execute(
                select(IngestionJob).order_by(
                    (IngestionJob.status == "running").desc(),
                    IngestionJob.created_at.desc(),
                    IngestionJob.job_id.desc(),
                ).limit(1)
            )
            return result.scalar_one_or_none()

    async def list_items(self, job_id: str) -> list[IngestionItem]:
        async with self.database.session() as session:
            result = await session.execute(
                select(IngestionItem)
                .where(IngestionItem.job_id == as_uuid(job_id))
                .order_by(IngestionItem.relative_path)
            )
            return list(result.scalars())

    async def confirm(self, job_id: str, values: dict[str, dict]) -> None:
        async with self.database.session() as session:
            job = await session.get(IngestionJob, as_uuid(job_id), with_for_update=True)
            if job is None:
                raise ValueError("入库任务不存在。")
            if job.status not in {"awaiting_confirmation", "confirmed"}:
                raise ValueError("当前任务不能重复确认。")
            items = list(
                (
                    await session.execute(
                        select(IngestionItem).where(IngestionItem.job_id == as_uuid(job_id))
                    )
                ).scalars()
            )
            known = {str(item.item_id): item for item in items}
            if set(values) - set(known):
                raise ValueError("包含不属于当前任务的入库条目。")
            for item_id, metadata in values.items():
                known[item_id].confirmed_metadata = metadata
                known[item_id].status = "confirmed"
            job.status = (
                "confirmed"
                if all(item.status == "confirmed" for item in items)
                else "awaiting_confirmation"
            )

    async def mark_running(self, job_id: str) -> None:
        async with self.database.session() as session:
            job = await session.get(IngestionJob, as_uuid(job_id), with_for_update=True)
            if job is None:
                raise ValueError("入库任务不存在。")
            if job.status != "confirmed" or job.total_items == 0:
                raise ValueError("请先确认待入库文件，运行中的任务不能重复启动。")
            job.status = "running"
            job.started_at = datetime.now(timezone.utc)

    async def finish_item(
        self,
        item_id: str,
        document_id: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        async with self.database.session() as session:
            item = await session.get(IngestionItem, as_uuid(item_id), with_for_update=True)
            if item is None:
                return
            if item.status in {"completed", "failed"}:
                return
            item.status = "failed" if error else "completed"
            item.document_id = as_uuid(document_id) if document_id else None
            item.error_message = error[:2000] if error else None
            job = await session.get(IngestionJob, item.job_id, with_for_update=True)
            if error:
                job.failed_items += 1
            else:
                job.completed_items += 1

    async def finish_job(self, job_id: str) -> None:
        async with self.database.session() as session:
            job = await session.get(IngestionJob, as_uuid(job_id))
            if job is None:
                return
            items = list(
                (
                    await session.execute(
                        select(IngestionItem).where(IngestionItem.job_id == as_uuid(job_id))
                    )
                ).scalars()
            )
            job.completed_items = sum(item.status == "completed" for item in items)
            job.failed_items = sum(item.status == "failed" for item in items)
            job.status = "completed" if job.completed_items == job.total_items else "partial"
            job.finished_at = datetime.now(timezone.utc)

    async def fail_job(self, job_id: str) -> None:
        async with self.database.session() as session:
            job = await session.get(IngestionJob, as_uuid(job_id))
            if job is not None:
                job.status = "failed"
                job.finished_at = datetime.now(timezone.utc)
