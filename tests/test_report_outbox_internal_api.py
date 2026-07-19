from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, date, datetime

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from apps.core.app.api.routes import report_outbox as report_outbox_routes
from apps.core.app.core.config import Settings
from apps.core.app.db.session import get_db
from apps.core.app.main import app
from apps.core.app.models.sales import HermesReportOutbox

NOW = datetime(2026, 7, 17, tzinfo=UTC)


def session_factory():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    HermesReportOutbox.__table__.create(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def add_outbox(
    session: Session,
    report_id: str,
    business_date: date,
    *,
    status: str = "pending",
    attempts: int = 0,
) -> None:
    session.add(
        HermesReportOutbox(
            id=report_id,
            report_type="sales_daily",
            organization_id="department-1",
            business_date=business_date,
            source_checksum=f"{report_id}-checksum".ljust(64, "0")[:64],
            idempotency_key=f"sales_daily:department-1:{business_date}:{report_id}",
            payload_json={"safe": True},
            payload_markdown=f"Report for {business_date.isoformat()}",
            delivery_status=status,
            delivery_attempts=attempts,
            created_at=NOW,
            updated_at=NOW,
        )
    )


def install_overrides(SessionLocal, monkeypatch, *, token: str = "secret-token") -> None:
    def override_db() -> Generator[Session, None, None]:
        with SessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(
        report_outbox_routes,
        "get_settings",
        lambda: Settings(report_outbox_internal_token=token),
    )


def test_report_outbox_returns_503_when_internal_token_is_not_configured(monkeypatch) -> None:
    SessionLocal = session_factory()
    install_overrides(SessionLocal, monkeypatch, token="")
    try:
        response = TestClient(app).get("/api/v1/internal/report-outbox/pending")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["detail"] == "internal API not configured"


def test_report_outbox_returns_401_when_internal_token_is_missing(monkeypatch) -> None:
    SessionLocal = session_factory()
    install_overrides(SessionLocal, monkeypatch)
    try:
        missing = TestClient(app).get("/api/v1/internal/report-outbox/pending")
    finally:
        app.dependency_overrides.clear()

    assert missing.status_code == 401


def test_report_outbox_returns_401_when_internal_token_is_wrong(monkeypatch) -> None:
    SessionLocal = session_factory()
    install_overrides(SessionLocal, monkeypatch)
    try:
        wrong = TestClient(app).get(
            "/api/v1/internal/report-outbox/pending",
            headers={"Authorization": "Bearer wrong"},
        )
    finally:
        app.dependency_overrides.clear()

    assert wrong.status_code == 401


def test_report_outbox_pending_returns_safe_filtered_rows(monkeypatch) -> None:
    SessionLocal = session_factory()
    with SessionLocal() as session:
        add_outbox(session, "report-1", date(2026, 7, 16), attempts=2)
        add_outbox(session, "report-2", date(2026, 7, 15))
        add_outbox(session, "report-3", date(2026, 7, 16), status="delivered")
        add_outbox(session, "report-4", date(2026, 7, 16), status="failed")
        session.commit()

    install_overrides(SessionLocal, monkeypatch)
    try:
        response = TestClient(app).get(
            "/api/v1/internal/report-outbox/pending?business_date=2026-07-16&limit=10",
            headers={"Authorization": "Bearer secret-token"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": "report-1",
            "business_date": "2026-07-16",
            "payload_markdown": "Report for 2026-07-16",
            "delivery_attempts": 2,
        }
    ]


def test_report_outbox_pending_can_include_failed_but_never_delivered(monkeypatch) -> None:
    SessionLocal = session_factory()
    with SessionLocal() as session:
        add_outbox(session, "report-1", date(2026, 7, 16), status="pending")
        add_outbox(session, "report-2", date(2026, 7, 16), status="failed")
        add_outbox(session, "report-3", date(2026, 7, 16), status="delivered")
        session.commit()

    install_overrides(SessionLocal, monkeypatch)
    try:
        response = TestClient(app).get(
            "/api/v1/internal/report-outbox/pending?include_failed=true",
            headers={"Authorization": "Bearer secret-token"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert [row["id"] for row in response.json()] == ["report-1", "report-2"]


def test_report_outbox_delivered_is_idempotent(monkeypatch) -> None:
    SessionLocal = session_factory()
    with SessionLocal() as session:
        add_outbox(session, "report-1", date(2026, 7, 16))
        session.commit()

    install_overrides(SessionLocal, monkeypatch)
    try:
        client = TestClient(app)
        first = client.post(
            "/api/v1/internal/report-outbox/report-1/delivered",
            headers={"Authorization": "Bearer secret-token"},
        )
        second = client.post(
            "/api/v1/internal/report-outbox/report-1/delivered",
            headers={"Authorization": "Bearer secret-token"},
        )
    finally:
        app.dependency_overrides.clear()

    assert first.status_code == 200
    assert second.status_code == 200
    with SessionLocal() as session:
        row = session.get(HermesReportOutbox, "report-1")
        assert row is not None
        assert row.delivery_status == "delivered"
        assert row.delivered_at is not None
        assert row.delivery_attempts == 0


def test_report_outbox_failed_increments_attempts_and_redacts_error(monkeypatch) -> None:
    SessionLocal = session_factory()
    with SessionLocal() as session:
        add_outbox(session, "report-1", date(2026, 7, 16))
        session.commit()

    install_overrides(SessionLocal, monkeypatch)
    try:
        response = TestClient(app).post(
            "/api/v1/internal/report-outbox/report-1/failed",
            headers={"Authorization": "Bearer secret-token"},
            json={
                "error_code": "telegram_delivery_failed",
                "error_message": "chat 100100100 failed with Bearer abc.def token=secret-token",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    with SessionLocal() as session:
        row = session.scalar(select(HermesReportOutbox))
        assert row is not None
        assert row.delivery_status == "failed"
        assert row.delivery_attempts == 1
        assert row.error_code == "telegram_delivery_failed"
        assert row.error_message_redacted is not None
        assert "100100100" not in row.error_message_redacted
        assert "Bearer" not in row.error_message_redacted
        assert "secret-token" not in row.error_message_redacted
