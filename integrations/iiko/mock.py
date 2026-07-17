from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from integrations.iiko.client import IikoClient
from integrations.iiko.schemas import (
    AuthResult,
    IikoMode,
    ProbeResult,
    RawResult,
    ResultStatus,
)


class MockIikoAdapter(IikoClient):
    adapter_name = "mock"
    mode = IikoMode.MOCK

    def __init__(
        self,
        organization_ref: str = "8340002",
        organization_id: str | None = None,
    ) -> None:
        self.organization_ref = organization_id or organization_ref

    def _trace_id(self) -> str:
        return str(uuid4())

    def _not_implemented(self, dataset: str) -> RawResult:
        return RawResult.blocked(
            adapter=self.adapter_name,
            mode=self.mode,
            dataset=dataset,
            trace_id=self._trace_id(),
            reason="mock_dataset_not_implemented",
        )

    async def authenticate(self) -> AuthResult:
        return AuthResult(
            status=ResultStatus.PROVEN,
            authenticated=True,
            adapter=self.adapter_name,
            mode=self.mode,
            organization_ref=self.organization_ref,
            trace_id=self._trace_id(),
            details={"mode": self.mode.value, "writes_enabled": False},
        )

    async def probe(self) -> ProbeResult:
        return ProbeResult(
            status=ResultStatus.PROVEN,
            adapter=self.adapter_name,
            mode=self.mode,
            trace_id=self._trace_id(),
            endpoint_reachable=True,
            authenticated=True,
            details={"mode": self.mode.value, "writes_enabled": False},
        )

    async def list_organizations(self) -> RawResult:
        payload = [{"id": self.organization_ref, "name": "Dos Amigos"}]
        return RawResult.proven(
            adapter=self.adapter_name,
            mode=self.mode,
            dataset="organizations",
            trace_id=self._trace_id(),
            payload=payload,
            records_count=len(payload),
        )

    async def list_terminal_groups(self, organization_ref: str) -> RawResult:
        payload = [
            {
                "id": "mock-terminal-group",
                "organization_id": organization_ref,
                "name": "Dos Amigos Mock Terminal Group",
            }
        ]
        return RawResult.proven(
            adapter=self.adapter_name,
            mode=self.mode,
            dataset="terminal_groups",
            trace_id=self._trace_id(),
            payload=payload,
            records_count=len(payload),
        )

    async def fetch_nomenclature(self, organization_ref: str) -> RawResult:
        payload = {
            "organization_id": organization_ref,
            "products": [],
            "groups": [],
            "revision": 1,
        }
        return RawResult.proven(
            adapter=self.adapter_name,
            mode=self.mode,
            dataset="nomenclature",
            trace_id=self._trace_id(),
            payload=payload,
            records_count=0,
        )

    async def fetch_menu(self, organization_ref: str) -> RawResult:
        return self._not_implemented("menu")

    async def fetch_orders_or_sales(
        self,
        parameters: Mapping[str, Any] | None = None,
    ) -> RawResult:
        return self._not_implemented("orders_or_sales")

    async def fetch_payments(
        self,
        parameters: Mapping[str, Any] | None = None,
    ) -> RawResult:
        return self._not_implemented("payments")

    async def fetch_inventory(
        self,
        parameters: Mapping[str, Any] | None = None,
    ) -> RawResult:
        return self._not_implemented("inventory")

    async def fetch_writeoffs(
        self,
        parameters: Mapping[str, Any] | None = None,
    ) -> RawResult:
        return self._not_implemented("writeoffs")

    async def fetch_costs(
        self,
        parameters: Mapping[str, Any] | None = None,
    ) -> RawResult:
        return self._not_implemented("costs")

    async def fetch_employees_or_shifts(
        self,
        parameters: Mapping[str, Any] | None = None,
    ) -> RawResult:
        return self._not_implemented("employees_or_shifts")

    async def close(self) -> None:
        return None
