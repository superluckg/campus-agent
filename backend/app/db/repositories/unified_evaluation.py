"""统一评测持久化：全局单运行、原子追加、不可复用编号、独立结果快照。"""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, text, update
from sqlalchemy.orm import defer

from app.core.observability import snapshot
from app.db.models import UnifiedEvalCase, UnifiedEvalResult, UnifiedEvalRun
from app.core.errors import EvaluationConflict


def case_view(row):
    return {
        "case_id": str(row.case_id),
        "case_number": row.case_number,
        "case_label": f"Case {row.case_number:03d}",
        "payload": row.payload,
    }


def run_view(row, include_snapshot=False):
    if row is None:
        return None
    return {
        column.name: snapshot(getattr(row, column.name))
        for column in row.__table__.columns
        if include_snapshot or column.name != "snapshot"
    }


async def lock(session):
    await session.execute(text("SELECT pg_advisory_xact_lock(6180710)"))


class UnifiedEvaluationRepository:
    def __init__(self, database):
        self.database = database

    async def cases(self):
        async with self.database.session() as session:
            rows = (
                await session.execute(select(UnifiedEvalCase).order_by(UnifiedEvalCase.case_number))
            ).scalars()
            return [case_view(row) for row in rows]

    async def selected(self, ids):
        try:
            values = [UUID(item) for item in ids]
        except ValueError as exc:
            raise ValueError("所选Case标识无效。") from exc
        async with self.database.session() as session:
            rows = list(
                (
                    await session.execute(
                        select(UnifiedEvalCase).where(UnifiedEvalCase.case_id.in_(values))
                    )
                ).scalars()
            )
            if len(rows) != len(values):
                raise ValueError("所选Case不存在或已经删除。编号不连续不影响运行。")
            rows.sort(key=lambda row: row.case_number)
            return [case_view(row) for row in rows]

    async def save(self, payloads, case_id=None):
        values = [payload.case_payload() for payload in payloads]
        if not values or (case_id and len(values) != 1):
            raise ValueError("创建不能为空；编辑只能提交一个Case。")
        saved = []
        async with self.database.session() as session:
            await lock(session)
            for payload in values:
                if case_id:
                    row = await session.get(UnifiedEvalCase, UUID(case_id))
                    if row is None:
                        raise LookupError("Case不存在。")
                    row.payload = payload
                else:
                    number = await session.scalar(
                        text("SELECT nextval('observability.eval_case_number_seq')")
                    )
                    row = UnifiedEvalCase(case_number=number, payload=payload)
                    session.add(row)
                await session.flush()
                saved.append(case_view(row))
        return {"saved_count": len(saved), "cases": saved}

    async def remove(self, case_id):
        async with self.database.session() as session:
            await lock(session)
            row = await session.get(UnifiedEvalCase, UUID(case_id))
            if row is None:
                raise LookupError("Case不存在。")
            await session.delete(row)
        return {"deleted": True}

    async def current(self):
        async with self.database.session() as session:
            row = await session.scalar(
                select(UnifiedEvalRun)
                .options(defer(UnifiedEvalRun.snapshot))
                .order_by(UnifiedEvalRun.started_at.desc(), UnifiedEvalRun.run_id.desc())
                .limit(1)
            )
            return run_view(row)

    async def create_run(self, user_id, repeats, cases, corpus, manifest):
        async with self.database.session() as session:
            await lock(session)
            if await session.scalar(
                select(UnifiedEvalRun.run_id)
                .where(UnifiedEvalRun.status.in_(["queued", "running"]))
                .limit(1)
            ):
                raise EvaluationConflict("已有统一评测运行，请等待结束或停止。")
            ids = [UUID(case["case_id"]) for case in cases]
            fresh = {
                str(row.case_id): row.payload
                for row in (
                    await session.execute(
                        select(UnifiedEvalCase).where(UnifiedEvalCase.case_id.in_(ids))
                    )
                ).scalars()
            }
            if any(fresh.get(case["case_id"]) != case["payload"] for case in cases):
                raise EvaluationConflict("用例在预检期间被编辑或删除，请刷新后重新运行。")
            row = UnifiedEvalRun(
                user_id=UUID(user_id),
                repeats=repeats,
                snapshot=snapshot({"cases": cases, "corpus": corpus, "manifest": manifest}),
                progress={
                    "total_attempts": len(cases) * repeats,
                    "completed_attempts": 0,
                    "current_case": None,
                    "trial_index": None,
                    "stage": "排队中",
                },
            )
            session.add(row)
            await session.flush()
            return run_view(row, include_snapshot=True)

    async def update_run(self, run_id, **values):
        async with self.database.session() as session:
            safe = {
                key: snapshot(value) if isinstance(value, (dict, list)) else value
                for key, value in values.items()
            }
            await session.execute(
                update(UnifiedEvalRun).where(UnifiedEvalRun.run_id == UUID(run_id)).values(**safe)
            )

    async def run(self, run_id):
        async with self.database.session() as session:
            row = await session.get(
                UnifiedEvalRun, UUID(run_id), options=[defer(UnifiedEvalRun.snapshot)]
            )
            if row is None:
                raise LookupError("评测运行不存在。")
            return run_view(row)

    async def request_stop(self, run_id):
        async with self.database.session() as session:
            row = await session.scalar(
                select(UnifiedEvalRun)
                .options(defer(UnifiedEvalRun.snapshot))
                .where(UnifiedEvalRun.run_id == UUID(run_id))
                .with_for_update()
            )
            if row is None:
                raise LookupError("评测运行不存在。")
            if row.status in {"queued", "running"}:
                row.stop_requested = True
            return run_view(row)

    async def add_result(self, run_id, row):
        async with self.database.session() as session:
            existing = await session.scalar(
                select(UnifiedEvalResult).where(
                    UnifiedEvalResult.run_id == UUID(run_id),
                    UnifiedEvalResult.case_id == UUID(row["case"]["case_id"]),
                    UnifiedEvalResult.trial_index == row["trial_index"],
                )
            )
            if existing:
                existing.payload = snapshot(row)
            else:
                session.add(
                    UnifiedEvalResult(
                        run_id=UUID(run_id),
                        case_id=UUID(row["case"]["case_id"]),
                        trial_index=row["trial_index"],
                        payload=snapshot(row),
                    )
                )

    async def results(self, run_id):
        async with self.database.session() as session:
            rows = (
                await session.execute(
                    select(UnifiedEvalResult)
                    .where(UnifiedEvalResult.run_id == UUID(run_id))
                    .order_by(UnifiedEvalResult.trial_index, UnifiedEvalResult.result_id)
                )
            ).scalars()
            return [{**row.payload, "result_id": str(row.result_id)} for row in rows]

    async def recover_interrupted(self):
        from app.evaluation.unified_scoring import summarize

        async with self.database.session() as session:
            await lock(session)
            runs = (
                await session.execute(
                    select(UnifiedEvalRun)
                    .options(defer(UnifiedEvalRun.snapshot))
                    .where(UnifiedEvalRun.status.in_(["queued", "running"]))
                    .with_for_update()
                )
            ).scalars()
            for row in runs:
                values = list(
                    (
                        await session.execute(
                            select(UnifiedEvalResult.payload).where(
                                UnifiedEvalResult.run_id == row.run_id
                            )
                        )
                    ).scalars()
                )
                completed = sum(item.get("status") != "cancelled" for item in values)
                row.status = "interrupted"
                row.finished_at = datetime.now(timezone.utc)
                row.error_message = "服务重启，已完成结果保留；运行不自动续跑。"
                row.progress = {
                    **row.progress,
                    "completed_attempts": completed,
                    "current_case": None,
                    "trial_index": None,
                    "stage": row.error_message,
                }
                row.summary_metrics = summarize(values, row.progress.get("total_attempts", 0))
