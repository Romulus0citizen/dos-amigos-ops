"""Add daily COO report Stage B delivery tracking.

Revision ID: 20260719_0005
Revises: 20260718_0004
Create Date: 2026-07-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260719_0005"
down_revision: str | None = "20260718_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "hermes_report_recipient_deliveries",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("report_id", sa.String(length=36), nullable=False),
        sa.Column("recipient_key", sa.String(length=64), nullable=False),
        sa.Column("delivery_status", sa.String(length=30), nullable=False),
        sa.Column("delivery_attempts", sa.Integer(), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("error_message_redacted", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["hermes_report_outbox.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "report_id",
            "recipient_key",
            name="uq_hermes_report_recipient_delivery_report_recipient",
        ),
    )
    op.create_index(
        "ix_hermes_report_recipient_delivery_report",
        "hermes_report_recipient_deliveries",
        ["report_id"],
    )
    op.create_index(
        "ix_hermes_report_recipient_delivery_status",
        "hermes_report_recipient_deliveries",
        ["delivery_status"],
    )
    op.create_table(
        "daily_coo_report_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("mode", sa.String(length=30), nullable=False),
        sa.Column("organization_id", sa.String(length=120), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("business_timezone", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dry_run", sa.Boolean(), nullable=False),
        sa.Column("outbox_id", sa.String(length=36), nullable=True),
        sa.Column("correction_outbox_id", sa.String(length=36), nullable=True),
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
        "ix_daily_coo_report_runs_mode_started",
        "daily_coo_report_runs",
        ["mode", "started_at"],
    )
    op.create_index(
        "ix_daily_coo_report_runs_business_date",
        "daily_coo_report_runs",
        ["business_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_daily_coo_report_runs_business_date", table_name="daily_coo_report_runs")
    op.drop_index("ix_daily_coo_report_runs_mode_started", table_name="daily_coo_report_runs")
    op.drop_table("daily_coo_report_runs")
    op.drop_index(
        "ix_hermes_report_recipient_delivery_status",
        table_name="hermes_report_recipient_deliveries",
    )
    op.drop_index(
        "ix_hermes_report_recipient_delivery_report",
        table_name="hermes_report_recipient_deliveries",
    )
    op.drop_table("hermes_report_recipient_deliveries")
