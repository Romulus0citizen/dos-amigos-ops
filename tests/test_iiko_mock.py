import pytest

from integrations.iiko.mock import MockIikoAdapter


@pytest.mark.asyncio
async def test_mock_iiko_authentication_is_read_only() -> None:
    adapter = MockIikoAdapter(organization_id="8340002")
    result = await adapter.authenticate()
    assert result.authenticated is True
    assert result.organization_id == "8340002"
    assert result.details["writes_enabled"] is False
