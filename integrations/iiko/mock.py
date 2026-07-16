from typing import Any
from uuid import uuid4

from integrations.iiko.base import AuthResult, IikoAdapter


class MockIikoAdapter(IikoAdapter):
    def __init__(self, organization_id: str = "8340002") -> None:
        self.organization_id = organization_id

    async def authenticate(self) -> AuthResult:
        return AuthResult(
            authenticated=True,
            adapter="mock",
            organization_id=self.organization_id,
            trace_id=str(uuid4()),
            details={"mode": "mock", "writes_enabled": False},
        )

    async def get_organizations(self) -> list[dict[str, Any]]:
        return [{"id": self.organization_id, "name": "Dos Amigos"}]

    async def get_terminal_groups(self, organization_id: str) -> list[dict[str, Any]]:
        return [{
            "id": "mock-terminal-group",
            "organization_id": organization_id,
            "name": "Dos Amigos Mock Terminal Group",
        }]

    async def get_nomenclature(self, organization_id: str) -> dict[str, Any]:
        return {
            "organization_id": organization_id,
            "products": [],
            "groups": [],
            "revision": 1,
        }

    async def close(self) -> None:
        return None
