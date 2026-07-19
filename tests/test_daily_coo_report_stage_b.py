from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from fixtures_iiko_sales import ORG_ID, copied_payload
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from apps.core.app.models.sales import (
    DailyCooReportRun,
    HermesReportOutbox,
    HermesReportRecipientDelivery,
    IikoSalesAutomationRun,
    IikoSalesDaily,
    IikoSalesDailyPayment,
    IikoSalesDailyProduct,
    IikoSalesSyncRun,
)
from apps.core.app.repositories.iiko_sales import IikoSalesRepository
from apps.core.app.services.daily_coo_report import (
    DailyCooReportConfig,
    DailyCooReportOrchestrator,
    daily_coo_business_date,
)
from apps.core.app.services.iiko_sales_automation import (
    SALES_AUTOMATION_LOCK_KEY,
    FakeSalesAutomationLock,
    build_sales_daily_hermes_payload,
)
from integrations.iiko.client import IikoClient
from integrations.iiko.sales import normalize_iiko_sales_payload
from integrations.iiko.schemas import AuthResult, IikoMode, ProbeResult, RawResult, ResultStatus


def session_factory():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for table in (
        IikoSalesSyncRun.__table__,
        IikoSalesDaily.__table__,
        IikoSalesDailyPayment.__table__,
        IikoSalesDailyProduct.__table__,
        IikoSalesAutomationRun.__table__,
        HermesReportOutbox.__table__,
        HermesReportRecipientDelivery.__table__,
        DailyCooReportRun.__table__,
    ):
        table.create(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def payload_for_day(business_date: date, *, net_sales: Decimal) -> dict[str, Any]:
    payload = copied_payload()
    for report in payload.values():
        for row in report["data"]:
            row["OpenDate.Typed"] = business_date.isoformat()
    payload["daily"]["data"][0]["DishDiscountSumInt"] = net_sales
    payload["payments"]["data"][1]["DishDiscountSumInt"] = net_sales - Decimal("10000")
    payload["products"]["data"][0]["DishDiscountSumInt"] = net_sales - Decimal("13955.25")
    return payload


def persist_day(session: Session, business_date: date, *, net_sales: Decimal) -> IikoSalesDaily:
    report = normalize_iiko_sales_payload(
        payload_for_day(business_date, net_sales=net_sales),
        organization_id=ORG_ID,
        business_date=business_date,
    )
    IikoSalesRepository(session).persist_report(
        report,
        started_at=datetime(2026, 7, 19, tzinfo=UTC),
        finished_at=datetime(2026, 7, 19, 0, 1, tzinfo=UTC),
    )
    session.commit()
    daily = session.get(IikoSalesDaily, {"organization_id": ORG_ID, "business_date": business_date})
    assert daily is not None
    return daily


class FakeSalesClient(IikoClient):
    adapter_name = "fake"
    mode = IikoMode.MOCK

    def __init__(self, *, net_sales: Decimal = Decimal("31955.25")) -> None:
        self.net_sales = net_sales
        self.requested_dates: list[date] = []

    async def authenticate(self) -> AuthResult:
        raise NotImplementedError

    async def probe(self) -> ProbeResult:
        raise NotImplementedError

    async def list_organizations(self) -> RawResult:
        raise NotImplementedError

    async def list_terminal_groups(self, organization_ref: str) -> RawResult:
        raise NotImplementedError

    async def fetch_nomenclature(self, organization_ref: str) -> RawResult:
        raise NotImplementedError

    async def fetch_menu(self, organization_ref: str) -> RawResult:
        raise NotImplementedError

    async def fetch_orders_or_sales(self, parameters=None) -> RawResult:
        business_date = parameters["business_date"]
        self.requested_dates.append(business_date)
        return RawResult.proven(
            adapter=self.adapter_name,
            mode=self.mode,
            dataset="orders_or_sales",
            trace_id="trace-stage-b",
            payload=payload_for_day(business_date, net_sales=self.net_sales),
            records_count=5,
        )

    async def fetch_payments(self, parameters=None) -> RawResult:
        raise NotImplementedError

    async def fetch_inventory(self, parameters=None) -> RawResult:
        raise NotImplementedError

    async def fetch_writeoffs(self, parameters=None) -> RawResult:
        raise NotImplementedError

    async def fetch_costs(self, parameters=None) -> RawResult:
        raise NotImplementedError

    async def fetch_employees_or_shifts(self, parameters=None) -> RawResult:
        raise NotImplementedError

    async def close(self) -> None:
        return None


class FailingSalesClient(FakeSalesClient):
    async def fetch_orders_or_sales(self, parameters=None) -> RawResult:
        business_date = parameters["business_date"]
        self.requested_dates.append(business_date)
        return RawResult(
            status=ResultStatus.BLOCKED,
            adapter=self.adapter_name,
            mode=self.mode,
            dataset="orders_or_sales",
            trace_id="trace-stage-b-failed",
            error_code="iiko_unavailable",
            error_message_sanitized="iiko unavailable",
        )


class ExplodingSalesClient(FakeSalesClient):
    async def fetch_orders_or_sales(self, parameters=None) -> RawResult:
        business_date = parameters["business_date"]
        self.requested_dates.append(business_date)
        raise RuntimeError(
            "database postgresql://user:password@example.internal/db token=secret chat 100100100"
        )


def config() -> DailyCooReportConfig:
    return DailyCooReportConfig(
        organization_id=ORG_ID,
        business_timezone="Asia/Yekaterinburg",
        payment_category_map={},
    )


def orchestrator(
    session: Session,
    *,
    client: FakeSalesClient,
    lock: FakeSalesAutomationLock | None = None,
) -> DailyCooReportOrchestrator:
    return DailyCooReportOrchestrator(
        session=session,
        client=client,
        config=config(),
        lock=lock or FakeSalesAutomationLock(),
    )


def test_closeout_uses_current_local_business_date() -> None:
    assert daily_coo_business_date(
        "closeout",
        now=datetime(2026, 7, 19, 18, 30, tzinfo=UTC),
        business_timezone="Asia/Yekaterinburg",
    ) == date(2026, 7, 19)


def test_reconcile_uses_previous_local_business_date() -> None:
    assert daily_coo_business_date(
        "reconcile",
        now=datetime(2026, 7, 19, 1, 0, tzinfo=UTC),
        business_timezone="Asia/Yekaterinburg",
    ) == date(2026, 7, 18)


async def test_closeout_imports_current_day_creates_operational_outbox() -> None:
    SessionLocal = session_factory()
    with SessionLocal() as session:
        client = FakeSalesClient()
        service = orchestrator(session, client=client)

        result = await service.closeout(
            now=datetime(2026, 7, 19, 18, 30, tzinfo=UTC),
        )

        assert result.status == "outbox_ready"
        assert result.business_date == date(2026, 7, 19)
        assert client.requested_dates == [date(2026, 7, 19)]
        outbox = session.scalars(select(HermesReportOutbox)).one()
        assert outbox.report_type == "sales_daily"
        assert outbox.payload_markdown.startswith("Dos Amigos — оперативные итоги 19.07.2026")
        assert session.scalar(select(func.count()).select_from(DailyCooReportRun)) == 1


async def test_parallel_closeout_is_blocked_without_import_or_delivery() -> None:
    SessionLocal = session_factory()
    with SessionLocal() as session:
        client = FakeSalesClient()
        service = orchestrator(
            session,
            client=client,
            lock=FakeSalesAutomationLock(acquired=False),
        )

        result = await service.closeout(
            now=datetime(2026, 7, 19, 18, 30, tzinfo=UTC),
        )

        assert result.status == "already_running"
        assert client.requested_dates == []
        assert session.scalar(select(func.count()).select_from(DailyCooReportRun)) == 0


async def test_closeout_dry_run_does_not_send_or_mutate_database() -> None:
    SessionLocal = session_factory()
    with SessionLocal() as session:
        client = FakeSalesClient()
        service = orchestrator(session, client=client)

        result = await service.closeout(
            now=datetime(2026, 7, 19, 18, 30, tzinfo=UTC),
            dry_run=True,
        )

        assert result.status == "dry_run_ready"
        assert client.requested_dates == [date(2026, 7, 19)]
        assert result.error_code is None
        assert result.dry_run is True
        assert session.scalar(select(func.count()).select_from(DailyCooReportRun)) == 0
        assert session.scalar(select(func.count()).select_from(HermesReportOutbox)) == 0
        assert session.scalar(select(func.count()).select_from(IikoSalesDaily)) == 0
        assert session.scalar(select(func.count()).select_from(IikoSalesSyncRun)) == 0


async def test_closeout_iiko_error_records_failed_run_and_alert_without_sales_report() -> None:
    SessionLocal = session_factory()
    with SessionLocal() as session:
        client = FailingSalesClient()
        service = orchestrator(session, client=client)

        result = await service.closeout(
            now=datetime(2026, 7, 19, 18, 30, tzinfo=UTC),
        )

        assert result.status == "failed"
        assert client.requested_dates == [date(2026, 7, 19)]
        outbox = session.scalars(select(HermesReportOutbox)).one()
        assert outbox.report_type == "sales_daily_alert"
        assert "техническое уведомление" in outbox.payload_markdown
        assert "iiko_unavailable" not in outbox.payload_markdown
        assert "оперативные итоги" not in outbox.payload_markdown
        run = session.scalars(select(DailyCooReportRun)).one()
        assert run.status == "failed"
        assert run.outbox_id == outbox.id


async def test_closeout_exception_closes_run_as_failed_and_redacts_error() -> None:
    SessionLocal = session_factory()
    with SessionLocal() as session:
        client = ExplodingSalesClient()
        lock = FakeSalesAutomationLock()
        service = orchestrator(session, client=client, lock=lock)

        result = await service.closeout(
            now=datetime(2026, 7, 19, 18, 30, tzinfo=UTC),
        )

        assert result.status == "failed"
        assert result.error_code == "iiko_sync_exception"
        assert lock.release_calls == 1
        outbox = session.scalars(select(HermesReportOutbox)).one()
        assert result.outbox_ids == [outbox.id]
        assert outbox.report_type == "sales_daily_alert"
        assert outbox.payload_markdown.startswith("Dos Amigos — техническое уведомление 19.07.2026")
        assert "postgresql://" not in outbox.payload_markdown
        assert "password" not in outbox.payload_markdown
        assert "token=secret" not in outbox.payload_markdown
        assert "100100100" not in outbox.payload_markdown
        run = session.scalars(select(DailyCooReportRun)).one()
        assert run.status == "failed"
        assert run.finished_at is not None
        assert run.outbox_id == outbox.id
        assert run.error_message_redacted is not None
        assert "password" not in run.error_message_redacted
        assert "token=secret" not in run.error_message_redacted
        assert "100100100" not in run.error_message_redacted


async def test_reconcile_without_changes_records_success_and_does_not_create_message() -> None:
    SessionLocal = session_factory()
    with SessionLocal() as session:
        business_date = date(2026, 7, 18)
        persist_day(session, business_date, net_sales=Decimal("31955.25"))
        original_payload = build_sales_daily_hermes_payload(session, ORG_ID, business_date)
        outbox = HermesReportOutbox(
            report_type="sales_daily",
            organization_id=ORG_ID,
            business_date=business_date,
            source_checksum=original_payload.source_checksum,
            idempotency_key=original_payload.idempotency_key,
            payload_json=original_payload.payload_json,
            payload_markdown=original_payload.payload_markdown,
            delivery_status="delivered",
            delivery_attempts=1,
            delivered_at=datetime(2026, 7, 18, 23, 31, tzinfo=UTC),
            updated_at=datetime(2026, 7, 18, 23, 31, tzinfo=UTC),
        )
        session.add(outbox)
        session.commit()

        result = await orchestrator(session, client=FakeSalesClient()).reconcile(
            now=datetime(2026, 7, 19, 1, 0, tzinfo=UTC),
        )

        assert result.status == "reconciled"
        assert result.outbox_ids == []
        assert session.scalar(select(func.count()).select_from(HermesReportOutbox)) == 1


async def test_reconcile_missing_closeout_creates_alert() -> None:
    SessionLocal = session_factory()
    with SessionLocal() as session:
        result = await orchestrator(session, client=FakeSalesClient()).reconcile(
            now=datetime(2026, 7, 19, 1, 0, tzinfo=UTC),
        )

        assert result.status == "missing_closeout"
        outbox = session.scalars(select(HermesReportOutbox)).one()
        assert result.outbox_ids == [outbox.id]
        assert outbox.report_type == "sales_daily_alert"
        assert outbox.payload_markdown.startswith("Dos Amigos — техническое уведомление 18.07.2026")
        assert "Финансовый отчёт не отправлен" in outbox.payload_markdown


async def test_reconcile_uses_failed_closeout_as_baseline_for_recipient_retry() -> None:
    SessionLocal = session_factory()
    with SessionLocal() as session:
        business_date = date(2026, 7, 18)
        persist_day(session, business_date, net_sales=Decimal("31955.25"))
        original_payload = build_sales_daily_hermes_payload(session, ORG_ID, business_date)
        outbox = HermesReportOutbox(
            report_type="sales_daily",
            organization_id=ORG_ID,
            business_date=business_date,
            source_checksum=original_payload.source_checksum,
            idempotency_key=original_payload.idempotency_key,
            payload_json=original_payload.payload_json,
            payload_markdown=original_payload.payload_markdown,
            delivery_status="failed",
            delivery_attempts=1,
            last_attempt_at=datetime(2026, 7, 18, 23, 31, tzinfo=UTC),
            updated_at=datetime(2026, 7, 18, 23, 31, tzinfo=UTC),
        )
        session.add(outbox)
        session.commit()

        result = await orchestrator(
            session,
            client=FakeSalesClient(net_sales=Decimal("32955.25")),
        ).reconcile(now=datetime(2026, 7, 19, 1, 0, tzinfo=UTC))

        assert result.status == "correction_created"
        report_types = list(
            session.scalars(
                select(HermesReportOutbox.report_type).order_by(HermesReportOutbox.report_type)
            )
        )
        assert report_types == ["sales_daily", "sales_daily_correction"]


async def test_closeout_does_not_rewrite_report_after_recipient_delivery_registration() -> None:
    SessionLocal = session_factory()
    with SessionLocal() as session:
        business_date = date(2026, 7, 19)
        persist_day(session, business_date, net_sales=Decimal("31955.25"))
        original_payload = build_sales_daily_hermes_payload(session, ORG_ID, business_date)
        outbox = HermesReportOutbox(
            report_type="sales_daily",
            organization_id=ORG_ID,
            business_date=business_date,
            source_checksum=original_payload.source_checksum,
            idempotency_key=original_payload.idempotency_key,
            payload_json=original_payload.payload_json,
            payload_markdown=original_payload.payload_markdown.replace(
                "Dos Amigos — итоги", "Dos Amigos — оперативные итоги", 1
            ),
            delivery_status="failed",
            delivery_attempts=1,
            last_attempt_at=datetime(2026, 7, 19, 18, 31, tzinfo=UTC),
            updated_at=datetime(2026, 7, 19, 18, 31, tzinfo=UTC),
        )
        session.add(outbox)
        session.flush()
        session.add_all(
            [
                HermesReportRecipientDelivery(
                    report_id=outbox.id,
                    recipient_key="a" * 64,
                    delivery_status="delivered",
                    delivery_attempts=1,
                    delivered_at=datetime(2026, 7, 19, 18, 31, tzinfo=UTC),
                    last_attempt_at=datetime(2026, 7, 19, 18, 31, tzinfo=UTC),
                    updated_at=datetime(2026, 7, 19, 18, 31, tzinfo=UTC),
                ),
                HermesReportRecipientDelivery(
                    report_id=outbox.id,
                    recipient_key="b" * 64,
                    delivery_status="failed",
                    delivery_attempts=1,
                    last_attempt_at=datetime(2026, 7, 19, 18, 31, tzinfo=UTC),
                    updated_at=datetime(2026, 7, 19, 18, 31, tzinfo=UTC),
                ),
            ]
        )
        session.commit()
        original_id = outbox.id
        original_checksum = outbox.source_checksum
        original_json = dict(outbox.payload_json)
        original_markdown = outbox.payload_markdown

        result = await orchestrator(
            session,
            client=FakeSalesClient(net_sales=Decimal("32955.25")),
        ).closeout(business_date=business_date)

        assert result.status == "outbox_ready"
        assert result.outbox_ids == [original_id]
        assert session.scalar(select(func.count()).select_from(HermesReportOutbox)) == 1
        frozen = session.get(HermesReportOutbox, original_id)
        assert frozen is not None
        assert frozen.source_checksum == original_checksum
        assert frozen.payload_json == original_json
        assert frozen.payload_markdown == original_markdown
        assert "31 955,25 ₽" in frozen.payload_markdown
        assert "32 955,25 ₽" not in frozen.payload_markdown

        reconciliation = await orchestrator(
            session,
            client=FakeSalesClient(net_sales=Decimal("32955.25")),
        ).reconcile(business_date=business_date)

        assert reconciliation.status == "correction_created"
        correction = session.scalar(
            select(HermesReportOutbox).where(
                HermesReportOutbox.report_type == "sales_daily_correction"
            )
        )
        assert correction is not None
        assert "31 955,25 ₽" in correction.payload_markdown
        assert "32 955,25 ₽" in correction.payload_markdown


async def test_closeout_does_not_rewrite_report_after_pending_recipient_registration() -> None:
    SessionLocal = session_factory()
    with SessionLocal() as session:
        business_date = date(2026, 7, 19)
        persist_day(session, business_date, net_sales=Decimal("31955.25"))
        original_payload = build_sales_daily_hermes_payload(session, ORG_ID, business_date)
        outbox = HermesReportOutbox(
            report_type="sales_daily",
            organization_id=ORG_ID,
            business_date=business_date,
            source_checksum=original_payload.source_checksum,
            idempotency_key=original_payload.idempotency_key,
            payload_json=original_payload.payload_json,
            payload_markdown=original_payload.payload_markdown,
            delivery_status="pending",
            delivery_attempts=0,
            updated_at=datetime(2026, 7, 19, 18, 31, tzinfo=UTC),
        )
        session.add(outbox)
        session.flush()
        session.add(
            HermesReportRecipientDelivery(
                report_id=outbox.id,
                recipient_key="a" * 64,
                delivery_status="pending",
                delivery_attempts=0,
                updated_at=datetime(2026, 7, 19, 18, 31, tzinfo=UTC),
            )
        )
        session.commit()
        original_id = outbox.id
        original_checksum = outbox.source_checksum
        original_json = dict(outbox.payload_json)
        original_markdown = outbox.payload_markdown

        result = await orchestrator(
            session,
            client=FakeSalesClient(net_sales=Decimal("32955.25")),
        ).closeout(business_date=business_date)

        assert result.outbox_ids == [original_id]
        assert session.scalar(select(func.count()).select_from(HermesReportOutbox)) == 1
        frozen = session.get(HermesReportOutbox, original_id)
        assert frozen is not None
        assert frozen.source_checksum == original_checksum
        assert frozen.payload_json == original_json
        assert frozen.payload_markdown == original_markdown


async def test_reconcile_changed_data_creates_one_correction_and_keeps_delivered_report() -> None:
    SessionLocal = session_factory()
    with SessionLocal() as session:
        business_date = date(2026, 7, 18)
        persist_day(session, business_date, net_sales=Decimal("31955.25"))
        original_payload = build_sales_daily_hermes_payload(session, ORG_ID, business_date)
        delivered = HermesReportOutbox(
            report_type="sales_daily",
            organization_id=ORG_ID,
            business_date=business_date,
            source_checksum=original_payload.source_checksum,
            idempotency_key=original_payload.idempotency_key,
            payload_json=original_payload.payload_json,
            payload_markdown=original_payload.payload_markdown,
            delivery_status="delivered",
            delivery_attempts=1,
            delivered_at=datetime(2026, 7, 18, 23, 31, tzinfo=UTC),
            updated_at=datetime(2026, 7, 18, 23, 31, tzinfo=UTC),
        )
        session.add(delivered)
        session.commit()
        delivered_id = delivered.id

        service = orchestrator(
            session,
            client=FakeSalesClient(net_sales=Decimal("32955.25")),
        )
        first = await service.reconcile(now=datetime(2026, 7, 19, 1, 0, tzinfo=UTC))
        second = await service.reconcile(now=datetime(2026, 7, 19, 1, 5, tzinfo=UTC))

        rows = list(session.scalars(select(HermesReportOutbox).order_by(HermesReportOutbox.id)))
        assert first.status == "correction_created"
        assert second.status == "unchanged"
        assert len(rows) == 2
        assert (
            session.get(HermesReportOutbox, delivered_id).payload_markdown
            == delivered.payload_markdown
        )
        correction = next(row for row in rows if row.report_type == "sales_daily_correction")
        assert correction.payload_markdown.startswith(
            "Dos Amigos — корректировка итогов 18.07.2026"
        )
        assert "Выручка" in correction.payload_markdown
        assert "32 955,25 ₽" in correction.payload_markdown
        assert "{" not in correction.payload_markdown
        assert "[" not in correction.payload_markdown


def test_daily_coo_report_uses_shared_sales_automation_lock_key() -> None:
    SessionLocal = session_factory()
    with SessionLocal() as session:
        service = DailyCooReportOrchestrator(
            session=session,
            client=FakeSalesClient(),
            config=config(),
        )

        assert service.lock.key == SALES_AUTOMATION_LOCK_KEY


async def test_reconcile_exception_creates_alert_and_closes_run() -> None:
    SessionLocal = session_factory()
    with SessionLocal() as session:
        business_date = date(2026, 7, 18)
        persist_day(session, business_date, net_sales=Decimal("31955.25"))
        original_payload = build_sales_daily_hermes_payload(session, ORG_ID, business_date)
        session.add(
            HermesReportOutbox(
                report_type="sales_daily",
                organization_id=ORG_ID,
                business_date=business_date,
                source_checksum=original_payload.source_checksum,
                idempotency_key=original_payload.idempotency_key,
                payload_json=original_payload.payload_json,
                payload_markdown=original_payload.payload_markdown,
                delivery_status="failed",
                delivery_attempts=1,
                updated_at=datetime(2026, 7, 18, 23, 31, tzinfo=UTC),
            )
        )
        session.commit()

        result = await orchestrator(session, client=ExplodingSalesClient()).reconcile(
            business_date=business_date,
        )

        assert result.status == "failed"
        assert result.error_code == "iiko_sync_exception"
        alert = session.scalar(
            select(HermesReportOutbox).where(HermesReportOutbox.report_type == "sales_daily_alert")
        )
        assert alert is not None
        assert result.outbox_ids == [alert.id]
        assert "postgresql://" not in alert.payload_markdown
        assert "password" not in alert.payload_markdown
        assert "token=secret" not in alert.payload_markdown
        assert "100100100" not in alert.payload_markdown
        run = session.scalars(select(DailyCooReportRun)).one()
        assert run.status == "failed"
        assert run.finished_at is not None
        assert run.outbox_id == alert.id
