from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.core.app.models.sales import (
    DailyCooReportRun,
    HermesReportOutbox,
    HermesReportRecipientDelivery,
)
from apps.core.app.services.iiko_sales import IikoSalesSyncService
from apps.core.app.services.iiko_sales_automation import (
    SALES_AUTOMATION_LOCK_KEY,
    HermesPayload,
    PostgresAdvisorySalesAutomationLock,
    SalesAutomationLock,
    build_sales_daily_hermes_payload,
)
from integrations.iiko.client import IikoClient
from integrations.iiko.schemas import ResultStatus

REPORT_TYPE_SALES_DAILY = "sales_daily"
REPORT_TYPE_SALES_DAILY_CORRECTION = "sales_daily_correction"
REPORT_TYPE_SALES_DAILY_ALERT = "sales_daily_alert"
_AUTH_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_TOKEN_ASSIGNMENT_RE = re.compile(r"\b(token|bot_token|authorization)=\S+", re.IGNORECASE)
_NUMERIC_ID_RE = re.compile(r"\b\d{6,}\b")
_URL_PASSWORD_RE = re.compile(r"://([^:/\s]+):([^@\s]+)@")


@dataclass(frozen=True)
class DailyCooReportConfig:
    organization_id: str
    business_timezone: str
    payment_category_map: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DailyCooReportResult:
    status: str
    mode: str
    business_date: date
    outbox_ids: list[str] = field(default_factory=list)
    dry_run: bool = False
    error_code: str | None = None
    error_message_redacted: str | None = None

    def to_json_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "mode": self.mode,
            "business_date": self.business_date.isoformat(),
            "outbox_ids": self.outbox_ids,
            "dry_run": self.dry_run,
            "error_code": self.error_code,
        }


def daily_coo_business_date(
    mode: str,
    *,
    now: datetime | None,
    business_timezone: str,
) -> date:
    local_now = now or datetime.now(UTC)
    if local_now.tzinfo is None:
        local_now = local_now.replace(tzinfo=UTC)
    local_date = local_now.astimezone(ZoneInfo(business_timezone)).date()
    if mode == "closeout":
        return local_date
    if mode == "reconcile":
        return local_date - timedelta(days=1)
    raise ValueError("mode must be closeout or reconcile")


class DailyCooReportOrchestrator:
    def __init__(
        self,
        *,
        session: Session,
        client: IikoClient,
        config: DailyCooReportConfig,
        lock: SalesAutomationLock | None = None,
    ) -> None:
        self.session = session
        self.client = client
        self.config = config
        self.lock = lock or PostgresAdvisorySalesAutomationLock(
            session,
            key=SALES_AUTOMATION_LOCK_KEY,
        )

    async def closeout(
        self,
        *,
        now: datetime | None = None,
        business_date: date | None = None,
        dry_run: bool = False,
    ) -> DailyCooReportResult:
        resolved_date = business_date or daily_coo_business_date(
            "closeout",
            now=now,
            business_timezone=self.config.business_timezone,
        )
        if dry_run:
            return await self._dry_run("closeout", resolved_date)
        if not self.lock.acquire():
            return self._result("already_running", "closeout", resolved_date)
        try:
            run = self._start_run("closeout", resolved_date, dry_run=False)
            try:
                try:
                    sync_result = await self._sync_day(resolved_date, dry_run=False)
                except Exception as exc:
                    return self._fail_run_with_alert(run, "closeout", resolved_date, exc)
                if sync_result.status not in {ResultStatus.PROVEN, ResultStatus.PARTIAL}:
                    error_code = sync_result.error_code or "iiko_sales_sync_failed"
                    alert = self._ensure_alert_outbox(
                        mode="closeout",
                        business_date=resolved_date,
                        error_code=error_code,
                    )
                    return self._finish_run(
                        run,
                        self._result(
                            "failed",
                            "closeout",
                            resolved_date,
                            outbox_ids=[alert.id],
                            error_code=error_code,
                        ),
                    )
                payload = build_sales_daily_hermes_payload(
                    self.session,
                    self.config.organization_id,
                    resolved_date,
                )
                outbox = self._upsert_sales_outbox(payload, resolved_date, operational=True)
                return self._finish_run(
                    run,
                    self._result(
                        "outbox_ready",
                        "closeout",
                        resolved_date,
                        outbox_ids=[outbox.id],
                    ),
                )
            except Exception as exc:
                return self._fail_run(run, "closeout", resolved_date, exc)
        finally:
            self.lock.release()

    async def reconcile(
        self,
        *,
        now: datetime | None = None,
        business_date: date | None = None,
        dry_run: bool = False,
    ) -> DailyCooReportResult:
        resolved_date = business_date or daily_coo_business_date(
            "reconcile",
            now=now,
            business_timezone=self.config.business_timezone,
        )
        if dry_run:
            return await self._dry_run("reconcile", resolved_date)
        if not self.lock.acquire():
            return self._result("already_running", "reconcile", resolved_date)
        try:
            run = self._start_run("reconcile", resolved_date, dry_run=False)
            try:
                original = self._latest_sales_report(resolved_date)
                if original is None:
                    alert = self._ensure_alert_outbox(
                        mode="reconcile",
                        business_date=resolved_date,
                        error_code="missing_closeout",
                    )
                    return self._finish_run(
                        run,
                        self._result(
                            "missing_closeout",
                            "reconcile",
                            resolved_date,
                            outbox_ids=[alert.id],
                            error_code="missing_closeout",
                        ),
                    )
                try:
                    sync_result = await self._sync_day(resolved_date, dry_run=False)
                except Exception as exc:
                    return self._fail_run_with_alert(run, "reconcile", resolved_date, exc)
                if sync_result.status not in {ResultStatus.PROVEN, ResultStatus.PARTIAL}:
                    error_code = sync_result.error_code or "iiko_sales_sync_failed"
                    alert = self._ensure_alert_outbox(
                        mode="reconcile",
                        business_date=resolved_date,
                        error_code=error_code,
                    )
                    return self._finish_run(
                        run,
                        self._result(
                            "failed",
                            "reconcile",
                            resolved_date,
                            outbox_ids=[alert.id],
                            error_code=error_code,
                        ),
                    )
                current_payload = build_sales_daily_hermes_payload(
                    self.session,
                    self.config.organization_id,
                    resolved_date,
                )
                changes = _payload_changes(original.payload_json, current_payload.payload_json)
                if not changes:
                    return self._finish_run(
                        run, self._result("reconciled", "reconcile", resolved_date)
                    )
                correction, created = self._ensure_correction_outbox(
                    original=original,
                    current=current_payload,
                    changes=changes,
                    business_date=resolved_date,
                )
                status = "correction_created" if created else "unchanged"
                return self._finish_run(
                    run,
                    self._result(
                        status,
                        "reconcile",
                        resolved_date,
                        outbox_ids=[correction.id],
                    ),
                )
            except Exception as exc:
                return self._fail_run(run, "reconcile", resolved_date, exc)
        finally:
            self.lock.release()

    async def _dry_run(self, mode: str, business_date: date) -> DailyCooReportResult:
        try:
            sync_result = await self._sync_day(business_date, dry_run=True)
        except Exception as exc:
            self.session.rollback()
            return self._result(
                "dry_run_failed",
                mode,
                business_date,
                error_code=type(exc).__name__,
                error_message_redacted=_safe_error_message(exc),
                dry_run=True,
            )
        if sync_result.status in {ResultStatus.PROVEN, ResultStatus.PARTIAL}:
            return self._result("dry_run_ready", mode, business_date, dry_run=True)
        return self._result(
            "dry_run_failed",
            mode,
            business_date,
            error_code=sync_result.error_code or "iiko_sales_sync_failed",
            dry_run=True,
        )

    async def _sync_day(self, business_date: date, *, dry_run: bool) -> Any:
        service = IikoSalesSyncService(
            session=self.session,
            client=self.client,
            organization_id=self.config.organization_id,
            business_timezone=self.config.business_timezone,
            payment_category_map=self.config.payment_category_map,
        )
        return await service.sync_day(business_date, dry_run=dry_run)

    def _upsert_sales_outbox(
        self,
        payload: HermesPayload,
        business_date: date,
        *,
        operational: bool,
    ) -> HermesReportOutbox:
        markdown = payload.payload_markdown
        if operational:
            markdown = markdown.replace(
                f"Dos Amigos — итоги {business_date.strftime('%d.%m.%Y')}",
                f"Dos Amigos — оперативные итоги {business_date.strftime('%d.%m.%Y')}",
                1,
            )
        existing = self.session.scalar(
            select(HermesReportOutbox).where(
                HermesReportOutbox.idempotency_key == payload.idempotency_key
            )
        )
        now = datetime.now(UTC)
        if existing:
            if self._sales_outbox_is_mutable(existing):
                self._update_sales_outbox(existing, payload=payload, markdown=markdown, now=now)
                self.session.commit()
            return existing

        existing_for_date = self._latest_sales_report(business_date)
        if existing_for_date is not None:
            if self._sales_outbox_is_mutable(existing_for_date):
                existing_for_date.idempotency_key = payload.idempotency_key
                self._update_sales_outbox(
                    existing_for_date,
                    payload=payload,
                    markdown=markdown,
                    now=now,
                )
                self.session.commit()
            return existing_for_date

        outbox = HermesReportOutbox(
            report_type=REPORT_TYPE_SALES_DAILY,
            organization_id=self.config.organization_id,
            business_date=business_date,
            source_checksum=payload.source_checksum,
            idempotency_key=payload.idempotency_key,
            payload_json=payload.payload_json,
            payload_markdown=markdown,
            delivery_status="pending",
            delivery_attempts=0,
            updated_at=now,
        )
        self.session.add(outbox)
        self.session.commit()
        return outbox

    def _sales_outbox_is_mutable(self, outbox: HermesReportOutbox) -> bool:
        if outbox.delivery_status == "delivered":
            return False
        return not self._sales_outbox_has_recipient_deliveries(outbox.id)

    def _sales_outbox_has_recipient_deliveries(self, report_id: str) -> bool:
        return (
            self.session.scalar(
                select(HermesReportRecipientDelivery.id)
                .where(HermesReportRecipientDelivery.report_id == report_id)
                .limit(1)
            )
            is not None
        )

    @staticmethod
    def _update_sales_outbox(
        outbox: HermesReportOutbox,
        *,
        payload: HermesPayload,
        markdown: str,
        now: datetime,
    ) -> None:
        outbox.payload_json = payload.payload_json
        outbox.payload_markdown = markdown
        outbox.source_checksum = payload.source_checksum
        outbox.updated_at = now

    def _latest_sales_report(self, business_date: date) -> HermesReportOutbox | None:
        return self.session.scalars(
            select(HermesReportOutbox)
            .where(HermesReportOutbox.report_type == REPORT_TYPE_SALES_DAILY)
            .where(HermesReportOutbox.organization_id == self.config.organization_id)
            .where(HermesReportOutbox.business_date == business_date)
            .order_by(
                HermesReportOutbox.created_at.desc(),
                HermesReportOutbox.updated_at.desc(),
                HermesReportOutbox.id.desc(),
            )
        ).first()

    def _ensure_correction_outbox(
        self,
        *,
        original: HermesReportOutbox,
        current: HermesPayload,
        changes: list[dict[str, str]],
        business_date: date,
    ) -> tuple[HermesReportOutbox, bool]:
        change_hash = hashlib.sha256(
            json.dumps(changes, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        idempotency_key = (
            f"{REPORT_TYPE_SALES_DAILY_CORRECTION}:{self.config.organization_id}:"
            f"{business_date.isoformat()}:{change_hash}"
        )
        existing = self.session.scalar(
            select(HermesReportOutbox).where(HermesReportOutbox.idempotency_key == idempotency_key)
        )
        if existing:
            return existing, False
        markdown = _correction_markdown(business_date, changes)
        outbox = HermesReportOutbox(
            report_type=REPORT_TYPE_SALES_DAILY_CORRECTION,
            organization_id=self.config.organization_id,
            business_date=business_date,
            source_checksum=current.source_checksum,
            idempotency_key=idempotency_key,
            payload_json={
                "schema_version": "1.0",
                "report_type": REPORT_TYPE_SALES_DAILY_CORRECTION,
                "business_date": business_date.isoformat(),
                "original_outbox_id": original.id,
                "changes": changes,
            },
            payload_markdown=markdown,
            delivery_status="pending",
            delivery_attempts=0,
            updated_at=datetime.now(UTC),
        )
        self.session.add(outbox)
        self.session.commit()
        return outbox, True

    def _ensure_alert_outbox(
        self,
        *,
        mode: str,
        business_date: date,
        error_code: str,
    ) -> HermesReportOutbox:
        idempotency_key = (
            f"{REPORT_TYPE_SALES_DAILY_ALERT}:{self.config.organization_id}:"
            f"{mode}:{business_date.isoformat()}:{error_code}"
        )
        existing = self.session.scalar(
            select(HermesReportOutbox).where(HermesReportOutbox.idempotency_key == idempotency_key)
        )
        markdown = _alert_markdown(mode=mode, business_date=business_date)
        now = datetime.now(UTC)
        if existing:
            if existing.delivery_status != "delivered":
                existing.payload_markdown = markdown
                existing.payload_json = _alert_payload_json(
                    mode=mode,
                    business_date=business_date,
                    error_code=error_code,
                )
                existing.updated_at = now
                self.session.commit()
            return existing
        outbox = HermesReportOutbox(
            report_type=REPORT_TYPE_SALES_DAILY_ALERT,
            organization_id=self.config.organization_id,
            business_date=business_date,
            source_checksum=hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest(),
            idempotency_key=idempotency_key,
            payload_json=_alert_payload_json(
                mode=mode,
                business_date=business_date,
                error_code=error_code,
            ),
            payload_markdown=markdown,
            delivery_status="pending",
            delivery_attempts=0,
            updated_at=now,
        )
        self.session.add(outbox)
        self.session.commit()
        return outbox

    def _start_run(self, mode: str, business_date: date, *, dry_run: bool) -> DailyCooReportRun:
        run = DailyCooReportRun(
            mode=mode,
            organization_id=self.config.organization_id,
            business_date=business_date,
            business_timezone=self.config.business_timezone,
            status="running",
            started_at=datetime.now(UTC),
            dry_run=dry_run,
        )
        self.session.add(run)
        self.session.commit()
        return run

    def _finish_run(
        self,
        run: DailyCooReportRun,
        result: DailyCooReportResult,
    ) -> DailyCooReportResult:
        run = self.session.get(DailyCooReportRun, run.id) or self.session.merge(run)
        run.status = result.status
        run.finished_at = datetime.now(UTC)
        run.outbox_id = result.outbox_ids[0] if result.outbox_ids else None
        if result.outbox_ids and result.status == "correction_created":
            run.correction_outbox_id = result.outbox_ids[0]
        run.error_code = result.error_code
        run.error_message_redacted = result.error_message_redacted
        self.session.commit()
        return result

    def _fail_run(
        self,
        run: DailyCooReportRun,
        mode: str,
        business_date: date,
        exc: Exception,
    ) -> DailyCooReportResult:
        self.session.rollback()
        return self._finish_run(
            run,
            self._result(
                "failed",
                mode,
                business_date,
                error_code=type(exc).__name__,
                error_message_redacted=_safe_error_message(exc),
            ),
        )

    def _fail_run_with_alert(
        self,
        run: DailyCooReportRun,
        mode: str,
        business_date: date,
        exc: Exception,
    ) -> DailyCooReportResult:
        self.session.rollback()
        result_without_alert = self._result(
            "failed",
            mode,
            business_date,
            error_code="iiko_sync_exception",
            error_message_redacted=_safe_error_message(exc),
        )
        try:
            alert = self._ensure_alert_outbox(
                mode=mode,
                business_date=business_date,
                error_code="iiko_sync_exception",
            )
            return self._finish_run(
                run,
                self._result(
                    "failed",
                    mode,
                    business_date,
                    outbox_ids=[alert.id],
                    error_code="iiko_sync_exception",
                    error_message_redacted=_safe_error_message(exc),
                ),
            )
        except Exception:
            self.session.rollback()
            try:
                return self._finish_run(run, result_without_alert)
            except Exception:
                self.session.rollback()
                return result_without_alert

    @staticmethod
    def _result(
        status: str,
        mode: str,
        business_date: date,
        *,
        outbox_ids: list[str] | None = None,
        error_code: str | None = None,
        error_message_redacted: str | None = None,
        dry_run: bool = False,
    ) -> DailyCooReportResult:
        return DailyCooReportResult(
            status=status,
            mode=mode,
            business_date=business_date,
            outbox_ids=outbox_ids or [],
            error_code=error_code,
            error_message_redacted=error_message_redacted,
            dry_run=dry_run,
        )


def _payload_changes(
    previous: dict[str, object],
    current: dict[str, object],
) -> list[dict[str, str]]:
    changes: list[dict[str, str]] = []
    labels = {
        "net_sales": ("Выручка", "money"),
        "gross_sales": ("Валовая выручка", "money"),
        "reported_discounts": ("Скидки", "money"),
        "checks_count": ("Чеков", "integer"),
        "average_check": ("Средний чек", "money"),
        "refunds": ("Возвраты", "money"),
    }
    for key, (label, value_type) in labels.items():
        old = str(previous.get(key))
        new = str(current.get(key))
        if old != new:
            changes.append(
                {
                    "label": label,
                    "old": _format_change_value(old, value_type),
                    "new": _format_change_value(new, value_type),
                }
            )

    previous_payments = _payments_snapshot(previous)
    current_payments = _payments_snapshot(current)
    for label in sorted(previous_payments.keys() | current_payments.keys()):
        old_amount = previous_payments.get(label, Decimal("0"))
        new_amount = current_payments.get(label, Decimal("0"))
        if old_amount != new_amount:
            changes.append(
                {
                    "label": label,
                    "old": _format_money(old_amount),
                    "new": _format_money(new_amount),
                }
            )

    previous_products = _products_snapshot(previous)
    current_products = _products_snapshot(current)
    for product_id in sorted(previous_products.keys() | current_products.keys()):
        old_product = previous_products.get(product_id)
        new_product = current_products.get(product_id)
        label = (
            (new_product or old_product or {}).get("name")
            or (new_product or old_product or {}).get("product_id")
            or "Товар"
        )
        old_rendered = _format_product_change(old_product)
        new_rendered = _format_product_change(new_product)
        if old_rendered != new_rendered:
            changes.append({"label": label, "old": old_rendered, "new": new_rendered})
    return changes


def _payments_snapshot(payload: dict[str, object]) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = {}
    payments = payload.get("payments")
    if not isinstance(payments, list):
        return {}
    for payment in payments:
        if not isinstance(payment, dict):
            continue
        category = _payment_label(str(payment.get("category")))
        amount = Decimal(str(payment.get("amount", "0")))
        totals[category] = totals.get(category, Decimal("0")) + amount
    return totals


def _products_snapshot(payload: dict[str, object]) -> dict[str, dict[str, str]]:
    products = payload.get("top_products")
    if not isinstance(products, list):
        return {}
    snapshot: dict[str, dict[str, str]] = {}
    for product in products:
        if not isinstance(product, dict):
            continue
        product_id = str(product.get("product_id"))
        snapshot[product_id] = {
            "product_id": product_id,
            "name": str(product.get("name") or product_id),
            "quantity": str(product.get("quantity", "0")),
            "net_sales": str(product.get("net_sales", "0")),
        }
    return snapshot


def _payment_label(category: str) -> str:
    return {
        "cash": "Наличные",
        "card": "Карта",
        "other": "Другие оплаты",
        "unknown": "Оплаты без категории",
    }.get(category, category)


def _format_change_value(value: str, value_type: str) -> str:
    if value_type == "money":
        return _format_money(Decimal(value))
    if value_type == "integer":
        return str(int(Decimal(value)))
    return value


def _format_money(value: Decimal) -> str:
    rounded = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    sign = "-" if rounded < 0 else ""
    rendered = format(abs(rounded), ".2f")
    whole, fraction = rendered.split(".")
    grouped = f"{int(whole):,}".replace(",", " ")
    if fraction == "00":
        return f"{sign}{grouped} ₽"
    return f"{sign}{grouped},{fraction} ₽"


def _format_quantity(value: str) -> str:
    amount = Decimal(value).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP).normalize()
    rendered = format(amount, "f").replace(".", ",")
    return f"{rendered} шт."


def _format_product_change(product: dict[str, str] | None) -> str:
    if product is None:
        return "нет"
    return (
        f"{_format_quantity(product['quantity'])} / {_format_money(Decimal(product['net_sales']))}"
    )


def _correction_markdown(business_date: date, changes: list[dict[str, str]]) -> str:
    lines = [
        f"Dos Amigos — корректировка итогов {business_date.strftime('%d.%m.%Y')}",
        "",
        "Изменившиеся показатели:",
    ]
    for change in changes:
        lines.append(f"— {change['label']}: было {change['old']}, стало {change['new']}")
    return "\n".join(lines)


def _alert_payload_json(
    *,
    mode: str,
    business_date: date,
    error_code: str,
) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "report_type": REPORT_TYPE_SALES_DAILY_ALERT,
        "mode": mode,
        "business_date": business_date.isoformat(),
        "error_code": error_code,
    }


def _alert_markdown(*, mode: str, business_date: date) -> str:
    mode_text = "оперативных итогов" if mode == "closeout" else "утренней сверки"
    return "\n".join(
        [
            f"Dos Amigos — техническое уведомление {business_date.strftime('%d.%m.%Y')}",
            "",
            f"Не удалось подтвердить данные iiko для {mode_text}.",
            "Финансовый отчёт не отправлен; требуется ручная проверка импорта.",
        ]
    )


def _safe_error_message(exc: BaseException) -> str:
    message = str(exc) or type(exc).__name__
    message = _AUTH_RE.sub("[redacted_auth]", message)
    message = _TOKEN_ASSIGNMENT_RE.sub("[redacted_secret]", message)
    message = _URL_PASSWORD_RE.sub("://[redacted_credentials]@", message)
    message = _NUMERIC_ID_RE.sub("[redacted_id]", message)
    return message[:500]
