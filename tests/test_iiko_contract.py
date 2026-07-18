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
from integrations.iiko.server_rest import ServerRestIikoClient


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


def test_factory_builds_mock_client_with_internal_fixture_when_ref_is_omitted() -> None:
    client = build_iiko_client(mode=IikoMode.MOCK)

    assert isinstance(client, MockIikoAdapter)
    assert client.organization_ref == "8340002"


@pytest.mark.asyncio
async def test_other_real_adapters_are_safely_blocked() -> None:
    client = build_iiko_client(
        mode=IikoMode.CLOUD_API,
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


def test_server_rest_factory_returns_real_read_only_adapter() -> None:
    client = build_iiko_client(
        mode=IikoMode.SERVER_REST_API,
        organization_ref="8340002",
        base_url="https://example.iiko.it/resto",
        auth_configuration=AuthConfiguration(
            kind=AuthKind.USER_PASSWORD,
            username="api_dos_amigos",
            password="do-not-print",
        ),
    )

    assert isinstance(client, ServerRestIikoClient)


def test_server_rest_factory_does_not_invent_organization_ref() -> None:
    client = build_iiko_client(
        mode=IikoMode.SERVER_REST_API,
        base_url="https://example.iiko.it/resto",
        auth_configuration=AuthConfiguration(
            kind=AuthKind.USER_PASSWORD,
            username="api_dos_amigos",
            password="do-not-print",
        ),
    )

    assert isinstance(client, ServerRestIikoClient)
    assert client.organization_ref is None
