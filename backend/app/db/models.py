"""四个 Schema 的完整数据模型。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class User(Base, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("username"), {"schema": "app"})

    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    username: Mapped[str] = mapped_column(String(80), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Conversation(Base, TimestampMixin):
    __tablename__ = "conversations"
    __table_args__ = (
        Index("ix_conversations_user_updated", "user_id", "updated_at"),
        {"schema": "app"},
    )

    conversation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("app.users.user_id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    title_status: Mapped[str] = mapped_column(String(20), server_default="pending", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_conversation_created", "conversation_id", "created_at"),
        Index("ix_messages_trace", "trace_id"),
        Index("ix_messages_request", "request_id"),
        CheckConstraint(
            "(trace_id IS NULL AND request_id IS NULL) OR "
            "(trace_id IS NOT NULL AND request_id IS NOT NULL)",
            name="ck_messages_trace_request_pair",
        ),
        ForeignKeyConstraint(
            ["trace_id", "request_id", "conversation_id"],
            [
                "observability.traces.trace_id",
                "observability.traces.request_id",
                "observability.traces.conversation_id",
            ],
            name="fk_messages_trace_request_conversation",
        ),
        {"schema": "app"},
    )

    message_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("app.conversations.conversation_id", ondelete="CASCADE"), nullable=False
    )
    request_id: Mapped[Optional[UUID]] = mapped_column(PGUUID(as_uuid=True))
    trace_id: Mapped[Optional[UUID]] = mapped_column(PGUUID(as_uuid=True))
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="success", nullable=False)
    agent_names: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    display_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class MessageCitation(Base):
    __tablename__ = "message_citations"
    __table_args__ = (
        UniqueConstraint("message_id", "citation_index"),
        {"schema": "app"},
    )

    citation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    message_id: Mapped[UUID] = mapped_column(
        ForeignKey("app.messages.message_id", ondelete="CASCADE"), nullable=False
    )
    citation_index: Mapped[int] = mapped_column(Integer, nullable=False)
    document_id: Mapped[Optional[UUID]] = mapped_column(PGUUID(as_uuid=True))
    chunk_id: Mapped[Optional[UUID]] = mapped_column(PGUUID(as_uuid=True))
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    filename: Mapped[Optional[str]] = mapped_column(String(500))
    page_number: Mapped[Optional[int]] = mapped_column(Integer)
    section_path: Mapped[Optional[str]] = mapped_column(String(1000))
    source_url: Mapped[Optional[str]] = mapped_column(Text)
    quoted_excerpt: Mapped[Optional[str]] = mapped_column(Text)
    fetched_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class UploadedFile(Base, TimestampMixin):
    __tablename__ = "uploaded_files"
    __table_args__ = (
        Index("ix_uploaded_files_conversation", "conversation_id"),
        {"schema": "app"},
    )

    file_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("app.users.user_id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("app.conversations.conversation_id", ondelete="CASCADE"), nullable=False
    )
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    relative_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    extension: Mapped[str] = mapped_column(String(20), nullable=False)
    mime_type: Mapped[Optional[str]] = mapped_column(String(120))
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    parse_status: Mapped[str] = mapped_column(String(32), default="uploaded", nullable=False)
    parse_error: Mapped[Optional[str]] = mapped_column(Text)


class UploadedFileContent(Base):
    __tablename__ = "uploaded_file_contents"
    __table_args__ = {"schema": "app"}

    file_id: Mapped[UUID] = mapped_column(
        ForeignKey("app.uploaded_files.file_id", ondelete="CASCADE"), primary_key=True
    )
    extracted_text: Mapped[str] = mapped_column(Text, nullable=False)
    page_map: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    extracted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class KnowledgeDocument(Base, TimestampMixin):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("source_type", "relative_path"),
        {"schema": "knowledge"},
    )

    document_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    relative_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    source_url: Mapped[Optional[str]] = mapped_column(Text)
    published_at: Mapped[Optional[date]] = mapped_column(Date)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_extra: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)


class KnowledgeChunk(Base, TimestampMixin):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_document_chunk_index"),
        Index("ix_chunks_document", "document_id"),
        {"schema": "knowledge"},
    )

    chunk_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge.documents.document_id", ondelete="CASCADE"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(1024), nullable=False)
    page_number: Mapped[Optional[int]] = mapped_column(Integer)
    section_path: Mapped[Optional[str]] = mapped_column(String(1000))
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False)
    metadata_extra: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)


class WebSource(Base, TimestampMixin):
    __tablename__ = "web_sources"
    __table_args__ = {"schema": "knowledge"}

    source_key: Mapped[str] = mapped_column(String(100), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(300), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    adapter: Mapped[str] = mapped_column(String(50), nullable=False)
    cache_ttl_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)


class WebSnapshot(Base):
    __tablename__ = "web_snapshots"
    __table_args__ = (
        Index("ix_web_snapshots_source_fetched", "source_key", "fetched_at"),
        {"schema": "knowledge"},
    )

    snapshot_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    source_key: Mapped[str] = mapped_column(
        ForeignKey("knowledge.web_sources.source_key", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="success", nullable=False)


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"
    __table_args__ = {"schema": "knowledge"}

    job_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    total_items: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completed_items: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_items: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class IngestionItem(Base):
    __tablename__ = "ingestion_items"
    __table_args__ = (
        UniqueConstraint("job_id", "relative_path"),
        {"schema": "knowledge"},
    )

    item_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge.ingestion_jobs.job_id", ondelete="CASCADE"), nullable=False
    )
    relative_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    detected_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    confirmed_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    document_id: Mapped[Optional[UUID]] = mapped_column(PGUUID(as_uuid=True))
    error_message: Mapped[Optional[str]] = mapped_column(Text)


class ConversationSummary(Base, TimestampMixin):
    __tablename__ = "conversation_summaries"
    __table_args__ = (
        Index("ix_summaries_conversation", "conversation_id", "created_at"),
        UniqueConstraint("conversation_id", name="uq_conversation_summary"),
        {"schema": "memory"},
    )

    summary_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("app.conversations.conversation_id", ondelete="CASCADE"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    covered_until_message_id: Mapped[Optional[UUID]] = mapped_column(PGUUID(as_uuid=True))
    message_count: Mapped[int] = mapped_column(Integer, nullable=False)


class LongTermMemory(Base, TimestampMixin):
    __tablename__ = "long_term_memories"
    __table_args__ = (
        Index("ix_long_term_memory_user_type", "user_id", "memory_type"),
        {"schema": "memory"},
    )

    memory_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("app.users.user_id", ondelete="CASCADE"), nullable=False
    )
    memory_type: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(1024), nullable=False)
    importance: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    source_conversation_id: Mapped[Optional[UUID]] = mapped_column(PGUUID(as_uuid=True))
    source_message_ids: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)


class Trace(Base):
    __tablename__ = "traces"
    __table_args__ = (
        UniqueConstraint("request_id"),
        UniqueConstraint(
            "trace_id", "request_id", "conversation_id", name="uq_traces_message_link"
        ),
        Index("ix_traces_started_status", "started_at", "status"),
        {"schema": "observability"},
    )

    trace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    request_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    conversation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    request_source: Mapped[str] = mapped_column(String(32), default="chat", nullable=False)
    query_snapshot: Mapped[Optional[str]] = mapped_column(Text)
    route: Mapped[Optional[str]] = mapped_column(String(32))
    primary_intent: Mapped[Optional[str]] = mapped_column(String(64))
    selected_agents: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    selected_skills: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    retrieval_rounds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tool_call_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[Optional[float]] = mapped_column(Float)
    error_code: Mapped[Optional[str]] = mapped_column(String(100))


class TraceEvent(Base):
    __tablename__ = "trace_events"
    __table_args__ = (
        UniqueConstraint("trace_id", "sequence_number"),
        Index("ix_trace_events_trace", "trace_id", "sequence_number"),
        {"schema": "observability"},
    )

    event_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    trace_id: Mapped[UUID] = mapped_column(
        ForeignKey("observability.traces.trace_id", ondelete="CASCADE"), nullable=False
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    node_name: Mapped[str] = mapped_column(String(100), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    input_summary: Mapped[Optional[str]] = mapped_column(Text)
    output_summary: Mapped[Optional[str]] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[Optional[float]] = mapped_column(Float)


class UnifiedEvalCase(Base, TimestampMixin):
    __tablename__ = "eval_cases"
    __table_args__ = {"schema": "observability"}
    case_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    case_number: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)


class UnifiedEvalRun(Base):
    __tablename__ = "eval_runs"
    __table_args__ = {"schema": "observability"}
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("app.users.user_id"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False)
    repeats: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    progress: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    summary_metrics: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    stop_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class UnifiedEvalResult(Base):
    __tablename__ = "eval_results"
    __table_args__ = (UniqueConstraint("run_id", "case_id", "trial_index"), {"schema": "observability"})
    result_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("observability.eval_runs.run_id"), nullable=False)
    # 无当前Case外键：永久删除用例不影响既有快照，也不影响正在执行的快照。
    case_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    trial_index: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)

