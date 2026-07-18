import hashlib
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from integrations.iiko import AuthConfiguration, AuthKind, IikoMode, ResultStatus, build_iiko_client
from integrations.iiko.server_rest import ServerRestIikoClient

PASSWORD = "pásswörd"
PASSWORD_SHA1 = hashlib.sha1(PASSWORD.encode("utf-8")).hexdigest()
TOKEN = "123e4567-e89b-12d3-a456-426614174000"


def make_client(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    organization_ref: str | None = None,
    max_retries: int = 0,
    verify_tls: bool = True,
) -> ServerRestIikoClient:
    return ServerRestIikoClient(
        base_url="https://example.iiko.it/resto/",
        organization_ref=organization_ref,
        auth_configuration=AuthConfiguration(
            kind=AuthKind.USER_PASSWORD,
            username="api_dos_amigos",
            password=PASSWORD,
        ),
        verify_tls=verify_tls,
        connect_timeout_seconds=1,
        read_timeout_seconds=1,
        max_retries=max_retries,
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_successful_authentication_stores_only_sanitized_state() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/resto/api/auth"
        assert request.url.params["login"] == "api_dos_amigos"
        assert request.url.params["pass"] == PASSWORD_SHA1
        return httpx.Response(200, text=TOKEN)

    client = make_client(handler)

    result = await client.authenticate()

    assert result.status is ResultStatus.PROVEN
    assert result.authenticated is True
    assert result.details["writes_enabled"] is False
    assert TOKEN not in repr(result)
    assert PASSWORD not in repr(result)
    assert PASSWORD_SHA1 not in repr(result)


@pytest.mark.asyncio
async def test_successful_authentication_accepts_quoted_uuid_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=f'"{TOKEN}"')

    client = make_client(handler)

    result = await client.authenticate()

    assert result.status is ResultStatus.PROVEN
    assert result.authenticated is True
    assert client._token == TOKEN


@pytest.mark.asyncio
async def test_authentication_hashes_password_as_utf8_sha1_lower_hex() -> None:
    observed_hashes: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed_hashes.append(request.url.params["pass"])
        return httpx.Response(200, text=TOKEN)

    client = make_client(handler)

    await client.authenticate()

    assert observed_hashes == [PASSWORD_SHA1]
    assert len(observed_hashes[0]) == 40
    assert observed_hashes[0].islower()


@pytest.mark.asyncio
async def test_authentication_rejected_does_not_leak_secrets() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="bad credentials")

    client = make_client(handler)

    result = await client.authenticate()

    assert result.status is ResultStatus.BLOCKED
    assert result.authenticated is False
    assert result.error_code == "authentication_rejected"
    assert "api_dos_amigos" not in repr(result)
    assert PASSWORD not in repr(result)
    assert PASSWORD_SHA1 not in repr(result)
    assert TOKEN not in repr(result)


@pytest.mark.asyncio
async def test_network_error_returns_unknown_endpoint_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection failed", request=request)

    client = make_client(handler)

    result = await client.authenticate()

    assert result.status is ResultStatus.UNKNOWN
    assert result.authenticated is False
    assert result.error_code == "endpoint_unreachable"
    assert PASSWORD not in repr(result)
    assert PASSWORD_SHA1 not in repr(result)


@pytest.mark.asyncio
async def test_authentication_network_error_is_not_retried() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        raise httpx.ConnectError("connection failed", request=request)

    client = make_client(handler, max_retries=3)

    result = await client.authenticate()

    assert result.status is ResultStatus.UNKNOWN
    assert result.error_code == "endpoint_unreachable"
    assert calls == ["/resto/api/auth"]


@pytest.mark.asyncio
async def test_invalid_uuid_token_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not-a-uuid")

    client = make_client(handler)

    result = await client.authenticate()

    assert result.status is ResultStatus.UNKNOWN
    assert result.authenticated is False
    assert result.error_code == "invalid_auth_token"
    assert "not-a-uuid" not in repr(result)


@pytest.mark.asyncio
async def test_list_organizations_parses_department_xml() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/resto/api/auth":
            return httpx.Response(200, text=TOKEN)
        assert request.url.path == "/resto/api/corporation/departments"
        assert request.url.params["key"] == TOKEN
        return httpx.Response(
            200,
            text="""
            <corporateItemDtos>
              <corporateItemDto>
                <id>department-1</id>
                <name>Main Bar</name>
                <type>DEPARTMENT</type>
              </corporateItemDto>
              <corporateItemDto>
                <id>store-1</id>
                <name>Store</name>
                <type>STORE</type>
              </corporateItemDto>
            </corporateItemDtos>
            """,
        )

    client = make_client(handler)

    result = await client.list_organizations()

    assert result.status is ResultStatus.PROVEN
    assert result.dataset == "organizations"
    assert result.records_count == 1
    assert result.payload == [{"id": "department-1", "name": "Main Bar", "type": "DEPARTMENT"}]
    assert TOKEN not in repr(result)


@pytest.mark.asyncio
async def test_list_organizations_filters_by_configured_organization_ref() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/resto/api/auth":
            return httpx.Response(200, text=TOKEN)
        return httpx.Response(
            200,
            text="""
            <corporateItemDtos>
              <corporateItemDto>
                <id>department-1</id>
                <name>Main Bar</name>
                <type>DEPARTMENT</type>
              </corporateItemDto>
              <corporateItemDto>
                <id>department-2</id>
                <name>Kitchen</name>
                <type>DEPARTMENT</type>
              </corporateItemDto>
            </corporateItemDtos>
            """,
        )

    client = make_client(handler, organization_ref="department-2")

    result = await client.list_organizations()

    assert result.status is ResultStatus.PROVEN
    assert result.records_count == 1
    assert result.payload == [{"id": "department-2", "name": "Kitchen", "type": "DEPARTMENT"}]


@pytest.mark.asyncio
async def test_list_organizations_retries_departments_request() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/resto/api/auth":
            return httpx.Response(200, text=TOKEN)
        if calls.count("/resto/api/corporation/departments") < 3:
            raise httpx.ConnectError("temporary read failure", request=request)
        return httpx.Response(
            200,
            text="""
            <corporateItemDtos>
              <corporateItemDto>
                <id>department-1</id>
                <name>Main Bar</name>
                <type>DEPARTMENT</type>
              </corporateItemDto>
            </corporateItemDtos>
            """,
        )

    client = make_client(handler, max_retries=2)

    result = await client.list_organizations()

    assert result.status is ResultStatus.PROVEN
    assert calls == [
        "/resto/api/auth",
        "/resto/api/corporation/departments",
        "/resto/api/corporation/departments",
        "/resto/api/corporation/departments",
    ]


@pytest.mark.asyncio
async def test_list_organizations_parses_namespaced_department_xml() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/resto/api/auth":
            return httpx.Response(200, text=TOKEN)
        return httpx.Response(
            200,
            text="""
            <root xmlns="https://iiko.example/schema">
              <corporateItemDto>
                <id>department-2</id>
                <name>Kitchen</name>
                <type>DEPARTMENT</type>
              </corporateItemDto>
            </root>
            """,
        )

    client = make_client(handler)

    result = await client.list_organizations()

    assert result.status is ResultStatus.PROVEN
    assert result.payload == [{"id": "department-2", "name": "Kitchen", "type": "DEPARTMENT"}]


@pytest.mark.asyncio
async def test_list_organizations_returns_empty_proven_result_for_no_departments() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/resto/api/auth":
            return httpx.Response(200, text=TOKEN)
        return httpx.Response(
            200,
            text="""
            <corporateItemDtos>
              <corporateItemDto>
                <id>store-1</id>
                <name>Store</name>
                <type>STORE</type>
              </corporateItemDto>
            </corporateItemDtos>
            """,
        )

    client = make_client(handler)

    result = await client.list_organizations()

    assert result.status is ResultStatus.PROVEN
    assert result.records_count == 0
    assert result.payload == []


@pytest.mark.asyncio
async def test_logout_clears_token_even_when_logout_fails() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/resto/api/auth":
            return httpx.Response(200, text=TOKEN)
        assert request.url.path == "/resto/api/logout"
        assert request.url.params["key"] == TOKEN
        return httpx.Response(500, text="logout failed")

    client = make_client(handler)
    await client.authenticate()

    await client.close()
    await client.close()

    assert calls == ["/resto/api/auth", "/resto/api/logout"]
    assert client._token is None


@pytest.mark.asyncio
async def test_logout_network_error_is_not_retried() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/resto/api/auth":
            return httpx.Response(200, text=TOKEN)
        raise httpx.ConnectError("logout response lost", request=request)

    client = make_client(handler, max_retries=3)
    await client.authenticate()

    await client.close()

    assert calls == ["/resto/api/auth", "/resto/api/logout"]
    assert client._token is None


@pytest.mark.asyncio
async def test_repeated_authenticate_reuses_existing_token() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, text=TOKEN)

    client = make_client(handler)

    first = await client.authenticate()
    second = await client.authenticate()

    assert first.status is ResultStatus.PROVEN
    assert second.status is ResultStatus.PROVEN
    assert calls == ["/resto/api/auth"]


def test_factory_builds_server_rest_client() -> None:
    client = build_iiko_client(
        mode=IikoMode.SERVER_REST_API,
        organization_ref="department-1",
        base_url="https://example.iiko.it/resto",
        auth_configuration=AuthConfiguration(
            kind=AuthKind.USER_PASSWORD,
            username="api_dos_amigos",
            password=PASSWORD,
        ),
    )

    assert isinstance(client, ServerRestIikoClient)


@pytest.mark.asyncio
async def test_unsupported_server_rest_datasets_are_explicitly_blocked() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("unsupported datasets must not perform HTTP requests")

    client = make_client(handler)
    calls: list[tuple[Callable[..., Any], tuple[Any, ...]]] = [
        (client.list_terminal_groups, ("department-1",)),
        (client.fetch_nomenclature, ("department-1",)),
        (client.fetch_menu, ("department-1",)),
        (client.fetch_payments, ()),
        (client.fetch_inventory, ()),
        (client.fetch_writeoffs, ()),
        (client.fetch_costs, ()),
        (client.fetch_employees_or_shifts, ()),
    ]

    for method, args in calls:
        result = await method(*args)
        assert result.status is ResultStatus.BLOCKED
        assert result.payload is None
        assert result.error_message_sanitized == "dataset_not_implemented_for_server_rest_api"


@pytest.mark.asyncio
async def test_results_and_errors_do_not_contain_password_hash_or_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/resto/api/auth":
            return httpx.Response(200, text=TOKEN)
        return httpx.Response(
            200,
            text="""
            <corporateItemDtos>
              <corporateItemDto>
                <id>department-1</id>
                <name>Main Bar</name>
                <type>DEPARTMENT</type>
              </corporateItemDto>
            </corporateItemDtos>
            """,
        )

    client = make_client(handler)

    authentication = await client.authenticate()
    organizations = await client.list_organizations()
    blocked = await client.fetch_menu("department-1")

    rendered = repr([authentication, organizations, blocked])
    assert PASSWORD not in rendered
    assert PASSWORD_SHA1 not in rendered
    assert TOKEN not in rendered


def test_tls_verification_is_enabled_by_default() -> None:
    client = ServerRestIikoClient(
        base_url="https://example.iiko.it/resto",
        organization_ref=None,
        auth_configuration=AuthConfiguration(
            kind=AuthKind.USER_PASSWORD,
            username="api_dos_amigos",
            password=PASSWORD,
        ),
        connect_timeout_seconds=1,
        read_timeout_seconds=1,
        max_retries=0,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=TOKEN)),
    )

    assert client.verify_tls is True
