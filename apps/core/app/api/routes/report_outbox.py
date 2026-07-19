from __future__ import annotations

import re
import secrets
from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.core.app.core.config import get_settings
from apps.core.app.db.session import get_db
from apps.core.app.models.sales import HermesReportOutbox, HermesReportRecipientDelivery

router = APIRouter()

_AUTH_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_TOKEN_ASSIGNMENT_RE = re.compile(r"\b(token|bot_token|authorization)=\S+", re.IGNORECASE)
_NUMERIC_ID_RE = re.compile(r"\b\d+\b")
_SAFE_ERROR_LIMIT = 500
_RECIPIENT_KEY_RE = re.compile(r"^[0-9a-f]{64}$")


class PendingReportResponse(BaseModel):
    id: str
    business_date: date
    payload_markdown: str
    delivery_attempts: int


class ReportStatusResponse(BaseModel):
    id: str
    delivery_status: str
    delivery_attempts: int


class FailedReportRequest(BaseModel):
    error_code: str | None = Field(default=None, max_length=120)
    error_message: str | None = Field(default=None, max_length=2000)


class RegisterRecipientsRequest(BaseModel):
    recipient_keys: list[str] = Field(min_length=1, max_length=100)

    @field_validator("recipient_keys")
    @classmethod
    def validate_recipient_keys(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        deduped: list[str] = []
        for recipient_key in value:
            if not _RECIPIENT_KEY_RE.fullmatch(recipient_key):
                raise ValueError("recipient_key must be 64 lowercase hex characters")
            if recipient_key not in seen:
                seen.add(recipient_key)
                deduped.append(recipient_key)
        return deduped


class RegisterRecipientsResponse(BaseModel):
    recipients: dict[str, str]


def require_internal_token(
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    token = get_settings().report_outbox_internal_token
    if not token:
        raise HTTPException(status_code=503, detail="internal API not configured")
    expected = f"Bearer {token}"
    if authorization is None or not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="invalid internal token")


def _get_outbox_or_404(db: Session, report_id: str) -> HermesReportOutbox:
    outbox = db.get(HermesReportOutbox, report_id)
    if outbox is None:
        raise HTTPException(status_code=404, detail="report not found")
    return outbox


def _get_recipient_or_404(
    db: Session,
    report_id: str,
    recipient_key: str,
) -> HermesReportRecipientDelivery:
    delivery = db.scalar(
        select(HermesReportRecipientDelivery)
        .where(HermesReportRecipientDelivery.report_id == report_id)
        .where(HermesReportRecipientDelivery.recipient_key == recipient_key)
    )
    if delivery is None:
        raise HTTPException(status_code=404, detail="recipient delivery not found")
    return delivery


def _safe_error_message(message: str | None) -> str | None:
    if not message:
        return None
    redacted = _AUTH_RE.sub("[redacted_auth]", message)
    redacted = _TOKEN_ASSIGNMENT_RE.sub("[redacted_secret]", redacted)
    redacted = _NUMERIC_ID_RE.sub("[redacted_id]", redacted)
    return redacted[:_SAFE_ERROR_LIMIT]


def _status_response(outbox: HermesReportOutbox) -> ReportStatusResponse:
    return ReportStatusResponse(
        id=outbox.id,
        delivery_status=outbox.delivery_status,
        delivery_attempts=outbox.delivery_attempts,
    )


@router.get(
    "/pending",
    response_model=list[PendingReportResponse],
    dependencies=[Depends(require_internal_token)],
)
def pending_reports(
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    business_date: date | None = None,
    include_failed: bool = False,
) -> list[PendingReportResponse]:
    statuses = ["pending"]
    if include_failed:
        statuses.append("failed")
    statement = (
        select(HermesReportOutbox)
        .where(HermesReportOutbox.delivery_status.in_(statuses))
        .order_by(HermesReportOutbox.created_at, HermesReportOutbox.id)
        .limit(limit)
    )
    if business_date is not None:
        statement = statement.where(HermesReportOutbox.business_date == business_date)
    return [
        PendingReportResponse(
            id=row.id,
            business_date=row.business_date,
            payload_markdown=row.payload_markdown,
            delivery_attempts=row.delivery_attempts,
        )
        for row in db.scalars(statement)
    ]


@router.post(
    "/{report_id}/delivered",
    response_model=ReportStatusResponse,
    dependencies=[Depends(require_internal_token)],
)
def mark_report_delivered(
    report_id: str,
    db: Annotated[Session, Depends(get_db)],
) -> ReportStatusResponse:
    outbox = _get_outbox_or_404(db, report_id)
    now = datetime.now(UTC)
    if outbox.delivery_status != "delivered":
        outbox.delivery_status = "delivered"
        outbox.delivered_at = now
        outbox.error_code = None
        outbox.error_message_redacted = None
    elif outbox.delivered_at is None:
        outbox.delivered_at = now
    outbox.updated_at = now
    db.commit()
    db.refresh(outbox)
    return _status_response(outbox)


@router.post(
    "/{report_id}/failed",
    response_model=ReportStatusResponse,
    dependencies=[Depends(require_internal_token)],
)
def mark_report_failed(
    report_id: str,
    request: FailedReportRequest,
    db: Annotated[Session, Depends(get_db)],
) -> ReportStatusResponse:
    outbox = _get_outbox_or_404(db, report_id)
    now = datetime.now(UTC)
    if outbox.delivery_status != "delivered":
        outbox.delivery_status = "failed"
        outbox.delivery_attempts += 1
        outbox.last_attempt_at = now
        outbox.error_code = request.error_code or "telegram_delivery_failed"
        outbox.error_message_redacted = _safe_error_message(request.error_message)
    outbox.updated_at = now
    db.commit()
    db.refresh(outbox)
    return _status_response(outbox)


@router.post(
    "/{report_id}/recipients",
    response_model=RegisterRecipientsResponse,
    dependencies=[Depends(require_internal_token)],
)
def register_report_recipients(
    report_id: str,
    request: RegisterRecipientsRequest,
    db: Annotated[Session, Depends(get_db)],
) -> RegisterRecipientsResponse:
    _get_outbox_or_404(db, report_id)
    now = datetime.now(UTC)
    statuses: dict[str, str] = {}
    for recipient_key in request.recipient_keys:
        delivery = db.scalar(
            select(HermesReportRecipientDelivery)
            .where(HermesReportRecipientDelivery.report_id == report_id)
            .where(HermesReportRecipientDelivery.recipient_key == recipient_key)
        )
        if delivery is None:
            delivery = HermesReportRecipientDelivery(
                report_id=report_id,
                recipient_key=recipient_key,
                delivery_status="pending",
                delivery_attempts=0,
                updated_at=now,
            )
            db.add(delivery)
            db.flush()
        statuses[recipient_key] = delivery.delivery_status
    db.commit()
    return RegisterRecipientsResponse(recipients=statuses)


@router.post(
    "/{report_id}/recipients/{recipient_key}/delivered",
    response_model=ReportStatusResponse,
    dependencies=[Depends(require_internal_token)],
)
def mark_report_recipient_delivered(
    report_id: str,
    recipient_key: Annotated[str, Path(pattern=r"^[0-9a-f]{64}$")],
    db: Annotated[Session, Depends(get_db)],
) -> ReportStatusResponse:
    outbox = _get_outbox_or_404(db, report_id)
    delivery = _get_recipient_or_404(db, report_id, recipient_key)
    now = datetime.now(UTC)
    if delivery.delivery_status != "delivered":
        delivery.delivery_status = "delivered"
        delivery.delivery_attempts += 1
        delivery.delivered_at = now
        delivery.last_attempt_at = now
        delivery.error_code = None
        delivery.error_message_redacted = None
    delivery.updated_at = now
    db.commit()
    db.refresh(outbox)
    return _status_response(outbox)


@router.post(
    "/{report_id}/recipients/{recipient_key}/failed",
    response_model=ReportStatusResponse,
    dependencies=[Depends(require_internal_token)],
)
def mark_report_recipient_failed(
    report_id: str,
    recipient_key: Annotated[str, Path(pattern=r"^[0-9a-f]{64}$")],
    request: FailedReportRequest,
    db: Annotated[Session, Depends(get_db)],
) -> ReportStatusResponse:
    outbox = _get_outbox_or_404(db, report_id)
    delivery = _get_recipient_or_404(db, report_id, recipient_key)
    now = datetime.now(UTC)
    if delivery.delivery_status != "delivered":
        delivery.delivery_status = "failed"
        delivery.delivery_attempts += 1
        delivery.last_attempt_at = now
        delivery.error_code = request.error_code or "telegram_delivery_failed"
        delivery.error_message_redacted = _safe_error_message(request.error_message)
    delivery.updated_at = now
    db.commit()
    db.refresh(outbox)
    return _status_response(outbox)
