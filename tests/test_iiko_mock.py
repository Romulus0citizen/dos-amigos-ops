import pytest

from integrations.iiko.mock import MockIikoAdapter
from integrations.iiko.schemas import IikoMode, ResultStatus


@pytest.mark.asyncio
async def test_mock_iiko_authentication_is_read_only() -> None:
    adapter = MockIikoAdapter(organization_id="8340002")
    result = await adapter.authenticate()

    assert result.status is ResultStatus.PROVEN
    assert result.authenticated is True
    assert result.organization_id == "8340002"
    assert result.mode is IikoMode.MOCK
    assert result.details["writes_enabled"] is False


@pytest.mark.asyncio
async def test_mock_implemented_datasets_return_typed_results() -> None:
    adapter = MockIikoAdapter(organization_ref="8340002")

    organizations = await adapter.list_organizations()
    terminals = await adapter.list_terminal_groups("8340002")
    nomenclature = await adapter.fetch_nomenclature("8340002")

    assert organizations.status is ResultStatus.PROVEN
    assert organizations.records_count == 1
    assert terminals.status is ResultStatus.PROVEN
    assert terminals.records_count == 1
    assert nomenclature.status is ResultStatus.PROVEN


@pytest.mark.asyncio
async def test_mock_unsupported_dataset_is_blocked_not_fabricated() -> None:
    adapter = MockIikoAdapter()

    result = await adapter.fetch_menu("8340002")

    assert result.status is ResultStatus.BLOCKED
    assert result.payload is None
    assert result.error_code == "capability_blocked"
