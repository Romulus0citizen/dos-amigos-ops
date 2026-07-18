import json
from decimal import Decimal

import httpx
import pytest
from fixtures_iiko_sales import BUSINESS_DATE, ORG_ID, TOKEN

from integrations.iiko import AuthConfiguration, AuthKind, ResultStatus
from integrations.iiko.server_rest import ServerRestIikoClient


def make_sales_client(handler, *, max_retries: int = 0) -> ServerRestIikoClient:
    return ServerRestIikoClient(
        base_url="https://example.iiko.it/resto",
        organization_ref=ORG_ID,
        auth_configuration=AuthConfiguration(
            kind=AuthKind.USER_PASSWORD,
            username="api_dos_amigos",
            password="secret",
        ),
        verify_tls=True,
        connect_timeout_seconds=1,
        read_timeout_seconds=1,
        max_retries=max_retries,
        transport=httpx.MockTransport(handler),
    )


def olap_response(amount: str = "10.25") -> httpx.Response:
    return httpx.Response(
        200,
        text=f"""
        {{
          "data": [
            {{
              "OpenDate.Typed": "{BUSINESS_DATE}",
              "Department.Id": "{ORG_ID}",
              "Storned": "FALSE",
              "OrderDeleted": "NOT_DELETED",
              "DishSumInt": {amount},
              "DishDiscountSumInt": {amount},
              "DiscountSum": 0,
              "IncreaseSum": 0,
              "DishReturnSum": 0,
              "UniqOrderId.OrdersCount": 1,
              "PayTypes.Group": "CASH",
              "PayTypes.GUID": "pay-cash",
              "PayTypes": "Cash",
              "DishId": "product-1",
              "DishName": "Synthetic",
              "DishSize.Id": null,
              "DishAmountInt": 1
            }}
          ],
          "summary": []
        }}
        """,
    )


@pytest.mark.asyncio
async def test_fetch_orders_or_sales_posts_confirmed_olap_requests_and_parses_decimal() -> None:
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/resto/api/auth":
            return httpx.Response(200, text=TOKEN)
        assert request.url.path == "/resto/api/v2/reports/olap"
        assert request.url.params["key"] == TOKEN
        bodies.append(json.loads(request.content))
        return olap_response("10.25")

    client = make_sales_client(handler)

    result = await client.fetch_orders_or_sales(
        {"organization_id": ORG_ID, "business_date": BUSINESS_DATE}
    )

    assert result.status is ResultStatus.PROVEN
    assert result.records_count == 3
    assert len(bodies) == 3
    assert bodies[0]["reportType"] == "SALES"
    assert bodies[0]["filters"]["Department.Id"]["values"] == [ORG_ID]
    assert bodies[0]["filters"]["OpenDate.Typed"]["from"] == BUSINESS_DATE
    assert bodies[0]["groupByRowFields"] == [
        "OpenDate.Typed",
        "Department.Id",
        "Storned",
        "OrderDeleted",
    ]
    assert isinstance(result.payload["daily"]["data"][0]["DishSumInt"], Decimal)
    assert not isinstance(result.payload["daily"]["data"][0]["DishSumInt"], float)


@pytest.mark.asyncio
async def test_fetch_orders_or_sales_retries_429_for_read_only_olap() -> None:
    olap_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal olap_calls
        if request.url.path == "/resto/api/auth":
            return httpx.Response(200, text=TOKEN)
        olap_calls += 1
        if olap_calls == 1:
            return httpx.Response(429, text="too many requests")
        return olap_response()

    client = make_sales_client(handler, max_retries=1)

    result = await client.fetch_orders_or_sales(
        {"organization_id": ORG_ID, "business_date": BUSINESS_DATE}
    )

    assert result.status is ResultStatus.PROVEN
    assert olap_calls == 4


@pytest.mark.asyncio
async def test_fetch_orders_or_sales_blocks_400_without_retry() -> None:
    olap_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal olap_calls
        if request.url.path == "/resto/api/auth":
            return httpx.Response(200, text=TOKEN)
        olap_calls += 1
        return httpx.Response(400, text="bad request")

    client = make_sales_client(handler, max_retries=3)

    result = await client.fetch_orders_or_sales(
        {"organization_id": ORG_ID, "business_date": BUSINESS_DATE}
    )

    assert result.status is ResultStatus.BLOCKED
    assert result.error_code == "sales_olap_http_400"
    assert olap_calls == 1


@pytest.mark.asyncio
async def test_fetch_orders_or_sales_invalid_json_is_unknown_and_sanitized() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/resto/api/auth":
            return httpx.Response(200, text=TOKEN)
        return httpx.Response(200, text="{not-json")

    client = make_sales_client(handler)

    result = await client.fetch_orders_or_sales(
        {"organization_id": ORG_ID, "business_date": BUSINESS_DATE}
    )

    rendered = repr(result)
    assert result.status is ResultStatus.UNKNOWN
    assert result.error_code == "sales_olap_json_invalid"
    assert TOKEN not in rendered


@pytest.mark.asyncio
async def test_fetch_orders_or_sales_timeout_is_unknown() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/resto/api/auth":
            return httpx.Response(200, text=TOKEN)
        raise httpx.ReadTimeout("timed out", request=request)

    client = make_sales_client(handler)

    result = await client.fetch_orders_or_sales(
        {"organization_id": ORG_ID, "business_date": BUSINESS_DATE}
    )

    assert result.status is ResultStatus.UNKNOWN
    assert result.error_code == "endpoint_unreachable"
