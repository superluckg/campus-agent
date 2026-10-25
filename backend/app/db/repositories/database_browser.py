"""开发者只读数据库浏览：固定业务表目录，参数化筛选，按需读取完整记录。"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import String, Text, cast, func, literal, or_, select, union_all
from sqlalchemy.dialects import postgresql

from app.db import models  # noqa: F401
from app.db.base import Base
from app.db.session import Database

# 顺序、中文用途、默认显示字段均由服务器指定；用户输入不参与 SQL 标识符拼接。
TABLES = {
    "app.users": ("用户", "user_id username role is_active created_at"),
    "app.conversations": ("对话窗口", "conversation_id user_id title status created_at updated_at"),
    "app.messages": (
        "聊天消息",
        "message_id conversation_id role content status trace_id created_at",
    ),
    "app.message_citations": (
        "回答引用",
        "citation_id message_id citation_index title document_id page_number",
    ),
    "app.uploaded_files": (
        "上传文件",
        "file_id conversation_id original_filename extension size_bytes parse_status created_at",
    ),
    "app.uploaded_file_contents": (
        "上传文件解析文本",
        "file_id extracted_text page_map extracted_at",
    ),
    "observability.traces": (
        "请求追踪",
        "trace_id conversation_id status primary_intent selected_agents started_at duration_ms",
    ),
    "observability.trace_events": (
        "追踪事件",
        "event_id trace_id sequence_number event_type status payload started_at",
    ),
    "observability.eval_cases": ("评测用例", "case_id case_number payload updated_at"),
    "observability.eval_runs": ("评测运行", "run_id status repeats progress started_at finished_at summary_metrics"),
    "observability.eval_results": (
        "评测结果",
        "result_id run_id case_id trial_index payload",
    ),
    "memory.conversation_summaries": (
        "对话摘要",
        "summary_id conversation_id content message_count updated_at",
    ),
    "memory.long_term_memories": (
        "长期记忆",
        "memory_id user_id memory_type content importance source_conversation_id updated_at",
    ),
    "knowledge.documents": (
        "知识文档",
        "document_id title filename relative_path",
    ),
    "knowledge.chunks": (
        "知识切片与向量",
        "chunk_id document_id chunk_index content page_number embedding",
    ),
    "knowledge.web_sources": (
        "官网来源",
        "source_key display_name url adapter enabled cache_ttl_seconds",
    ),
    "knowledge.web_snapshots": (
        "官网快照",
        "snapshot_id source_key title status fetched_at expires_at",
    ),
    "knowledge.ingestion_jobs": (
        "入库任务",
        "job_id status total_items completed_items failed_items started_at finished_at",
    ),
    "knowledge.ingestion_items": (
        "入库文件",
        "item_id job_id relative_path status document_id error_message",
    ),
}
REFERENCES = {
    "user_id": ("app.users", "user_id"),
    "conversation_id": ("app.conversations", "conversation_id"),
    "source_conversation_id": ("app.conversations", "conversation_id"),
    "message_id": ("app.messages", "message_id"),
    "covered_until_message_id": ("app.messages", "message_id"),
    "trace_id": ("observability.traces", "trace_id"),
    "request_id": ("observability.traces", "request_id"),
    "document_id": ("knowledge.documents", "document_id"),
    "chunk_id": ("knowledge.chunks", "chunk_id"),
    "file_id": ("app.uploaded_files", "file_id"),
    "job_id": ("knowledge.ingestion_jobs", "job_id"),
    "source_key": ("knowledge.web_sources", "source_key"),
    "case_id": ("observability.eval_cases", "case_id"),
    "run_id": ("observability.eval_runs", "run_id"),
}
CHILDREN = {
    "app.users": [
        ("app.conversations", "user_id"),
        ("memory.long_term_memories", "user_id"),
        ("observability.traces", "user_id"),
    ],
    "app.conversations": [
        ("app.messages", "conversation_id"),
        ("observability.traces", "conversation_id"),
        ("memory.conversation_summaries", "conversation_id"),
        ("app.uploaded_files", "conversation_id"),
    ],
    "app.messages": [("app.message_citations", "message_id")],
    "app.uploaded_files": [("app.uploaded_file_contents", "file_id")],
    "observability.traces": [
        ("observability.trace_events", "trace_id"),
        ("app.messages", "trace_id"),
    ],
    "observability.eval_cases": [("observability.eval_results", "case_id")],
    "observability.eval_runs": [("observability.eval_results", "run_id")],
    "knowledge.documents": [
        ("knowledge.chunks", "document_id"),
        ("app.message_citations", "document_id"),
        ("knowledge.ingestion_items", "document_id"),
    ],
    "knowledge.chunks": [("app.message_citations", "chunk_id")],
    "knowledge.ingestion_jobs": [("knowledge.ingestion_items", "job_id")],
    "knowledge.web_sources": [("knowledge.web_snapshots", "source_key")],
}
SENSITIVE = {
    "password",
    "password_hash",
    "api_key",
    "apikey",
    "deepseek_api_key",
    "jwt_secret_key",
    "secret",
    "secret_key",
    "access_token",
    "refresh_token",
    "authorization",
}
PREVIEW_LENGTH = 240


def safe_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(k): "[已隐藏]" if str(k).lower() in SENSITIVE else safe_value(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [safe_value(item) for item in value]
    if isinstance(value, (UUID, Decimal)):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "tolist"):
        return safe_value(value.tolist())
    return value


def column_kind(column) -> str:
    if isinstance(column.type, models.Vector):
        return "vector"
    if isinstance(column.type, postgresql.JSONB):
        return "json"
    if isinstance(column.type, (String, Text)):
        return "text"
    return "scalar"


class DatabaseBrowserRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def table(self, schema: str, name: str):
        key = f"{schema}.{name}"
        if key not in TABLES:
            raise LookupError("该表不在可浏览的业务表目录中。")
        return Base.metadata.tables[key]

    def describe(self, table) -> dict:
        key = table.fullname
        description, defaults = TABLES[key]
        columns = []
        for column in table.columns:
            kind = column_kind(column)
            reference = REFERENCES.get(column.name)
            if reference and reference[0] == key and reference[1] == column.name:
                reference = None
            columns.append(
                {
                    "name": column.name,
                    "type": str(column.type.compile(dialect=postgresql.dialect())),
                    "kind": kind,
                    "nullable": column.nullable,
                    "primary_key": column.primary_key,
                    "foreign_keys": [fk.target_fullname for fk in column.foreign_keys],
                    "sensitive": column.name in SENSITIVE,
                    "sortable": kind not in {"json", "vector"} and column.name not in SENSITIVE,
                    "filterable": kind not in {"json", "vector"} and column.name not in SENSITIVE,
                    "reference": {"table": reference[0], "column": reference[1]}
                    if reference
                    else None,
                }
            )
        return {
            "schema": table.schema,
            "name": table.name,
            "key": key,
            "description": description,
            "primary_key": next(column.name for column in table.primary_key),
            "default_columns": [name for name in defaults.split() if name in table.c],
            "columns": columns,
            "relations": [
                {
                    "table": target,
                    "column": column,
                    "source_column": column,
                    "label": f"查看{TABLES[target][0]}",
                }
                for target, column in CHILDREN.get(key, [])
            ],
        }

    async def catalog(self) -> dict:
        tables = [self.table(*key.split(".")) for key in TABLES]
        query = union_all(
            *[
                select(
                    literal(table.fullname).label("key"), func.count().label("count")
                ).select_from(table)
                for table in tables
            ]
        )
        async with self.database.session() as session:
            counts = dict((await session.execute(query)).all())
        return {
            "database": self.database.engine.url.database,
            "schemas": list(dict.fromkeys(table.schema for table in tables)),
            "tables": [
                {**self.describe(table), "row_count": counts[table.fullname]} for table in tables
            ],
        }

    def conditions(self, table, filters: dict[str, str], query: str):
        conditions = []
        if len(filters) > 10:
            raise ValueError("最多同时使用 10 个筛选条件。")
        for name, value in filters.items():
            if (
                name not in table.c
                or name in SENSITIVE
                or column_kind(table.c[name]) in {"json", "vector"}
            ):
                raise ValueError("筛选字段不可用。")
            if not isinstance(value, str) or len(value) > 500:
                raise ValueError("筛选值必须是长度不超过 500 的文本。")
            if value == "__NULL__":
                conditions.append(table.c[name].is_(None))
            else:
                conditions.append(cast(table.c[name], Text) == value)
        if query:
            searchable = [
                column
                for column in table.c
                if column_kind(column) == "text" and column.name not in SENSITIVE
            ]
            if searchable:
                conditions.append(
                    or_(
                        *[
                            column.ilike(
                                "%"
                                + query.replace("\\", "\\\\")
                                .replace("%", "\\%")
                                .replace("_", "\\_")
                                + "%",
                                escape="\\",
                            )
                            for column in searchable
                        ]
                    )
                )
            else:
                raise ValueError("此表没有可进行文本搜索的字段，请使用字段筛选。")
        return conditions

    async def rows(
        self,
        schema: str,
        name: str,
        *,
        page: int,
        page_size: int,
        filters: dict[str, str],
        query: str,
        sort_by: str | None,
        descending: bool,
    ) -> dict:
        table = self.table(schema, name)
        conditions = self.conditions(table, filters, query)
        if sort_by:
            if (
                sort_by not in table.c
                or sort_by in SENSITIVE
                or column_kind(table.c[sort_by]) in {"json", "vector"}
            ):
                raise ValueError("排序字段不可用。")
        else:
            sort_by = (
                "sequence_number"
                if name == "trace_events"
                else next(
                    (
                        field
                        for field in ("created_at", "started_at", "fetched_at")
                        if field in table.c
                    ),
                    next(iter(table.primary_key)).name,
                )
            )
            if name == "trace_events":
                descending = False
        projections = []
        preview_fields = []
        for column in table.c:
            kind = column_kind(column)
            if column.name in SENSITIVE:
                projections.append(literal("[已隐藏]").label(column.name))
            elif kind == "vector":
                projections.append(func.vector_dims(column).label(column.name))
                preview_fields.append(column.name)
            elif kind == "json":
                # 先在服务端脱敏 JSON，再生成摘要，避免截断后无法脱敏。
                projections.append(column)
                preview_fields.append(column.name)
            elif kind == "text":
                projections.append(func.left(column, PREVIEW_LENGTH).label(column.name))
                preview_fields.append(column.name)
            else:
                projections.append(column)
        primary = next(iter(table.primary_key))
        order = table.c[sort_by].desc() if descending else table.c[sort_by].asc()
        statement = (
            select(*projections)
            .where(*conditions)
            .order_by(order, primary.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        async with self.database.session() as session:
            total = (
                await session.execute(select(func.count()).select_from(table).where(*conditions))
            ).scalar_one()
            records = (await session.execute(statement)).mappings().all()
        rows = []
        for record in records:
            values = dict(record)
            for column in table.c:
                if column_kind(column) == "vector" and values[column.name] is not None:
                    values[column.name] = {"dimensions": values[column.name]}
                elif column_kind(column) == "json" and values[column.name] is not None:
                    values[column.name] = json.dumps(
                        safe_value(values[column.name]), ensure_ascii=False
                    )[:PREVIEW_LENGTH]
            rows.append(
                {
                    "key": str(record[primary.name]),
                    "values": safe_value(values),
                    "preview_fields": preview_fields,
                }
            )
        return {"rows": rows, "total": total, "page": page, "page_size": page_size}

    async def record(self, schema: str, name: str, key: str) -> dict:
        table = self.table(schema, name)
        primary = next(iter(table.primary_key))
        if isinstance(primary.type, postgresql.UUID):
            try:
                key = str(UUID(key))
            except ValueError as exc:
                raise ValueError("记录编号格式无效。") from exc
        projections = [
            literal("[已隐藏]").label(column.name) if column.name in SENSITIVE else column
            for column in table.c
        ]
        async with self.database.session() as session:
            record = (
                (await session.execute(select(*projections).where(cast(primary, Text) == key)))
                .mappings()
                .one_or_none()
            )
        if record is None:
            raise LookupError("记录不存在，可能已被其他操作更新。")
        return {"key": str(record[primary.name]), "values": safe_value(dict(record))}
