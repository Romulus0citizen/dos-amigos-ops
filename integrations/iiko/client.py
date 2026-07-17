from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from integrations.iiko.auth import AuthConfiguration
from integrations.iiko.schemas import (
    AuthResult,
    IikoMode,
    ProbeResult,
    RawResult,
    ResultStatus,
)


class IikoClient(ABC):
    adapter_name: str
    mode: IikoMode

    @abstractmethod
    async def authenticate(self) -> AuthResult:
        raise NotImplementedError

    @abstractmethod
    async def probe(self) -> ProbeResult:
        raise NotImplementedError

    @abstractmethod
    async def list_organizations(self) -> RawResult:
        raise NotImplementedError

    @abstractmethod
    async def list_terminal_groups(self, organization_ref: str) -> RawResult:
        raise NotImplementedError

    @abstractmethod
    async def fetch_nomenclature(self, organization_ref: str) -> RawResult:
        raise NotImplementedError

    @abstractmethod
    async def fetch_menu(self, organization_ref: str) -> RawResult:
        raise NotImplementedError

    @abstractmethod
    async def fetch_orders_or_sales(
        self,
        parameters: Mapping[str, Any] | None = None,
    ) -> RawResult:
        raise NotImplementedError

    @abstractmethod
    async def fetch_payments(
        self,
        parameters: Mapping[str, Any] | None = None,
    ) -> RawResult:
        raise NotImplementedError

    @abstractmethod
    async def fetch_inventory(
        self,
        parameters: Mapping[str, Any] | None = None,
    ) -> RawResult:
        raise NotImplementedError

    @abstractmethod
    async def fetch_writeoffs(
        self,
        parameters: Mapping[str, Any] | None = None,
    ) -> RawResult:
        raise NotImplementedError

    @abstractmethod
    async def fetch_costs(
        self,
        parameters: Mapping[str, Any] | None = None,
    ) -> RawResult:
        raise NotImplementedError

    @abstractmethod
    async def fetch_employees_or_shifts(
        self,
        parameters: Mapping[str, Any] | None = None,
    ) -> RawResult:
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        raise NotImplementedError


IikoAdapter = IikoClient


class BlockedIikoClient(IikoClient):
    def __init__(
        self,
        *,
        mode: IikoMode,
        base_url: str = "",
        organization_ref: str | None = None,
        auth_configuration: AuthConfiguration | None = None,
        reason: str = "official_endpoint_or_auth_contract_unconfirmed",
    ) -> None:
        self.mode = mode
        self.adapter_name = mode.value
        self.base_url = base_url
        self.organization_ref = organization_ref
        self.auth_configuration = auth_configuration or AuthConfiguration()
        self.reason = reason

    def _trace_id(self) -> str:
        return str(uuid4())

    def _blocked(self, dataset: str) -> RawResult:
        return RawResult.blocked(
            adapter=self.adapter_name,
            mode=self.mode,
            dataset=dataset,
            trace_id=self._trace_id(),
            reason=self.reason,
        )

    async def authenticate(self) -> AuthResult:
        return AuthResult(
            status=ResultStatus.BLOCKED,
            authenticated=False,
            adapter=self.adapter_name,
            mode=self.mode,
            organization_ref=self.organization_ref,
            trace_id=self._trace_id(),
            details={
                "endpoint_configured": bool(self.base_url),
                "writes_enabled": False,
                **self.auth_configuration.sanitized_metadata(),
            },
            error_code="authentication_blocked",
            error_message_sanitized=self.reason,
        )

    async def probe(self) -> ProbeResult:
        return ProbeResult(
            status=ResultStatus.BLOCKED,
            adapter=self.adapter_name,
            mode=self.mode,
            trace_id=self._trace_id(),
            endpoint_reachable=None,
            authenticated=None,
            details={
                "endpoint_configured": bool(self.base_url),
                "writes_enabled": False,
                **self.auth_configuration.sanitized_metadata(),
            },
            error_code="probe_blocked",
            error_message_sanitized=self.reason,
        )

    async def list_organizations(self) -> RawResult:
        return self._blocked("organizations")

    async def list_terminal_groups(self, organization_ref: str) -> RawResult:
        return self._blocked("terminal_groups")

    async def fetch_nomenclature(self, organization_ref: str) -> RawResult:
        return self._blocked("nomenclature")

    async def fetch_menu(self, organization_ref: str) -> RawResult:
        return self._blocked("menu")

    async def fetch_orders_or_sales(
        self,
        parameters: Mapping[str, Any] | None = None,
    ) -> RawResult:
        return self._blocked("orders_or_sales")

    async def fetch_payments(
        self,
        parameters: Mapping[str, Any] | None = None,
    ) -> RawResult:
        return self._blocked("payments")

    async def fetch_inventory(
        self,
        parameters: Mapping[str, Any] | None = None,
    ) -> RawResult:
        return self._blocked("inventory")

    async def fetch_writeoffs(
        self,
        parameters: Mapping[str, Any] | None = None,
    ) -> RawResult:
        return self._blocked("writeoffs")

    async def fetch_costs(
        self,
        parameters: Mapping[str, Any] | None = None,
    ) -> RawResult:
        return self._blocked("costs")

    async def fetch_employees_or_shifts(
        self,
        parameters: Mapping[str, Any] | None = None,
    ) -> RawResult:
        return self._blocked("employees_or_shifts")

    async def close(self) -> None:
        return None


def build_iiko_client(
    *,
    mode: IikoMode,
    organization_ref: str | None = None,
    base_url: str = "",
    auth_configuration: AuthConfiguration | None = None,
    verify_tls: bool = True,
    connect_timeout_seconds: int = 10,
    read_timeout_seconds: int = 30,
    max_retries: int = 3,
) -> IikoClient:
    if mode is IikoMode.MOCK:
        from integrations.iiko.mock import MockIikoAdapter

        return MockIikoAdapter(organization_ref=organization_ref or "8340002")

    if mode is IikoMode.SERVER_REST_API:
        from integrations.iiko.server_rest import ServerRestIikoClient

        return ServerRestIikoClient(
            base_url=base_url,
            organization_ref=organization_ref,
            auth_configuration=auth_configuration or AuthConfiguration(),
            verify_tls=verify_tls,
            connect_timeout_seconds=connect_timeout_seconds,
            read_timeout_seconds=read_timeout_seconds,
            max_retries=max_retries,
        )

    return BlockedIikoClient(
        mode=mode,
        base_url=base_url,
        organization_ref=organization_ref,
        auth_configuration=auth_configuration,
    )
