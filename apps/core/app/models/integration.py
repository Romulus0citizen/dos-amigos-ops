from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from apps.core.app.models.base import Base


class IntegrationConnection(Base):
    __tablename__ = "integration_connections"

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(50), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(500))
    organization_ref: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="configured",
        server_default="configured",
    )
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class SyncRun(Base):
    __tablename__ = "sync_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'partial', 'failed')",
            name="ck_sync_runs_status",
        ),
        Index("ix_sync_runs_provider_dataset_created", "provider", "dataset", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    dataset: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="queued",
        server_default="queued",
        index=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    records_received: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    payloads_saved: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message_sanitized: Mapped[str | None] = mapped_column(Text)
    trace_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class RawPayload(Base):
    __tablename__ = "raw_payloads"
    __table_args__ = (
        CheckConstraint(
            "http_status BETWEEN 100 AND 599",
            name="ck_raw_payloads_http_status",
        ),
        Index(
            "ix_raw_payloads_sync_run_id",
            "sync_run_id",
        ),
        Index(
            "ix_raw_payloads_provider_dataset_fetched",
            "provider",
            "dataset",
            "fetched_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    dataset: Mapped[str] = mapped_column(String(100), nullable=False)
    external_reference: Mapped[str | None] = mapped_column(String(500))
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    http_status: Mapped[int] = mapped_column(Integer, nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    payload_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_hint: Mapped[str | None] = mapped_column(String(120))
    sync_run_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("sync_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


Index(
    "uq_raw_payload_with_external_reference",
    RawPayload.provider,
    RawPayload.dataset,
    RawPayload.payload_sha256,
    RawPayload.external_reference,
    unique=True,
    postgresql_where=RawPayload.external_reference.is_not(None),
)

Index(
    "uq_raw_payload_without_external_reference",
    RawPayload.provider,
    RawPayload.dataset,
    RawPayload.payload_sha256,
    unique=True,
    postgresql_where=RawPayload.external_reference.is_(None),
)


class IntegrationCapability(Base):
    __tablename__ = "integration_capabilities"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "capability",
            name="uq_integration_capability",
        ),
        CheckConstraint(
            "status IN ('proven', 'partial', 'blocked', 'unknown')",
            name="ck_integration_capabilities_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    capability: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="unknown",
        server_default="unknown",
    )
    method_or_report: Mapped[str | None] = mapped_column(String(255))
    history_depth: Mapped[str | None] = mapped_column(String(100))
    expected_freshness: Mapped[str | None] = mapped_column(String(100))
    contains_pii: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    evidence_reference: Mapped[str | None] = mapped_column(String(500))
    notes: Mapped[str | None] = mapped_column(Text)
