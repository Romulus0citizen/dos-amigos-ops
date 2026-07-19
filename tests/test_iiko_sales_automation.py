from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, date, datetime
from typing import Any

from fastapi.testclient import TestClient
from fixtures_iiko_sales import BUSINESS_DATE, ORG_ID, copied_payload
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from apps.core.app.db.session import get_db
from apps.core.app.main import app
from apps.core.app.models.sales import (
    HermesReportOutbox,
    IikoSalesAutomationRun,
    IikoSalesDaily,
    IikoSalesDailyPayment,
    IikoSalesDailyProduct,
    IikoSalesSyncRun,
)
from apps.core.app.repositories.iiko_sales import IikoSalesRepository
from apps.core.app.services.iiko_sales_automation import (
    DisabledHermesReportPublisher,
    FakeSalesAutomationLock,
    IikoSalesAutomationService,
    MockHermesReportPublisher,
    SalesAutomationConfig,
    build_sales_daily_hermes_payload,
)
from apps.core.app.services.publish_hermes_reports import HermesOutboxPublishService
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
    ):
        table.create(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def payload_for_day(business_date: date) -> dict[str, Any]:
    payload = copied_payload()
    for report in payload.values():
        for row in report["data"]:
            row["OpenDate.Typed"] = business_date.isoformat()
    return payload


def persist_sales_day(session: Session, business_date: date, *, checksum_suffix: str = "") -> None:
    report = normalize_iiko_sales_payload(
        payload_for_day(business_date),
        organization_id=ORG_ID,
        business_date=business_date,
    )
    if checksum_suffix:
        report.daily.source_checksum = f"{report.daily.source_checksum[:-4]}{checksum_suffix}"
    IikoSalesRepository(session).persist_report(
        report,
        started_at=datetime(2026, 7, 18, tzinfo=UTC),
        finished_at=datetime(2026, 7, 18, 0, 1, tzinfo=UTC),
    )


class FakeSalesClient(IikoClient):
    adapter_name = "fake"
    mode = IikoMode.MOCK

    def __init__(self) -> None:
        self.requested_dates: list[date] = []
        self.fail_dates: set[date] = set()

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
        if business_date in self.fail_dates:
            return RawResult(
                status=ResultStatus.UNKNOWN,
                adapter=self.adapter_name,
                mode=self.mode,
                dataset="orders_or_sales",
                trace_id="trace-failed",
                payload=None,
                records_count=0,
                details={"writes_enabled": False},
                error_code="endpoint_unreachable",
                error_message_sanitized="temporary read failure",
            )
        return RawResult.proven(
            adapter=self.adapter_name,
            mode=self.mode,
            dataset="orders_or_sales",
            trace_id="trace-1",
            payload=payload_for_day(business_date),
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


def automation_service(
    session: Session,
    client: FakeSalesClient | None = None,
    *,
    lock: FakeSalesAutomationLock | None = None,
    enabled: bool = True,
    backfill_days: int = 3,
) -> IikoSalesAutomationService:
    return IikoSalesAutomationService(
        session=session,
        client=client or FakeSalesClient(),
        config=SalesAutomationConfig(
            organization_id=ORG_ID,
            business_timezone="Etc/UTC",
            automation_enabled=enabled,
            scheduled_local_time="06:00",
            backfill_max_days=backfill_days,
            retry_max_attempts=1,
            retry_base_seconds=0,
            outbox_enabled=True,
            hermes_delivery_mode="disabled",
        ),
        lock=lock or FakeSalesAutomationLock(),
    )


async def test_run_due_before_scheduled_time_is_not_due() -> None:
    SessionLocal = session_factory()
    with SessionLocal() as session:
        client = FakeSalesClient()
        service = automation_service(session, client)

        result = await service.run_due(now=datetime(2026, 7, 17, 5, 59, tzinfo=UTC))

        assert result.status == "not_due"
        assert client.requested_dates == []
        assert session.scalar(select(func.count()).select_from(IikoSalesAutomationRun)) == 1


async def test_run_due_after_scheduled_time_imports_previous_day_and_creates_outbox() -> None:
    SessionLocal = session_factory()
    with SessionLocal() as session:
        client = FakeSalesClient()
        service = automation_service(session, client, backfill_days=1)

        result = await service.run_due(now=datetime(2026, 7, 17, 6, 1, tzinfo=UTC))

        assert result.status == "partial"
        assert client.requested_dates == [date(2026, 7, 16)]
        assert session.get(
            IikoSalesDaily, {"organization_id": ORG_ID, "business_date": date(2026, 7, 16)}
        )
        outbox = session.scalars(select(HermesReportOutbox)).one()
        assert outbox.delivery_status == "pending"
        assert outbox.idempotency_key.startswith(f"sales_daily:{ORG_ID}:2026-07-16:")


async def test_run_due_after_success_today_is_already_completed() -> None:
    SessionLocal = session_factory()
    with SessionLocal() as session:
        service = automation_service(session, backfill_days=1)
        first = await service.run_due(now=datetime(2026, 7, 17, 6, 1, tzinfo=UTC))
        second = await service.run_due(now=datetime(2026, 7, 17, 7, 1, tzinfo=UTC))

        assert first.status == "partial"
        assert second.status == "already_completed"


async def test_backfill_processes_missing_days_oldest_first_and_does_not_repeat_partial() -> None:
    SessionLocal = session_factory()
    with SessionLocal() as session:
        persist_sales_day(session, date(2026, 7, 15))
        session.commit()
        client = FakeSalesClient()
        service = automation_service(session, client, backfill_days=3)

        result = await service.run_backfill(
            target_date=date(2026, 7, 16),
            backfill_days=3,
            retry_partial=False,
        )

        assert result.days_considered == 3
        assert client.requested_dates == [date(2026, 7, 14), date(2026, 7, 16)]
        assert result.days_processed == 2


async def test_retry_partial_explicitly_reprocesses_partial_day() -> None:
    SessionLocal = session_factory()
    with SessionLocal() as session:
        persist_sales_day(session, date.fromisoformat(BUSINESS_DATE))
        session.commit()
        client = FakeSalesClient()
        service = automation_service(session, client, backfill_days=1)

        await service.run_backfill(
            target_date=date.fromisoformat(BUSINESS_DATE),
            backfill_days=1,
            retry_partial=True,
        )

        assert client.requested_dates == [date.fromisoformat(BUSINESS_DATE)]


async def test_lock_busy_returns_already_running_and_does_not_import() -> None:
    SessionLocal = session_factory()
    with SessionLocal() as session:
        client = FakeSalesClient()
        lock = FakeSalesAutomationLock(acquired=False)
        service = automation_service(session, client, lock=lock)

        result = await service.run_date(date.fromisoformat(BUSINESS_DATE))

        assert result.status == "already_running"
        assert client.requested_dates == []
        assert lock.release_calls == 0


async def test_lock_released_after_exception() -> None:
    SessionLocal = session_factory()
    with SessionLocal() as session:
        client = FakeSalesClient()
        client.fail_dates.add(date.fromisoformat(BUSINESS_DATE))
        lock = FakeSalesAutomationLock()
        service = automation_service(session, client, lock=lock)

        result = await service.run_date(date.fromisoformat(BUSINESS_DATE))

        assert result.status == "failed"
        assert lock.release_calls == 1


def test_outbox_idempotency_supersedes_pending_but_keeps_delivered() -> None:
    SessionLocal = session_factory()
    with SessionLocal() as session:
        persist_sales_day(session, date.fromisoformat(BUSINESS_DATE))
        session.commit()
        service = automation_service(session)

        first = service.ensure_outbox(date.fromisoformat(BUSINESS_DATE))
        second = service.ensure_outbox(date.fromisoformat(BUSINESS_DATE))
        first_record = session.get(HermesReportOutbox, first.outbox_ids[0])
        first_record.delivery_status = "delivered"
        daily = session.get(
            IikoSalesDaily,
            {"organization_id": ORG_ID, "business_date": date.fromisoformat(BUSINESS_DATE)},
        )
        daily.source_checksum = "f" * 64
        session.commit()
        third = service.ensure_outbox(date.fromisoformat(BUSINESS_DATE))

        assert first.outbox_created == 1
        assert second.outbox_created == 0
        assert third.outbox_created == 1
        statuses = sorted(
            row.delivery_status for row in session.scalars(select(HermesReportOutbox))
        )
        assert statuses == ["delivered", "pending"]


def test_rebuild_outbox_updates_existing_payload_without_duplicate() -> None:
    SessionLocal = session_factory()
    with SessionLocal() as session:
        business_date = date.fromisoformat(BUSINESS_DATE)
        persist_sales_day(session, business_date)
        daily = session.get(
            IikoSalesDaily,
            {"organization_id": ORG_ID, "business_date": business_date},
        )
        assert daily is not None
        outbox = HermesReportOutbox(
            report_type="sales_daily",
            organization_id=ORG_ID,
            business_date=business_date,
            source_checksum=daily.source_checksum,
            idempotency_key=f"sales_daily:{ORG_ID}:{BUSINESS_DATE}:{daily.source_checksum}",
            payload_json={"schema_version": "old"},
            payload_markdown="old payload",
            delivery_status="pending",
            delivery_attempts=0,
            updated_at=datetime(2026, 7, 18, tzinfo=UTC),
        )
        session.add(outbox)
        session.commit()
        outbox_id = outbox.id
        service = automation_service(session)

        result = service.rebuild_outbox()

        rows = list(session.scalars(select(HermesReportOutbox)))
        assert result.outbox_created == 0
        assert result.outbox_ids == [outbox_id]
        assert len(rows) == 1
        assert rows[0].id == outbox_id
        assert rows[0].payload_markdown != "old payload"
        assert rows[0].payload_markdown.startswith("Dos Amigos — итоги")


def test_hermes_payload_is_deterministic_decimal_string_and_safe() -> None:
    SessionLocal = session_factory()
    with SessionLocal() as session:
        persist_sales_day(session, date.fromisoformat(BUSINESS_DATE))
        session.commit()

        first = build_sales_daily_hermes_payload(session, ORG_ID, date.fromisoformat(BUSINESS_DATE))
        second = build_sales_daily_hermes_payload(
            session, ORG_ID, date.fromisoformat(BUSINESS_DATE)
        )

        assert first.payload_json == second.payload_json
        assert first.payload_markdown == second.payload_markdown
        assert first.payload_json["net_sales"] == "31955.25"
        assert isinstance(first.payload_json["payments"][0]["amount"], str)
        assert len(first.payload_json["top_products"]) == 2
        assert "token" not in repr(first).lower()


async def test_rebuild_outbox_restores_missing_report_without_iiko() -> None:
    SessionLocal = session_factory()
    with SessionLocal() as session:
        persist_sales_day(session, date.fromisoformat(BUSINESS_DATE))
        session.commit()
        client = FakeSalesClient()
        service = automation_service(session, client)

        result = service.rebuild_outbox()

        assert result.outbox_created == 1
        assert client.requested_dates == []


async def test_disabled_publisher_leaves_pending_outbox() -> None:
    SessionLocal = session_factory()
    with SessionLocal() as session:
        persist_sales_day(session, date.fromisoformat(BUSINESS_DATE))
        session.commit()
        service = automation_service(session)
        service.ensure_outbox(date.fromisoformat(BUSINESS_DATE))

        result = await HermesOutboxPublishService(
            session=session,
            publisher=DisabledHermesReportPublisher(),
        ).publish_pending()

        outbox = session.scalars(select(HermesReportOutbox)).one()
        assert result.status == "disabled"
        assert outbox.delivery_status == "pending"


async def test_mock_publisher_marks_outbox_delivered() -> None:
    SessionLocal = session_factory()
    with SessionLocal() as session:
        persist_sales_day(session, date.fromisoformat(BUSINESS_DATE))
        session.commit()
        service = automation_service(session)
        service.ensure_outbox(date.fromisoformat(BUSINESS_DATE))

        result = await HermesOutboxPublishService(
            session=session,
            publisher=MockHermesReportPublisher(),
        ).publish_pending()

        outbox = session.scalars(select(HermesReportOutbox)).one()
        assert result.status == "delivered"
        assert outbox.delivery_status == "delivered"
        assert outbox.external_message_id.startswith("mock-sales_daily-")


def test_sales_automation_status_endpoint_reports_counts() -> None:
    SessionLocal = session_factory()
    with SessionLocal() as setup_session:
        persist_sales_day(setup_session, date.fromisoformat(BUSINESS_DATE))
        setup_session.commit()
        automation_service(setup_session).ensure_outbox(date.fromisoformat(BUSINESS_DATE))

    def override_db() -> Generator[Session, None, None]:
        with SessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    try:
        response = TestClient(app).get("/api/v1/operations/sales-automation/status")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["pending_outbox_count"] == 1
    assert body["failed_outbox_count"] == 0
    assert body["current_lock_held"] is False
