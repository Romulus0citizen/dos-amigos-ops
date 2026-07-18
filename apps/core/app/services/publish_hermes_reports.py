from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.core.app.models.sales import HermesReportOutbox
from apps.core.app.services.iiko_sales_automation import HermesReportPublisher

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class HermesPublishSummary:
    status: str
    considered: int
    delivered: int
    failed: int
    dry_run: bool = False
    error_code: str | None = None

    def to_json_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "considered": self.considered,
            "delivered": self.delivered,
            "failed": self.failed,
            "dry_run": self.dry_run,
            "error_code": self.error_code,
        }


class HermesOutboxPublishService:
    def __init__(
        self,
        *,
        session: Session,
        publisher: HermesReportPublisher,
    ) -> None:
        self.session = session
        self.publisher = publisher

    async def publish_pending(
        self,
        *,
        limit: int = 20,
        business_date: date | None = None,
        retry_failed: bool = False,
        dry_run: bool = False,
    ) -> HermesPublishSummary:
        statuses = ["pending"]
        if retry_failed:
            statuses.append("failed")
        statement = (
            select(HermesReportOutbox)
            .where(HermesReportOutbox.delivery_status.in_(statuses))
            .order_by(HermesReportOutbox.created_at, HermesReportOutbox.id)
            .limit(limit)
        )
        if business_date:
            statement = statement.where(HermesReportOutbox.business_date == business_date)
        rows = list(self.session.scalars(statement))
        if not rows:
            return HermesPublishSummary(status="empty", considered=0, delivered=0, failed=0)

        delivered = 0
        failed = 0
        disabled = False
        for row in rows:
            if dry_run:
                continue
            row.delivery_status = "delivering"
            row.delivery_attempts += 1
            row.last_attempt_at = datetime.now(UTC)
            self.session.commit()
            result = await self.publisher.publish(row)
            if result.status == "disabled":
                row.delivery_status = "pending"
                row.error_code = result.error_code
                disabled = True
                logger.info("hermes_delivery_disabled", business_date=row.business_date.isoformat())
            elif result.status == "delivered":
                row.delivery_status = "delivered"
                row.delivered_at = datetime.now(UTC)
                row.external_message_id = result.external_message_id
                row.error_code = None
                row.error_message_redacted = None
                delivered += 1
            else:
                row.delivery_status = "failed"
                row.error_code = result.error_code or "hermes_delivery_failed"
                row.error_message_redacted = result.error_message_redacted
                failed += 1
            row.updated_at = datetime.now(UTC)
            self.session.commit()

        if dry_run:
            return HermesPublishSummary(
                status="dry_run",
                considered=len(rows),
                delivered=0,
                failed=0,
                dry_run=True,
            )
        if disabled:
            return HermesPublishSummary(
                status="disabled",
                considered=len(rows),
                delivered=delivered,
                failed=failed,
                error_code="hermes_delivery_disabled",
            )
        if failed:
            return HermesPublishSummary(
                status="failed",
                considered=len(rows),
                delivered=delivered,
                failed=failed,
            )
        return HermesPublishSummary(
            status="delivered",
            considered=len(rows),
            delivered=delivered,
            failed=failed,
        )
