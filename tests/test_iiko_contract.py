import pytest

from integrations.iiko import (
    AuthConfiguration,
    AuthKind,
    BlockedIikoClient,
    IikoMode,
    MockIikoAdapter,
    ResultStatus,
    build_iiko_client,
)


def test_auth_configuration_repr_hides_secrets() -> None:
    configuration = AuthConfiguration(
        kind=AuthKind.USER_PASSWORD,
        username="api_dos_amigos",
        password="do-not-print",
        token="do-not-print-token",
    )

    rendered = repr(configuration)

    assert "do-not-print" not in rendered
    assert "do-not-print-token" not in rendered
    assert configuration.sanitized_metadata()["password_configured"] is True


def test_factory_builds_mock_client() -> None:
    client = build_iiko_client(
        mode=IikoMode.MOCK,
        organization_ref="8340002",
    )

    assert isinstance(client, MockIikoAdapter)


@pytest.mark.asyncio
async def test_unconfirmed_real_adapter_is_safely_blocked() -> None:
    client = build_iiko_client(
        mode=IikoMode.SERVER_REST_API,
        organization_ref="8340002",
        auth_configuration=AuthConfiguration(
            kind=AuthKind.USER_PASSWORD,
            username="api_dos_amigos",
            password="do-not-print",
        ),
    )

    assert isinstance(client, BlockedIikoClient)

    authentication = await client.authenticate()
    discovery = await client.list_organizations()

    assert authentication.status is ResultStatus.BLOCKED
    assert authentication.authenticated is False
    assert authentication.details["writes_enabled"] is False
    assert authentication.details["password_configured"] is True
    assert "do-not-print" not in repr(authentication.details)

    assert discovery.status is ResultStatus.BLOCKED
    assert discovery.payload is None
