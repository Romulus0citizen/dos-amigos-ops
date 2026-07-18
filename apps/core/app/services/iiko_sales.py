from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy.orm import Session

from apps.core.app.repositories.iiko_sales import IikoSalesRepository
from integrations.iiko.client import IikoClient
from integrations.iiko.sales import (
    NormalizedSalesReport,
    SalesNormalizationError,
    normalize_iiko_sales_payload,
)
from integrations.iiko.schemas import ResultStatus

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class IikoSalesSyncResult:
    status: ResultStatus
    organization_id: str
    date_from: date
    date_to: date
    days_processed: int
    source_rows: int
    records_persisted: int
    unchanged: bool
    dry_run: bool
    error_code: str | None = None
    source_checksum: str | None = None

    def to_json_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "organization_id": self.organization_id,
            "date_from": self.date_from.isoformat(),
            "date_to": self.date_to.isoformat(),
            "days_processed": self.days_processed,
            "source_rows": self.source_rows,
            "records_persisted": self.records_persisted,
            "unchanged": self.unchanged,
            "dry_run": self.dry_run,
            "error_code": self.error_code,
            "source_checksum": self.source_checksum,
        }


class IikoSalesSyncService:
    def __init__(
        self,
        *,
        session: Session,
        client: IikoClient,
        organization_id: str,
        business_timezone: str,
        payment_category_map: dict[str, str] | None = None,
    ) -> None:
        self.session = session
        self.client = client
        self.organization_id = organization_id
        self.business_timezone = ZoneInfo(business_timezone)
        self.payment_category_map = payment_category_map or {}
        self.repository = IikoSalesRepository(session)

    def validate_date_range(
        self,
        *,
        date_from: date,
        date_to: date,
        allow_open_day: bool = False,
    ) -> None:
        if date_to < date_from:
            raise ValueError("date_to must be greater than or equal to date_from")
        if (date_to - date_from).days > 30:
            raise ValueError("date range must not exceed 31 days")
        today = datetime.now(self.business_timezone).date()
        if not allow_open_day and date_to >= today:
            raise ValueError("open business day requires --allow-open-day")

    async def sync_range(
        self,
        *,
        date_from: date,
        date_to: date,
        dry_run: bool = False,
        allow_open_day: bool = False,
    ) -> IikoSalesSyncResult:
        self.validate_date_range(
            date_from=date_from,
            date_to=date_to,
            allow_open_day=allow_open_day,
        )
        total_source_rows = 0
        total_persisted = 0
        unchanged = True
        final_status = ResultStatus.PROVEN
        final_error_code: str | None = None
        final_checksum: str | None = None
        current = date_from
        days_processed = 0
        while current <= date_to:
            result = await self.sync_day(current, dry_run=dry_run)
            days_processed += 1
            total_source_rows += result.source_rows
            total_persisted += result.records_persisted
            unchanged = unchanged and result.unchanged
            final_checksum = result.source_checksum or final_checksum
            if result.status is ResultStatus.UNKNOWN:
                final_status = ResultStatus.UNKNOWN
            elif result.status is ResultStatus.BLOCKED and final_status is not ResultStatus.UNKNOWN:
                final_status = ResultStatus.BLOCKED
            elif result.status is ResultStatus.PARTIAL and final_status is ResultStatus.PROVEN:
                final_status = ResultStatus.PARTIAL
            final_error_code = result.error_code or final_error_code
            current += timedelta(days=1)

        return IikoSalesSyncResult(
            status=final_status,
            organization_id=self.organization_id,
            date_from=date_from,
            date_to=date_to,
            days_processed=days_processed,
            source_rows=total_source_rows,
            records_persisted=total_persisted,
            unchanged=unchanged,
            dry_run=dry_run,
            error_code=final_error_code,
            source_checksum=final_checksum,
        )

    async def sync_day(self, business_date: date, *, dry_run: bool = False) -> IikoSalesSyncResult:
        started_at = datetime.now(UTC)
        logger.info(
            "iiko_sales_sync_started",
            organization_id=self.organization_id,
            date_from=business_date.isoformat(),
            date_to=business_date.isoformat(),
        )
        raw_result = await self.client.fetch_orders_or_sales(
            {
                "organization_id": self.organization_id,
                "business_date": business_date,
            }
        )
        fetched_rows = raw_result.records_count
        if raw_result.status is not ResultStatus.PROVEN:
            finished_at = datetime.now(UTC)
            if not dry_run:
                self.repository.record_failed_run(
                    organization_id=self.organization_id,
                    business_date=business_date,
                    status=raw_result.status.value,
                    started_at=started_at,
                    finished_at=finished_at,
                    fetched_rows=fetched_rows,
                    error_code=raw_result.error_code or "iiko_sales_fetch_failed",
                    error_message_redacted=raw_result.error_message_sanitized
                    or "iiko sales fetch failed",
                )
                self.session.commit()
            return IikoSalesSyncResult(
                status=raw_result.status,
                organization_id=self.organization_id,
                date_from=business_date,
                date_to=business_date,
                days_processed=1,
                source_rows=fetched_rows,
                records_persisted=0,
                unchanged=False,
                dry_run=dry_run,
                error_code=raw_result.error_code,
            )

        logger.info(
            "iiko_sales_fetch_completed",
            organization_id=self.organization_id,
            records_count=fetched_rows,
            result_status=raw_result.status.value,
        )
        try:
            normalized = normalize_iiko_sales_payload(
                raw_result.payload,
                organization_id=self.organization_id,
                business_date=business_date,
                payment_category_map=self.payment_category_map,
            )
        except SalesNormalizationError as exc:
            finished_at = datetime.now(UTC)
            if not dry_run:
                self.repository.record_failed_run(
                    organization_id=self.organization_id,
                    business_date=business_date,
                    status=ResultStatus.UNKNOWN.value,
                    started_at=started_at,
                    finished_at=finished_at,
                    fetched_rows=fetched_rows,
                    error_code=exc.error_code,
                    error_message_redacted=str(exc),
                )
                self.session.commit()
            return IikoSalesSyncResult(
                status=ResultStatus.UNKNOWN,
                organization_id=self.organization_id,
                date_from=business_date,
                date_to=business_date,
                days_processed=1,
                source_rows=fetched_rows,
                records_persisted=0,
                unchanged=False,
                dry_run=dry_run,
                error_code=exc.error_code,
            )

        logger.info(
            "iiko_sales_validation_completed",
            organization_id=self.organization_id,
            records_count=normalized.daily.source_rows_count,
            result_status=normalized.result_status.value,
            checksum_prefix=normalized.daily.source_checksum[:12],
        )
        if dry_run:
            return self._result_from_normalized(
                normalized, dry_run=True, persisted_rows=0, unchanged=False
            )

        finished_at = datetime.now(UTC)
        try:
            persistence = self.repository.persist_report(
                normalized,
                started_at=started_at,
                finished_at=finished_at,
            )
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise

        if persistence.unchanged:
            logger.info(
                "iiko_sales_sync_unchanged",
                organization_id=self.organization_id,
                checksum_prefix=normalized.daily.source_checksum[:12],
            )
        logger.info(
            "iiko_sales_sync_completed",
            organization_id=self.organization_id,
            records_count=persistence.persisted_rows,
            result_status=normalized.result_status.value,
        )
        return self._result_from_normalized(
            normalized,
            dry_run=False,
            persisted_rows=persistence.persisted_rows,
            unchanged=persistence.unchanged,
        )

    def _result_from_normalized(
        self,
        normalized: NormalizedSalesReport,
        *,
        dry_run: bool,
        persisted_rows: int,
        unchanged: bool,
    ) -> IikoSalesSyncResult:
        daily = normalized.daily
        return IikoSalesSyncResult(
            status=daily.result_status,
            organization_id=daily.organization_id,
            date_from=daily.business_date,
            date_to=daily.business_date,
            days_processed=1,
            source_rows=daily.source_rows_count,
            records_persisted=persisted_rows,
            unchanged=unchanged,
            dry_run=dry_run,
            error_code=daily.reconciliation_error_code,
            source_checksum=daily.source_checksum,
        )


def parse_payment_category_map(raw: str) -> dict[str, str]:
    if not raw.strip():
        return {}
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("payment category map must be a JSON object")
    return {str(key): str(value) for key, value in parsed.items()}
