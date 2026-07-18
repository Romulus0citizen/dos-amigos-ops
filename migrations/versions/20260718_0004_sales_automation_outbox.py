"""Add sales automation and Hermes outbox tables.

Revision ID: 20260718_0004
Revises: 20260718_0003
Create Date: 2026-07-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260718_0004"
down_revision: str | None = "20260718_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "iiko_sales_daily",
        sa.Column(
            "requires_resync",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.create_table(
        "iiko_sales_automation_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("trigger_type", sa.String(length=30), nullable=False),
        sa.Column("requested_date_from", sa.Date(), nullable=True),
        sa.Column("requested_date_to", sa.Date(), nullable=True),
        sa.Column("business_timezone", sa.String(length=100), nullable=False),
        sa.Column("scheduled_local_time", sa.String(length=5), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("days_considered", sa.Integer(), nullable=False),
        sa.Column("days_processed", sa.Integer(), nullable=False),
        sa.Column("days_unchanged", sa.Integer(), nullable=False),
        sa.Column("days_partial", sa.Integer(), nullable=False),
        sa.Column("days_failed", sa.Integer(), nullable=False),
        sa.Column("outbox_created", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("error_message_redacted", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_iiko_sales_automation_runs_started_at",
        "iiko_sales_automation_runs",
        ["started_at"],
    )
    op.create_index(
        "ix_iiko_sales_automation_runs_status",
        "iiko_sales_automation_runs",
        ["status"],
    )

    op.create_table(
        "hermes_report_outbox",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("report_type", sa.String(length=50), nullable=False),
        sa.Column("organization_id", sa.String(length=120), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("source_checksum", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("payload_markdown", sa.Text(), nullable=False),
        sa.Column("delivery_status", sa.String(length=30), nullable=False),
        sa.Column("delivery_attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("external_message_id", sa.String(length=255), nullable=True),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("error_message_redacted", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_hermes_report_outbox_idempotency_key",
        ),
    )
    op.create_index(
        "ix_hermes_report_outbox_report_day",
        "hermes_report_outbox",
        ["report_type", "organization_id", "business_date"],
    )
    op.create_index(
        "ix_hermes_report_outbox_delivery_status",
        "hermes_report_outbox",
        ["delivery_status"],
    )
    op.create_index(
        "ix_hermes_report_outbox_next_attempt",
        "hermes_report_outbox",
        ["next_attempt_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_hermes_report_outbox_next_attempt", table_name="hermes_report_outbox")
    op.drop_index("ix_hermes_report_outbox_delivery_status", table_name="hermes_report_outbox")
    op.drop_index("ix_hermes_report_outbox_report_day", table_name="hermes_report_outbox")
    op.drop_table("hermes_report_outbox")
    op.drop_index(
        "ix_iiko_sales_automation_runs_status",
        table_name="iiko_sales_automation_runs",
    )
    op.drop_index(
        "ix_iiko_sales_automation_runs_started_at",
        table_name="iiko_sales_automation_runs",
    )
    op.drop_table("iiko_sales_automation_runs")
    op.drop_column("iiko_sales_daily", "requires_resync")
