from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from apps.core.app.models.sales import (
    HermesReportOutbox,
    IikoSalesAutomationRun,
    IikoSalesDaily,
    IikoSalesDailyPayment,
    IikoSalesDailyProduct,
)
from apps.core.app.repositories.iiko_sales import IikoSalesRepository
from apps.core.app.services.iiko_sales import IikoSalesSyncService
from integrations.iiko.client import IikoClient
from integrations.iiko.schemas import ResultStatus

logger = structlog.get_logger(__name__)

REPORT_TYPE_SALES_DAILY = "sales_daily"
NO_COMPARISON_DATA = "нет данных для сравнения"
SALES_AUTOMATION_LOCK_KEY = (
    int.from_bytes(
        hashlib.sha256(b"dos-amigos:iiko-sales-automation").digest()[:8],
        "big",
    )
    & 0x7FFFFFFFFFFFFFFF
)


@dataclass(frozen=True)
class SalesAutomationConfig:
    organization_id: str
    business_timezone: str
    automation_enabled: bool
    scheduled_local_time: str
    backfill_max_days: int
    retry_max_attempts: int
    retry_base_seconds: int
    outbox_enabled: bool
    hermes_delivery_mode: str
    payment_category_map: dict[str, str] = field(default_factory=dict)

    @property
    def scheduled_time(self) -> time:
        hour, minute = self.scheduled_local_time.split(":")
        return time(int(hour), int(minute))


@dataclass(frozen=True)
class SalesAutomationResult:
    status: str
    trigger_type: str
    organization_id: str
    date_from: date | None = None
    date_to: date | None = None
    days_considered: int = 0
    days_processed: int = 0
    days_unchanged: int = 0
    days_partial: int = 0
    days_failed: int = 0
    outbox_created: int = 0
    dry_run: bool = False
    error_code: str | None = None
    outbox_ids: list[str] = field(default_factory=list)

    def to_json_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "trigger_type": self.trigger_type,
            "organization_id": self.organization_id,
            "date_from": self.date_from.isoformat() if self.date_from else None,
            "date_to": self.date_to.isoformat() if self.date_to else None,
            "days_considered": self.days_considered,
            "days_processed": self.days_processed,
            "days_unchanged": self.days_unchanged,
            "days_partial": self.days_partial,
            "days_failed": self.days_failed,
            "outbox_created": self.outbox_created,
            "dry_run": self.dry_run,
            "error_code": self.error_code,
            "outbox_ids": self.outbox_ids,
        }


@dataclass(frozen=True)
class HermesPayload:
    source_checksum: str
    idempotency_key: str
    payload_json: dict[str, object]
    payload_markdown: str


class SalesAutomationLock(Protocol):
    def acquire(self) -> bool:
        raise NotImplementedError

    def release(self) -> None:
        raise NotImplementedError

    def is_held_elsewhere(self) -> bool:
        raise NotImplementedError


class FakeSalesAutomationLock:
    def __init__(self, *, acquired: bool = True) -> None:
        self.acquired = acquired
        self.acquire_calls = 0
        self.release_calls = 0

    def acquire(self) -> bool:
        self.acquire_calls += 1
        return self.acquired

    def release(self) -> None:
        self.release_calls += 1

    def is_held_elsewhere(self) -> bool:
        return not self.acquired


class PostgresAdvisorySalesAutomationLock:
    def __init__(self, session: Session, key: int = SALES_AUTOMATION_LOCK_KEY) -> None:
        self.session = session
        self.key = key
        self.acquired = False

    def acquire(self) -> bool:
        if self.session.bind is not None and self.session.bind.dialect.name != "postgresql":
            self.acquired = True
            return True
        acquired = bool(
            self.session.execute(
                text("SELECT pg_try_advisory_lock(:lock_key)"),
                {"lock_key": self.key},
            ).scalar_one()
        )
        self.acquired = acquired
        return acquired

    def release(self) -> None:
        if not self.acquired:
            return
        if self.session.bind is not None and self.session.bind.dialect.name == "postgresql":
            self.session.execute(
                text("SELECT pg_advisory_unlock(:lock_key)"),
                {"lock_key": self.key},
            )
        self.acquired = False

    def is_held_elsewhere(self) -> bool:
        if self.session.bind is not None and self.session.bind.dialect.name != "postgresql":
            return False
        acquired = bool(
            self.session.execute(
                text("SELECT pg_try_advisory_lock(:lock_key)"),
                {"lock_key": self.key},
            ).scalar_one()
        )
        if acquired:
            self.session.execute(
                text("SELECT pg_advisory_unlock(:lock_key)"),
                {"lock_key": self.key},
            )
        return not acquired


@dataclass(frozen=True)
class PublishResult:
    status: str
    external_message_id: str | None = None
    error_code: str | None = None
    error_message_redacted: str | None = None


class HermesReportPublisher(Protocol):
    async def publish(self, outbox: HermesReportOutbox) -> PublishResult:
        raise NotImplementedError


class DisabledHermesReportPublisher:
    async def publish(self, outbox: HermesReportOutbox) -> PublishResult:
        return PublishResult(status="disabled", error_code="hermes_delivery_disabled")


class MockHermesReportPublisher:
    async def publish(self, outbox: HermesReportOutbox) -> PublishResult:
        return PublishResult(
            status="delivered",
            external_message_id=f"mock-{outbox.report_type}-{outbox.business_date.isoformat()}",
        )


def money(value: Decimal | None) -> str:
    return format(value or Decimal("0"), "f")


def _human_decimal(value: Decimal | None, *, places: int) -> str:
    quant = Decimal("1").scaleb(-places)
    rounded = (value or Decimal("0")).quantize(quant, rounding=ROUND_HALF_UP)
    sign = "-" if rounded < 0 else ""
    rendered = format(abs(rounded), f".{places}f")
    whole, _, fraction = rendered.partition(".")
    grouped = f"{int(whole):,}".replace(",", " ")
    fraction = fraction.rstrip("0")
    if fraction:
        return f"{sign}{grouped},{fraction}"
    return f"{sign}{grouped}"


def _human_money(value: Decimal | None) -> str:
    return f"{_human_decimal(value, places=2)} ₽"


def _human_quantity(value: Decimal | None) -> str:
    return f"{_human_decimal(value, places=4)} шт."


def _comparison_percent(
    current: Decimal,
    previous: IikoSalesDaily | None,
) -> Decimal | None:
    if previous is None or previous.net_sales == 0:
        return None
    return ((current - previous.net_sales) / previous.net_sales * Decimal("100")).quantize(
        Decimal("0.1"),
        rounding=ROUND_HALF_UP,
    )


def _sales_comparison(
    current: Decimal,
    previous: IikoSalesDaily | None,
) -> str:
    percent = _comparison_percent(current, previous)
    if percent is None:
        return NO_COMPARISON_DATA
    sign = "+" if percent > 0 else ""
    return f"{sign}{_human_decimal(percent, places=1)} %"


def _payment_totals(payments: list[IikoSalesDailyPayment]) -> dict[str, Decimal]:
    totals = {
        "cash": Decimal("0"),
        "card": Decimal("0"),
        "other": Decimal("0"),
        "unknown": Decimal("0"),
    }
    for payment in payments:
        category = payment.payment_category
        if category not in totals:
            category = "other"
        totals[category] += payment.sales_amount
    return totals


def _daily_coo_facts(
    daily: IikoSalesDaily,
    products: list[IikoSalesDailyProduct],
    previous_day: IikoSalesDaily | None,
    previous_weekday: IikoSalesDaily | None,
) -> list[str]:
    facts: list[str] = []
    for label, previous in (
        ("к предыдущему дню", previous_day),
        ("к тому же дню прошлой недели", previous_weekday),
    ):
        percent = _comparison_percent(daily.net_sales, previous)
        if percent is None:
            continue
        abs_percent = f"{_human_decimal(abs(percent), places=1)} %"
        if percent > 0:
            facts.append(f"Выручка выросла {label} на {abs_percent}.")
        elif percent < 0:
            facts.append(f"Выручка снизилась {label} на {abs_percent}.")
        else:
            facts.append(f"Выручка не изменилась {label}.")
    if products:
        leader = products[0]
        facts.append(
            f"Лидер продаж: {leader.product_name_snapshot} — "
            f"{_human_quantity(leader.quantity)} / {_human_money(leader.net_sales)}."
        )
    if daily.gross_sales > 0 and daily.reported_discounts > 0:
        discount_share = (daily.reported_discounts / daily.gross_sales * Decimal("100")).quantize(
            Decimal("0.1"),
            rounding=ROUND_HALF_UP,
        )
        facts.append(
            f"Скидки составили {_human_decimal(discount_share, places=1)} % от валовой выручки."
        )
    return facts[:3]


def _daily_coo_attention(
    daily: IikoSalesDaily,
    payments: list[IikoSalesDailyPayment],
) -> list[str]:
    attention: list[str] = []
    if daily.reconciliation_error_code == "IIKO_DISCOUNT_RECONCILIATION_MISMATCH":
        attention.append(
            "В iiko обнаружено расхождение между суммой скидок и итоговой выручкой. "
            "Продажи загружены, но показатель скидок требует сверки."
        )
    else:
        if daily.result_status == ResultStatus.PARTIAL.value:
            attention.append(
                "Часть показателей не удалось подтвердить полностью; требуется сверка с iiko."
            )
        if daily.reconciliation_error_code:
            attention.append("В iiko есть расхождение, требующее ручной сверки.")
    if any(payment.payment_category == "unknown" for payment in payments):
        attention.append("Есть оплаты без подтверждённой категории.")
    return attention or ["нет"]


def build_sales_daily_hermes_payload(
    session: Session,
    organization_id: str,
    business_date: date,
    *,
    json_top_products: int = 10,
    markdown_top_products: int = 3,
) -> HermesPayload:
    repository = IikoSalesRepository(session)
    daily = repository.get_daily(organization_id, business_date)
    if daily is None:
        raise ValueError("sales day was not imported")

    payments = repository.list_payments(organization_id, business_date)
    products = repository.list_top_products(
        organization_id,
        business_date,
        limit=json_top_products,
    )
    previous_day = repository.get_daily(organization_id, business_date - timedelta(days=1))
    previous_weekday = repository.get_daily(organization_id, business_date - timedelta(days=7))
    previous_day_comparison = _sales_comparison(daily.net_sales, previous_day)
    previous_weekday_comparison = _sales_comparison(daily.net_sales, previous_weekday)
    warnings: list[str] = []
    if daily.reconciliation_error_code:
        warnings.append(daily.reconciliation_error_code)
    if daily.result_status == ResultStatus.PARTIAL.value:
        warnings.append("partial")
    if any(payment.payment_category == "unknown" for payment in payments):
        warnings.append("unknown_payment_category")

    payment_rows = [
        {
            "category": payment.payment_category,
            "payment_type_id": payment.payment_type_id,
            "payment_type_name": payment.payment_type_name,
            "amount": money(payment.sales_amount),
        }
        for payment in payments
    ]
    top_products = [
        {
            "product_id": product.product_id,
            "name": product.product_name_snapshot,
            "quantity": format(product.quantity, "f"),
            "net_sales": money(product.net_sales),
        }
        for product in products
    ]
    payload_json: dict[str, object] = {
        "schema_version": "1.0",
        "report_type": REPORT_TYPE_SALES_DAILY,
        "status": daily.result_status,
        "organization_id": daily.organization_id,
        "business_date": daily.business_date.isoformat(),
        "gross_sales": money(daily.gross_sales),
        "reported_discounts": money(daily.reported_discounts),
        "reported_increases": money(daily.reported_increases),
        "net_sales": money(daily.net_sales),
        "unexplained_adjustment": money(daily.unexplained_adjustment),
        "refunds": money(daily.refunds),
        "checks_count": daily.checks_count,
        "average_check": money(daily.average_check),
        "payments": payment_rows,
        "top_products": top_products,
        "source_checksum": daily.source_checksum,
        "warnings": warnings,
        "comparison_previous_day": previous_day_comparison,
        "comparison_same_weekday_previous_week": previous_weekday_comparison,
    }
    totals = _payment_totals(payments)
    other_total = totals["other"] + totals["unknown"]
    markdown_lines = [
        f"Dos Amigos — итоги {business_date.strftime('%d.%m.%Y')}",
        "",
        f"Выручка: {_human_money(daily.net_sales)}",
        f"К предыдущему дню: {previous_day_comparison}",
        f"К тому же дню прошлой недели: {previous_weekday_comparison}",
        "",
        f"Чеков: {daily.checks_count}",
        f"Средний чек: {_human_money(daily.average_check)}",
        f"Скидки: {_human_money(daily.reported_discounts)}",
        "",
        "Оплаты:",
        f"— наличные: {_human_money(totals['cash'])}",
        f"— карта: {_human_money(totals['card'])}",
        f"— другие: {_human_money(other_total)}",
        "",
        "Топ продаж:",
    ]
    for index, product in enumerate(products[:markdown_top_products], start=1):
        markdown_lines.append(
            f"{index}. {product.product_name_snapshot} — "
            f"{_human_quantity(product.quantity)} / {_human_money(product.net_sales)}"
        )
    if not products:
        markdown_lines.append("— нет данных")
    markdown_lines.extend(["", "Факты:"])
    for fact in _daily_coo_facts(daily, products, previous_day, previous_weekday):
        markdown_lines.append(f"— {fact}")
    if len(markdown_lines) >= 2 and markdown_lines[-1] == "Факты:":
        markdown_lines.append("— нет новых числовых наблюдений.")
    markdown_lines.extend(["", "Требует внимания:"])
    for item in _daily_coo_attention(daily, payments):
        markdown_lines.append(f"— {item}")
    idempotency_key = (
        f"{REPORT_TYPE_SALES_DAILY}:{organization_id}:"
        f"{business_date.isoformat()}:{daily.source_checksum}"
    )
    return HermesPayload(
        source_checksum=daily.source_checksum,
        idempotency_key=idempotency_key,
        payload_json=payload_json,
        payload_markdown="\n".join(markdown_lines),
    )


class IikoSalesAutomationService:
    def __init__(
        self,
        *,
        session: Session,
        client: IikoClient,
        config: SalesAutomationConfig,
        lock: SalesAutomationLock | None = None,
    ) -> None:
        self.session = session
        self.client = client
        self.config = config
        self.timezone = ZoneInfo(config.business_timezone)
        self.lock = lock or PostgresAdvisorySalesAutomationLock(session)
        self.sales_repository = IikoSalesRepository(session)

    def previous_business_date(self, now: datetime | None = None) -> date:
        local_now = self._local_now(now)
        return local_now.date() - timedelta(days=1)

    async def run_due(
        self,
        *,
        now: datetime | None = None,
        dry_run: bool = False,
        force: bool = False,
        no_publish: bool = False,
        retry_partial: bool = False,
    ) -> SalesAutomationResult:
        local_now = self._local_now(now)
        target_date = local_now.date() - timedelta(days=1)
        if not self.config.automation_enabled:
            return self._record_skipped("scheduled", "skipped", target_date, dry_run=dry_run)
        if local_now.time() < self.config.scheduled_time:
            logger.info("sales_automation_not_due", result_status="not_due")
            return self._record_skipped("scheduled", "not_due", target_date, dry_run=dry_run)
        if self._successful_scheduled_run_exists(local_now.date(), target_date):
            return self._record_skipped(
                "scheduled", "already_completed", target_date, dry_run=dry_run
            )

        due_days = self._find_backfill_days(
            target_date, self.config.backfill_max_days, retry_partial
        )
        backfill = await self.run_backfill(
            target_date=target_date,
            backfill_days=self.config.backfill_max_days,
            dry_run=dry_run,
            no_publish=no_publish,
            retry_partial=retry_partial,
        )
        if target_date in due_days:
            scheduled_result = SalesAutomationResult(
                status=backfill.status,
                trigger_type="scheduled",
                organization_id=self.config.organization_id,
                date_from=backfill.date_from,
                date_to=backfill.date_to,
                days_considered=backfill.days_considered,
                days_processed=backfill.days_processed,
                days_unchanged=backfill.days_unchanged,
                days_partial=backfill.days_partial,
                days_failed=backfill.days_failed,
                outbox_created=backfill.outbox_created,
                dry_run=dry_run,
                error_code=backfill.error_code,
                outbox_ids=backfill.outbox_ids,
            )
            run = self._start_run(
                "scheduled", backfill.date_from, backfill.date_to, dry_run=dry_run
            )
            self._finish_run(run, scheduled_result, dry_run=dry_run)
            return scheduled_result
        today = await self.run_date(
            target_date,
            trigger_type="scheduled",
            dry_run=dry_run,
            force=force,
            no_publish=no_publish,
        )
        return self._merge_results("scheduled", [backfill, today])

    async def run_backfill(
        self,
        *,
        target_date: date,
        backfill_days: int,
        dry_run: bool = False,
        no_publish: bool = False,
        retry_partial: bool = False,
    ) -> SalesAutomationResult:
        days = self._find_backfill_days(target_date, backfill_days, retry_partial)
        logger.info("sales_backfill_started", date_to=target_date.isoformat())
        results: list[SalesAutomationResult] = []
        for business_date in days:
            logger.info("sales_backfill_day_started", business_date=business_date.isoformat())
            results.append(
                await self.run_date(
                    business_date,
                    trigger_type="backfill",
                    dry_run=dry_run,
                    force=True,
                    no_publish=no_publish,
                )
            )
            logger.info("sales_backfill_day_completed", business_date=business_date.isoformat())
        merged = self._merge_results("backfill", results)
        return SalesAutomationResult(
            status=merged.status,
            trigger_type="backfill",
            organization_id=self.config.organization_id,
            date_from=merged.date_from,
            date_to=merged.date_to,
            days_considered=min(backfill_days, self.config.backfill_max_days),
            days_processed=merged.days_processed,
            days_unchanged=merged.days_unchanged,
            days_partial=merged.days_partial,
            days_failed=merged.days_failed,
            outbox_created=merged.outbox_created,
            dry_run=dry_run,
            error_code=merged.error_code,
            outbox_ids=merged.outbox_ids,
        )

    async def run_date(
        self,
        business_date: date,
        *,
        trigger_type: str = "manual",
        dry_run: bool = False,
        force: bool = False,
        no_publish: bool = False,
    ) -> SalesAutomationResult:
        if not self.lock.acquire():
            return self._record_skipped(
                trigger_type, "already_running", business_date, dry_run=dry_run
            )

        run = self._start_run(trigger_type, business_date, business_date, dry_run=dry_run)
        try:
            existing = self.sales_repository.get_daily(self.config.organization_id, business_date)
            if existing and not force:
                outbox = self.ensure_outbox(business_date, dry_run=dry_run or no_publish)
                result = SalesAutomationResult(
                    status="skipped",
                    trigger_type=trigger_type,
                    organization_id=self.config.organization_id,
                    date_from=business_date,
                    date_to=business_date,
                    days_considered=1,
                    days_processed=0,
                    days_unchanged=1,
                    outbox_created=outbox.outbox_created,
                    dry_run=dry_run,
                    outbox_ids=outbox.outbox_ids,
                )
                self._finish_run(run, result, dry_run=dry_run)
                return result

            sync_service = IikoSalesSyncService(
                session=self.session,
                client=self.client,
                organization_id=self.config.organization_id,
                business_timezone=self.config.business_timezone,
                payment_category_map=self.config.payment_category_map,
            )
            sync_result = await sync_service.sync_day(business_date, dry_run=dry_run)
            if sync_result.status in {ResultStatus.PROVEN, ResultStatus.PARTIAL}:
                outbox = self.ensure_outbox(business_date, dry_run=dry_run or no_publish)
                status = sync_result.status.value
                result = SalesAutomationResult(
                    status=status,
                    trigger_type=trigger_type,
                    organization_id=self.config.organization_id,
                    date_from=business_date,
                    date_to=business_date,
                    days_considered=1,
                    days_processed=1,
                    days_unchanged=1 if sync_result.unchanged else 0,
                    days_partial=1 if sync_result.status is ResultStatus.PARTIAL else 0,
                    outbox_created=outbox.outbox_created,
                    dry_run=dry_run,
                    error_code=sync_result.error_code,
                    outbox_ids=outbox.outbox_ids,
                )
            else:
                result = SalesAutomationResult(
                    status="failed",
                    trigger_type=trigger_type,
                    organization_id=self.config.organization_id,
                    date_from=business_date,
                    date_to=business_date,
                    days_considered=1,
                    days_processed=1,
                    days_failed=1,
                    dry_run=dry_run,
                    error_code=sync_result.error_code,
                )
            self._finish_run(run, result, dry_run=dry_run)
            return result
        except Exception as exc:
            result = SalesAutomationResult(
                status="failed",
                trigger_type=trigger_type,
                organization_id=self.config.organization_id,
                date_from=business_date,
                date_to=business_date,
                days_considered=1,
                days_failed=1,
                dry_run=dry_run,
                error_code=type(exc).__name__,
            )
            self._finish_run(run, result, dry_run=dry_run, error_message=str(exc))
            return result
        finally:
            self.lock.release()

    def ensure_outbox(
        self,
        business_date: date,
        *,
        dry_run: bool = False,
    ) -> SalesAutomationResult:
        if dry_run or not self.config.outbox_enabled:
            return SalesAutomationResult(
                status="skipped",
                trigger_type="outbox",
                organization_id=self.config.organization_id,
                date_from=business_date,
                date_to=business_date,
                dry_run=dry_run,
            )
        payload = build_sales_daily_hermes_payload(
            self.session,
            self.config.organization_id,
            business_date,
        )
        existing = self.session.scalar(
            select(HermesReportOutbox).where(
                HermesReportOutbox.idempotency_key == payload.idempotency_key
            )
        )
        if existing:
            if existing.delivery_status != "delivered" and (
                existing.payload_json != payload.payload_json
                or existing.payload_markdown != payload.payload_markdown
            ):
                existing.payload_json = payload.payload_json
                existing.payload_markdown = payload.payload_markdown
                existing.source_checksum = payload.source_checksum
                existing.updated_at = datetime.now(UTC)
                self.session.commit()
            return SalesAutomationResult(
                status="unchanged",
                trigger_type="outbox",
                organization_id=self.config.organization_id,
                date_from=business_date,
                date_to=business_date,
                outbox_created=0,
                outbox_ids=[existing.id],
            )
        self.session.execute(
            update(HermesReportOutbox)
            .where(HermesReportOutbox.report_type == REPORT_TYPE_SALES_DAILY)
            .where(HermesReportOutbox.organization_id == self.config.organization_id)
            .where(HermesReportOutbox.business_date == business_date)
            .where(HermesReportOutbox.delivery_status == "pending")
            .values(delivery_status="superseded", updated_at=datetime.now(UTC))
        )
        outbox = HermesReportOutbox(
            report_type=REPORT_TYPE_SALES_DAILY,
            organization_id=self.config.organization_id,
            business_date=business_date,
            source_checksum=payload.source_checksum,
            idempotency_key=payload.idempotency_key,
            payload_json=payload.payload_json,
            payload_markdown=payload.payload_markdown,
            delivery_status="pending",
            delivery_attempts=0,
            updated_at=datetime.now(UTC),
        )
        self.session.add(outbox)
        self.session.flush()
        self.session.commit()
        logger.info("hermes_outbox_created", business_date=business_date.isoformat())
        return SalesAutomationResult(
            status="created",
            trigger_type="outbox",
            organization_id=self.config.organization_id,
            date_from=business_date,
            date_to=business_date,
            outbox_created=1,
            outbox_ids=[outbox.id],
        )

    def rebuild_outbox(self, *, dry_run: bool = False) -> SalesAutomationResult:
        statement = (
            select(IikoSalesDaily.business_date)
            .where(IikoSalesDaily.organization_id == self.config.organization_id)
            .order_by(IikoSalesDaily.business_date)
        )
        created = 0
        outbox_ids: list[str] = []
        for business_date in self.session.scalars(statement):
            result = self.ensure_outbox(business_date, dry_run=dry_run)
            created += result.outbox_created
            outbox_ids.extend(result.outbox_ids)
        return SalesAutomationResult(
            status="completed",
            trigger_type="rebuild_outbox",
            organization_id=self.config.organization_id,
            outbox_created=created,
            outbox_ids=outbox_ids,
            dry_run=dry_run,
        )

    def _find_backfill_days(
        self,
        target_date: date,
        backfill_days: int,
        retry_partial: bool,
    ) -> list[date]:
        limit = min(backfill_days, self.config.backfill_max_days)
        start = target_date - timedelta(days=limit - 1)
        days: list[date] = []
        current = start
        while current <= target_date:
            daily = self.sales_repository.get_daily(self.config.organization_id, current)
            if daily is None:
                days.append(current)
            elif daily.requires_resync:
                days.append(current)
            elif retry_partial and daily.result_status == ResultStatus.PARTIAL.value:
                days.append(current)
            current += timedelta(days=1)
        return days

    def _local_now(self, now: datetime | None) -> datetime:
        value = now or datetime.now(UTC)
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(self.timezone)

    def _successful_scheduled_run_exists(self, local_date: date, target_date: date) -> bool:
        runs = self.session.scalars(
            select(IikoSalesAutomationRun)
            .where(IikoSalesAutomationRun.trigger_type == "scheduled")
            .where(IikoSalesAutomationRun.status.in_(["completed", "partial"]))
            .order_by(IikoSalesAutomationRun.started_at.desc())
        )
        for run in runs:
            if run.requested_date_to == target_date:
                return True
            started_at = run.started_at
            if started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=UTC)
            if started_at.astimezone(self.timezone).date() == local_date:
                return True
        return False

    def _record_skipped(
        self,
        trigger_type: str,
        status: str,
        business_date: date,
        *,
        dry_run: bool,
    ) -> SalesAutomationResult:
        result = SalesAutomationResult(
            status=status,
            trigger_type=trigger_type,
            organization_id=self.config.organization_id,
            date_from=business_date,
            date_to=business_date,
            dry_run=dry_run,
        )
        run = self._start_run(trigger_type, business_date, business_date, dry_run=dry_run)
        self._finish_run(run, result, dry_run=dry_run)
        return result

    def _start_run(
        self,
        trigger_type: str,
        date_from: date | None,
        date_to: date | None,
        *,
        dry_run: bool,
    ) -> IikoSalesAutomationRun | None:
        if dry_run:
            return None
        run = IikoSalesAutomationRun(
            trigger_type=trigger_type,
            requested_date_from=date_from,
            requested_date_to=date_to,
            business_timezone=self.config.business_timezone,
            scheduled_local_time=self.config.scheduled_local_time,
            started_at=datetime.now(UTC),
            status="running",
            days_considered=0,
            days_processed=0,
            days_unchanged=0,
            days_partial=0,
            days_failed=0,
            outbox_created=0,
        )
        self.session.add(run)
        self.session.commit()
        return run

    def _finish_run(
        self,
        run: IikoSalesAutomationRun | None,
        result: SalesAutomationResult,
        *,
        dry_run: bool,
        error_message: str | None = None,
    ) -> None:
        if dry_run or run is None:
            return
        run.status = self._run_status(result)
        run.finished_at = datetime.now(UTC)
        run.days_considered = result.days_considered
        run.days_processed = result.days_processed
        run.days_unchanged = result.days_unchanged
        run.days_partial = result.days_partial
        run.days_failed = result.days_failed
        run.outbox_created = result.outbox_created
        run.error_code = result.error_code
        run.error_message_redacted = error_message
        self.session.commit()

    @staticmethod
    def _run_status(result: SalesAutomationResult) -> str:
        if result.status == "partial":
            return "partial"
        if result.status in {"failed", "blocked"}:
            return "failed"
        if result.status in {"not_due", "already_completed", "already_running", "skipped"}:
            return result.status
        return "completed"

    def _merge_results(
        self,
        trigger_type: str,
        results: list[SalesAutomationResult],
    ) -> SalesAutomationResult:
        if not results:
            return SalesAutomationResult(
                status="completed",
                trigger_type=trigger_type,
                organization_id=self.config.organization_id,
            )
        status = "completed"
        if any(result.status == "failed" for result in results):
            status = "failed"
        elif any(result.status == "partial" for result in results):
            status = "partial"
        return SalesAutomationResult(
            status=status,
            trigger_type=trigger_type,
            organization_id=self.config.organization_id,
            date_from=min(
                (result.date_from for result in results if result.date_from), default=None
            ),
            date_to=max((result.date_to for result in results if result.date_to), default=None),
            days_considered=sum(result.days_considered for result in results),
            days_processed=sum(result.days_processed for result in results),
            days_unchanged=sum(result.days_unchanged for result in results),
            days_partial=sum(result.days_partial for result in results),
            days_failed=sum(result.days_failed for result in results),
            outbox_created=sum(result.outbox_created for result in results),
            dry_run=any(result.dry_run for result in results),
            error_code=next((result.error_code for result in results if result.error_code), None),
            outbox_ids=[outbox_id for result in results for outbox_id in result.outbox_ids],
        )


def create_sales_automation_config_from_settings(settings: Any) -> SalesAutomationConfig:
    return SalesAutomationConfig(
        organization_id=settings.iiko_organization_id,
        business_timezone=settings.business_timezone,
        automation_enabled=settings.sales_automation_enabled,
        scheduled_local_time=settings.sales_daily_run_local_time,
        backfill_max_days=settings.sales_backfill_max_days,
        retry_max_attempts=settings.sales_retry_max_attempts,
        retry_base_seconds=settings.sales_retry_base_seconds,
        outbox_enabled=settings.sales_outbox_enabled,
        hermes_delivery_mode=settings.hermes_delivery_mode,
        payment_category_map=settings.iiko_payment_category_map(),
    )


def sales_automation_status(session: Session, config: SalesAutomationConfig) -> dict[str, object]:
    last_run = session.scalars(
        select(IikoSalesAutomationRun).order_by(IikoSalesAutomationRun.started_at.desc())
    ).first()
    last_successful_day = session.scalar(
        select(func.max(IikoSalesDaily.business_date)).where(
            IikoSalesDaily.organization_id == config.organization_id
        )
    )
    pending_count = session.scalar(
        select(func.count())
        .select_from(HermesReportOutbox)
        .where(HermesReportOutbox.delivery_status == "pending")
    )
    failed_count = session.scalar(
        select(func.count())
        .select_from(HermesReportOutbox)
        .where(HermesReportOutbox.delivery_status == "failed")
    )
    lock = PostgresAdvisorySalesAutomationLock(session)
    return {
        "automation_enabled": config.automation_enabled,
        "business_timezone": config.business_timezone,
        "scheduled_local_time": config.scheduled_local_time,
        "last_run_at": last_run.started_at.isoformat() if last_run else None,
        "last_run_status": last_run.status if last_run else None,
        "last_successful_business_date": last_successful_day.isoformat()
        if last_successful_day
        else None,
        "pending_outbox_count": pending_count or 0,
        "failed_outbox_count": failed_count or 0,
        "current_lock_held": lock.is_held_elsewhere(),
        "recent_error_code": last_run.error_code if last_run else None,
    }
