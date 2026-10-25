"""建立空库的最终结构；已有数据库只核验，不自动改变结构或数据。"""

from __future__ import annotations

import asyncio

from sqlalchemy import UniqueConstraint, inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.db import models  # noqa: F401
from app.db.base import Base


SCHEMAS = ("app", "knowledge", "memory", "observability")
SPECIAL_DDL = (
    "CREATE INDEX ix_chunks_embedding_hnsw "
    "ON knowledge.chunks USING hnsw (embedding vector_cosine_ops)",
    "CREATE INDEX ix_long_term_memories_embedding_hnsw "
    "ON memory.long_term_memories USING hnsw (embedding vector_cosine_ops)",
    "CREATE SEQUENCE observability.eval_case_number_seq",
    "CREATE UNIQUE INDEX ix_eval_one_active ON observability.eval_runs ((true)) "
    "WHERE status IN ('queued','running')",
)
SPECIAL_INDEXES = {
    "knowledge": {"ix_chunks_embedding_hnsw"},
    "memory": {"ix_long_term_memories_embedding_hnsw"},
    "observability": {"ix_eval_one_active"},
}


def _tables(connection) -> set[str]:
    inspector = inspect(connection)
    return {
        schema + "." + name
        for schema in SCHEMAS
        for name in inspector.get_table_names(schema=schema)
    }


def validate_schema(connection) -> None:
    inspector = inspect(connection)
    expected = set(Base.metadata.tables)
    actual = _tables(connection)
    issues = []
    if actual != expected:
        issues.append("业务表目录不一致")
    for name in sorted(expected & actual):
        table = Base.metadata.tables[name]
        columns = {column["name"] for column in inspector.get_columns(table.name, schema=table.schema)}
        if columns != set(table.columns.keys()):
            issues.append(name + " 字段不一致")
        primary = inspector.get_pk_constraint(table.name, schema=table.schema)
        if set(primary["constrained_columns"]) != {column.name for column in table.primary_key}:
            issues.append(name + " 主键不一致")
        uniques = {
            frozenset(item["column_names"])
            for item in inspector.get_unique_constraints(table.name, schema=table.schema)
        }
        for constraint in table.constraints:
            if isinstance(constraint, UniqueConstraint):
                if frozenset(column.name for column in constraint.columns) not in uniques:
                    issues.append(name + " 缺少唯一约束")
        indexes = {
            item["name"] for item in inspector.get_indexes(table.name, schema=table.schema)
        }
        if not {index.name for index in table.indexes} <= indexes:
            issues.append(name + " 缺少索引")
    for schema, names in SPECIAL_INDEXES.items():
        existing = set(connection.execute(text(
            "SELECT indexname FROM pg_indexes WHERE schemaname=:schema"
        ), {"schema": schema}).scalars())
        if not names <= existing:
            issues.append(schema + " 缺少专用索引")
    if not connection.scalar(text("SELECT to_regclass('observability.eval_case_number_seq')")):
        issues.append("缺少评测用例编号序列")
    if not connection.scalar(text("SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname='vector')")):
        issues.append("缺少 pgvector 扩展")
    if issues:
        raise RuntimeError("数据库结构不符合当前模型，请核对环境：" + "；".join(issues))


async def initialize_database(database_url: str | None = None) -> dict:
    engine = create_async_engine(
        database_url or get_settings(require_secrets=False).database_url,
        hide_parameters=True,
    )
    try:
        async with engine.begin() as connection:
            await connection.execute(text("SELECT pg_advisory_xact_lock(85909619060213)"))
            existing = await connection.run_sync(_tables)
            created = not existing
            if created:
                await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                for schema in SCHEMAS:
                    await connection.execute(text('CREATE SCHEMA IF NOT EXISTS "' + schema + '"'))
                await connection.run_sync(Base.metadata.create_all)
                for statement in SPECIAL_DDL:
                    await connection.execute(text(statement))
            await connection.run_sync(validate_schema)
        return {"created": created, "tables": len(Base.metadata.tables)}
    finally:
        await engine.dispose()


if __name__ == "__main__":
    result = asyncio.run(initialize_database())
    print("数据库结构已就绪，共 %d 张业务表。" % result["tables"])
