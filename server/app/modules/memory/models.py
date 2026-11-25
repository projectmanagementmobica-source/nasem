"""Memory module: structured memories, append-only revisions, and vector embeddings."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import settings
from app.db.base import Base, CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin, str_enum
from app.shared.enums import MemoryCategory, PrivacyLevel, RevisionActor, SourceType


class Memory(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One fact about the user: (entity_name, attribute_key) -> attribute_value."""

    __tablename__ = "memories"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    category: Mapped[MemoryCategory] = mapped_column(
        str_enum(MemoryCategory, "category"), nullable=False
    )
    entity_name: Mapped[str] = mapped_column(String(255), nullable=False)
    attribute_key: Mapped[str] = mapped_column(String(255), nullable=False)
    attribute_value: Mapped[str] = mapped_column(Text, nullable=False)

    # --- Metadata ---
    confidence_score: Mapped[float] = mapped_column(
        Float, nullable=False, server_default=text("1.0")
    )
    source_type: Mapped[SourceType] = mapped_column(
        str_enum(SourceType, "source_type"), nullable=False
    )
    privacy_level: Mapped[PrivacyLevel] = mapped_column(
        str_enum(PrivacyLevel, "privacy_level"),
        nullable=False,
        server_default=PrivacyLevel.PRIVATE.value,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    last_confirmed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Snapshot of the metadata at creation time. Written once, never updated (Rule 3).
    original_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    revisions: Mapped[list[MemoryRevision]] = relationship(
        back_populates="memory",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="MemoryRevision.revision_number",
    )
    embedding: Mapped[MemoryEmbedding | None] = relationship(
        back_populates="memory",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )

    __table_args__ = (
        CheckConstraint(
            "confidence_score >= 0 AND confidence_score <= 1", name="confidence_score_range"
        ),
        # Hot path: "all memories of user X in category Y"
        Index("ix_memories_user_id_category", "user_id", "category"),
        # Integrity: only ONE active value per (user, entity, attribute).
        # Old values stay as is_active=false rows + revisions.
        Index(
            "uq_memories_active_attribute",
            "user_id",
            "entity_name",
            "attribute_key",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )


class MemoryRevision(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Append-only history of every change to a memory. Rows are never updated or deleted."""

    __tablename__ = "memory_revisions"

    memory_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memories.id", ondelete="CASCADE"), nullable=False
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    previous_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str] = mapped_column(Text, nullable=False)
    # Snapshot of (confidence_score, source_type, privacy_level, is_active) BEFORE the change.
    previous_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    changed_by: Mapped[RevisionActor] = mapped_column(
        str_enum(RevisionActor, "changed_by"), nullable=False
    )
    change_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    memory: Mapped[Memory] = relationship(back_populates="revisions")

    __table_args__ = (
        UniqueConstraint(
            "memory_id", "revision_number", name="uq_memory_revisions_memory_id_revision"
        ),
    )


class MemoryEmbedding(CreatedAtMixin, Base):
    """Vector representation of a memory (1:1). Kept separate so the main table stays lean."""

    __tablename__ = "memory_embeddings"

    memory_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("memories.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # Denormalized so vector search can be filtered by user without a JOIN.
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # pgvector returns numpy.ndarray on read; accepts list[float] on write.
    embedding: Mapped[Any] = mapped_column(Vector(settings.embedding_dim), nullable=False)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    # sha256 of the embedded text -> detect stale embeddings after a memory update.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    memory: Mapped[Memory] = relationship(back_populates="embedding")

    __table_args__ = (
        Index("ix_memory_embeddings_user_id", "user_id"),
        Index(
            "ix_memory_embeddings_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )
