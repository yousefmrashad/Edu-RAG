"""Tables shared by the API and the ingest worker."""

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from edu_rag.config import settings
from edu_rag.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    email: Mapped[str] = mapped_column(Text, unique=True)
    password_hash: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("owner_id", "sha256", name="documents_owner_sha256_key"),
        CheckConstraint(
            "status IN ('uploaded', 'processing', 'ready', 'failed')",
            name="documents_status_check",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
    )
    sha256: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    page_count: Mapped[int | None] = mapped_column(Integer)
    doc_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, default=dict
    )
    # Kept only until the worker has embedded the file, then cleared.
    content: Mapped[bytes | None] = mapped_column(LargeBinary)
    status: Mapped[str] = mapped_column(
        Text, default="uploaded", server_default=text("'uploaded'")
    )
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Chunk(Base):
    """One embedded passage. Wider context is rebuilt from ``ordinal`` windows."""

    __tablename__ = "chunks"
    __table_args__ = (
        Index(
            "chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("chunks_tsv_gin", "tsv", postgresql_using="gin"),
        UniqueConstraint("document_id", "ordinal", name="chunks_document_ordinal_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE")
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    headings: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    embedding: Mapped[list[float]] = mapped_column(Vector(settings.embedding_dim))
    tsv: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', text)", persisted=True)
    )


class Generation(Base):
    """One completion: the request that asked for it, the answer, and its metadata."""

    __tablename__ = "generations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('generating', 'completed', 'failed', 'cancelled')",
            name="generations_status_check",
        ),
        Index("generations_user_created_idx", "user_id", "created_at"),
        Index(
            "generations_generating_idx",
            "updated_at",
            postgresql_where=text("status = 'generating'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    status: Mapped[str] = mapped_column(
        Text, default="generating", server_default=text("'generating'")
    )
    # What was asked: query, source document ids, and per-call overrides.
    request: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    # The answer as prose. Streamed, and never queried into.
    answer: Mapped[str] = mapped_column(Text, default="")
    # The answer as data: citations, retrieved chunks, finish reason. The column
    # keeps the short name; the attribute cannot, since Base.metadata is taken.
    answer_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, default=dict
    )
    model: Mapped[str] = mapped_column(Text)
    usage: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
