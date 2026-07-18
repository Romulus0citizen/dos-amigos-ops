"""Finalize integration persistence contract.

Revision ID: 20260717_0002
Revises: 20260717_0001
Create Date: 2026-07-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260717_0002"
down_revision: str | None = "20260717_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = (
    "integration_connections",
    "integration_capabilities",
    "sync_runs",
    "raw_payloads",
)


def _assert_tables_are_empty() -> None:
    connection = op.get_bind()

    for table_name in TABLES:
        row_count = connection.execute(sa.text(f'SELECT COUNT(*) FROM "{table_name}"')).scalar_one()

        if row_count:
            raise RuntimeError(
                f"Migration requires empty tables; {table_name} contains {row_count} rows"
            )


def upgrade() -> None:
    _assert_tables_are_empty()

    op.drop_table("raw_payloads")
    op.drop_table("sync_runs")
    op.drop_table("integration_capabilities")
    op.drop_table("integration_connections")

    op.create_table(
        "integration_connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("mode", sa.String(length=50), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=True),
        sa.Column("organization_ref", sa.String(length=120), nullable=True),
        sa.Column(
            "status",
            sa.String(length=30),
            server_default="configured",
            nullable=False,
        ),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_integration_connections_provider",
        "integration_connections",
        ["provider"],
    )

    op.create_table(
        "integration_capabilities",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("capability", sa.String(length=100), nullable=False),
        sa.Column(
            "status",
            sa.String(length=30),
            server_default="unknown",
            nullable=False,
        ),
        sa.Column("method_or_report", sa.String(length=255), nullable=True),
        sa.Column("history_depth", sa.String(length=100), nullable=True),
        sa.Column("expected_freshness", sa.String(length=100), nullable=True),
        sa.Column(
            "contains_pii",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evidence_reference", sa.String(length=500), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('proven', 'partial', 'blocked', 'unknown')",
            name="ck_integration_capabilities_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider",
            "capability",
            name="uq_integration_capability",
        ),
    )

    op.create_table(
        "sync_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("dataset", sa.String(length=100), nullable=False),
        sa.Column(
            "status",
            sa.String(length=30),
            server_default="queued",
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "records_received",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "payloads_saved",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message_sanitized", sa.Text(), nullable=True),
        sa.Column("trace_id", sa.String(length=100), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'partial', 'failed')",
            name="ck_sync_runs_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sync_runs_status", "sync_runs", ["status"])
    op.create_index("ix_sync_runs_trace_id", "sync_runs", ["trace_id"])
    op.create_index(
        "ix_sync_runs_provider_dataset_created",
        "sync_runs",
        ["provider", "dataset", "created_at"],
    )

    op.create_table(
        "raw_payloads",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("dataset", sa.String(length=100), nullable=False),
        sa.Column("external_reference", sa.String(length=500), nullable=True),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=False),
        sa.Column("content_type", sa.String(length=120), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("schema_hint", sa.String(length=120), nullable=True),
        sa.Column("sync_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "http_status BETWEEN 100 AND 599",
            name="ck_raw_payloads_http_status",
        ),
        sa.ForeignKeyConstraint(
            ["sync_run_id"],
            ["sync_runs.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_raw_payloads_sync_run_id",
        "raw_payloads",
        ["sync_run_id"],
    )
    op.create_index(
        "ix_raw_payloads_provider_dataset_fetched",
        "raw_payloads",
        ["provider", "dataset", "fetched_at"],
    )
    op.create_index(
        "uq_raw_payload_with_external_reference",
        "raw_payloads",
        [
            "provider",
            "dataset",
            "payload_sha256",
            "external_reference",
        ],
        unique=True,
        postgresql_where=sa.text("external_reference IS NOT NULL"),
    )
    op.create_index(
        "uq_raw_payload_without_external_reference",
        "raw_payloads",
        ["provider", "dataset", "payload_sha256"],
        unique=True,
        postgresql_where=sa.text("external_reference IS NULL"),
    )


def downgrade() -> None:
    op.drop_table("raw_payloads")
    op.drop_table("sync_runs")
    op.drop_table("integration_capabilities")
    op.drop_table("integration_connections")

    op.create_table(
        "integration_connections",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=True),
        sa.Column("adapter", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("secret_reference", sa.String(length=255), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "integration_capabilities",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("capability", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("method_or_report", sa.String(length=255), nullable=True),
        sa.Column("history_depth", sa.String(length=100), nullable=True),
        sa.Column("expected_freshness", sa.String(length=100), nullable=True),
        sa.Column("contains_pii", sa.Boolean(), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evidence_reference", sa.String(length=500), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider",
            "capability",
            name="uq_integration_capability",
        ),
    )

    op.create_table(
        "sync_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("connection_id", sa.String(length=36), nullable=False),
        sa.Column("dataset", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("trace_id", sa.String(length=100), nullable=False),
        sa.Column("cursor_value", sa.String(length=500), nullable=True),
        sa.Column("records_received", sa.Integer(), nullable=False),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["integration_connections.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "raw_payloads",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("sync_run_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("dataset", sa.String(length=100), nullable=False),
        sa.Column("source_key", sa.String(length=500), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["sync_run_id"],
            ["sync_runs.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider",
            "dataset",
            "source_key",
            "payload_hash",
            name="uq_raw_payload_idempotency",
        ),
    )
