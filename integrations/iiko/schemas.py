from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Self


class IikoMode(StrEnum):
    SERVER_REST_API = "server_rest_api"
    CLOUD_API = "cloud_api"
    EXPORT_BRIDGE = "export_bridge"
    MOCK = "mock"


class ResultStatus(StrEnum):
    PROVEN = "proven"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class AuthKind(StrEnum):
    UNKNOWN = "unknown"
    NONE = "none"
    USER_PASSWORD = "user_password"
    API_LOGIN = "api_login"
    BEARER_TOKEN = "bearer_token"


@dataclass(frozen=True)
class AuthResult:
    status: ResultStatus
    authenticated: bool
    adapter: str
    mode: IikoMode
    organization_ref: str | None
    trace_id: str
    details: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    error_message_sanitized: str | None = None

    @property
    def organization_id(self) -> str | None:
        """Backward-compatible alias."""
        return self.organization_ref


@dataclass(frozen=True)
class ProbeResult:
    status: ResultStatus
    adapter: str
    mode: IikoMode
    trace_id: str
    endpoint_reachable: bool | None
    authenticated: bool | None
    details: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    error_message_sanitized: str | None = None


@dataclass(frozen=True)
class RawResult:
    status: ResultStatus
    adapter: str
    mode: IikoMode
    dataset: str
    trace_id: str
    payload: Any = None
    records_count: int = 0
    details: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    error_message_sanitized: str | None = None

    @classmethod
    def proven(
        cls,
        *,
        adapter: str,
        mode: IikoMode,
        dataset: str,
        trace_id: str,
        payload: Any,
        records_count: int,
        details: dict[str, Any] | None = None,
    ) -> Self:
        return cls(
            status=ResultStatus.PROVEN,
            adapter=adapter,
            mode=mode,
            dataset=dataset,
            trace_id=trace_id,
            payload=payload,
            records_count=records_count,
            details=details or {},
        )

    @classmethod
    def blocked(
        cls,
        *,
        adapter: str,
        mode: IikoMode,
        dataset: str,
        trace_id: str,
        reason: str,
    ) -> Self:
        return cls(
            status=ResultStatus.BLOCKED,
            adapter=adapter,
            mode=mode,
            dataset=dataset,
            trace_id=trace_id,
            error_code="capability_blocked",
            error_message_sanitized=reason,
            details={"writes_enabled": False},
        )

    @classmethod
    def unknown(
        cls,
        *,
        adapter: str,
        mode: IikoMode,
        dataset: str,
        trace_id: str,
        reason: str,
    ) -> Self:
        return cls(
            status=ResultStatus.UNKNOWN,
            adapter=adapter,
            mode=mode,
            dataset=dataset,
            trace_id=trace_id,
            error_code="capability_unknown",
            error_message_sanitized=reason,
            details={"writes_enabled": False},
        )
