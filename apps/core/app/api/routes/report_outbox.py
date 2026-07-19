from __future__ import annotations

import re
import secrets
from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.core.app.core.config import get_settings
from apps.core.app.db.session import get_db
from apps.core.app.models.sales import HermesReportOutbox

router = APIRouter()

_AUTH_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_TOKEN_ASSIGNMENT_RE = re.compile(r"\b(token|bot_token|authorization)=\S+", re.IGNORECASE)
_NUMERIC_ID_RE = re.compile(r"\b\d+\b")
_SAFE_ERROR_LIMIT = 500


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


def _safe_error_message(message: str | None) -> str | None:
    if not message:
        return None
    redacted = _AUTH_RE.sub("[redacted_auth]", message)
    redacted = _TOKEN_ASSIGNMENT_RE.sub("[redacted_secret]", redacted)
    redacted = _NUMERIC_ID_RE.sub("[redacted_id]", redacted)
    return redacted[:_SAFE_ERROR_LIMIT]


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
    return ReportStatusResponse(
        id=outbox.id,
        delivery_status=outbox.delivery_status,
        delivery_attempts=outbox.delivery_attempts,
    )


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
    return ReportStatusResponse(
        id=outbox.id,
        delivery_status=outbox.delivery_status,
        delivery_attempts=outbox.delivery_attempts,
    )
