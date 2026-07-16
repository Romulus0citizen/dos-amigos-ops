from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AuthResult:
    authenticated: bool
    adapter: str
    organization_id: str | None
    trace_id: str
    details: dict[str, Any]


class IikoAdapter(ABC):
    @abstractmethod
    async def authenticate(self) -> AuthResult:
        raise NotImplementedError

    @abstractmethod
    async def get_organizations(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    async def get_terminal_groups(self, organization_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    async def get_nomenclature(self, organization_id: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        raise NotImplementedError
