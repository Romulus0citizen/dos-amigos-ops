from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, date, datetime

from fastapi.testclient import TestClient
from fixtures_iiko_sales import BUSINESS_DATE, ORG_ID, copied_payload
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from apps.core.app.db.session import get_db
from apps.core.app.main import app
from apps.core.app.models.sales import (
    IikoSalesDaily,
    IikoSalesDailyPayment,
    IikoSalesDailyProduct,
    IikoSalesSyncRun,
)
from apps.core.app.repositories.iiko_sales import IikoSalesRepository
from apps.core.app.services.iiko_sales import IikoSalesSyncService
from integrations.iiko.client import IikoClient
from integrations.iiko.sales import normalize_iiko_sales_payload
from integrations.iiko.schemas import AuthResult, IikoMode, ProbeResult, RawResult


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
    ):
        table.create(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def normalized_report(payload: dict | None = None):
    return normalize_iiko_sales_payload(
        payload or copied_payload(),
        organization_id=ORG_ID,
        business_date=date.fromisoformat(BUSINESS_DATE),
    )


def persist_report(session: Session, payload: dict | None = None):
    repository = IikoSalesRepository(session)
    return repository.persist_report(
        normalized_report(payload),
        started_at=datetime(2026, 7, 18, tzinfo=UTC),
        finished_at=datetime(2026, 7, 18, 0, 1, tzinfo=UTC),
    )


def test_first_import_persists_daily_payments_products_and_sync_run() -> None:
    SessionLocal = session_factory()
    with SessionLocal() as session:
        result = persist_report(session)
        session.commit()

        assert result.persisted_rows == 5
        assert session.scalar(select(func.count()).select_from(IikoSalesDaily)) == 1
        assert session.scalar(select(func.count()).select_from(IikoSalesDailyPayment)) == 2
        assert session.scalar(select(func.count()).select_from(IikoSalesDailyProduct)) == 2
        assert session.scalar(select(func.count()).select_from(IikoSalesSyncRun)) == 1


def test_repeated_identical_import_is_unchanged_and_creates_no_duplicates() -> None:
    SessionLocal = session_factory()
    with SessionLocal() as session:
        persist_report(session)
        session.commit()

        result = persist_report(session)
        session.commit()

        assert result.unchanged is True
        assert result.persisted_rows == 0
        assert session.scalar(select(func.count()).select_from(IikoSalesDaily)) == 1
        assert session.scalar(select(func.count()).select_from(IikoSalesDailyPayment)) == 2
        assert session.scalar(select(func.count()).select_from(IikoSalesDailyProduct)) == 2
        assert session.scalar(select(func.count()).select_from(IikoSalesSyncRun)) == 2


def test_reimport_changed_values_replaces_previous_day() -> None:
    SessionLocal = session_factory()
    with SessionLocal() as session:
        persist_report(session)
        session.commit()

        changed = copied_payload()
        changed["daily"]["data"][0]["DishDiscountSumInt"] += 1
        changed["payments"]["data"][1]["DishDiscountSumInt"] += 1
        changed["products"]["data"][1]["DishDiscountSumInt"] += 1
        result = persist_report(session, changed)
        session.commit()

        daily = session.get(
            IikoSalesDaily,
            {"organization_id": ORG_ID, "business_date": date.fromisoformat(BUSINESS_DATE)},
        )
        assert result.unchanged is False
        assert daily is not None
        assert str(daily.net_sales) == "31956.25"
        assert session.scalar(select(func.count()).select_from(IikoSalesDailyPayment)) == 2


def test_transaction_rollback_preserves_previous_successful_data() -> None:
    SessionLocal = session_factory()
    with SessionLocal() as session:
        persist_report(session)
        session.commit()
        checksum = session.get(
            IikoSalesDaily,
            {"organization_id": ORG_ID, "business_date": date.fromisoformat(BUSINESS_DATE)},
        ).source_checksum

        changed = copied_payload()
        changed["daily"]["data"][0]["DishDiscountSumInt"] += 1
        changed["payments"]["data"][1]["DishDiscountSumInt"] += 1
        changed["products"]["data"][1]["DishDiscountSumInt"] += 1
        try:
            persist_report(session, changed)
            raise RuntimeError("force rollback")
        except RuntimeError:
            session.rollback()

        daily = session.get(
            IikoSalesDaily,
            {"organization_id": ORG_ID, "business_date": date.fromisoformat(BUSINESS_DATE)},
        )
        assert daily.source_checksum == checksum


class FakeSalesClient(IikoClient):
    adapter_name = "fake"
    mode = IikoMode.MOCK

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
        payload = copied_payload()
        return RawResult.proven(
            adapter=self.adapter_name,
            mode=self.mode,
            dataset="orders_or_sales",
            trace_id="trace-1",
            payload=payload,
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


async def test_service_dry_run_reads_and_validates_but_writes_nothing() -> None:
    SessionLocal = session_factory()
    with SessionLocal() as session:
        service = IikoSalesSyncService(
            session=session,
            client=FakeSalesClient(),
            organization_id=ORG_ID,
            business_timezone="Etc/UTC",
        )

        result = await service.sync_day(date.fromisoformat(BUSINESS_DATE), dry_run=True)

        assert result.dry_run is True
        assert result.records_persisted == 0
        assert session.scalar(select(func.count()).select_from(IikoSalesDaily)) == 0


def test_service_rejects_ranges_longer_than_31_days() -> None:
    SessionLocal = session_factory()
    with SessionLocal() as session:
        service = IikoSalesSyncService(
            session=session,
            client=FakeSalesClient(),
            organization_id=ORG_ID,
            business_timezone="Etc/UTC",
        )

        try:
            service.validate_date_range(
                date_from=date(2026, 1, 1),
                date_to=date(2026, 2, 1),
            )
        except ValueError as exc:
            assert "31 days" in str(exc)
        else:
            raise AssertionError("expected date range validation failure")


def test_daily_sales_report_returns_decimal_strings_and_sorted_top_products() -> None:
    SessionLocal = session_factory()
    with SessionLocal() as setup_session:
        persist_report(setup_session)
        setup_session.commit()

    def override_db() -> Generator[Session, None, None]:
        with SessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    try:
        response = TestClient(app).get(
            f"/api/v1/reports/sales/daily?date={BUSINESS_DATE}&organization_id={ORG_ID}"
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "partial"
    assert body["gross_sales"] == "34645.00"
    assert body["net_sales"] == "31955.25"
    assert body["payments"] == {
        "cash": "10000.00",
        "card": "21955.25",
        "other": "0",
        "unknown": "0",
    }
    assert [item["product_id"] for item in body["top_products"]] == ["product-1", "product-2"]


def test_daily_sales_report_returns_404_for_missing_day() -> None:
    SessionLocal = session_factory()

    def override_db() -> Generator[Session, None, None]:
        with SessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    try:
        response = TestClient(app).get(
            f"/api/v1/reports/sales/daily?date={BUSINESS_DATE}&organization_id={ORG_ID}"
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
