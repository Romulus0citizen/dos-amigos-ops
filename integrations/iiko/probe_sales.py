from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

from apps.core.app.core.config import get_settings
from integrations.iiko.client import IikoClient
from integrations.iiko.schemas import ProbeResult, RawResult, ResultStatus

SALES_DISCOVERY_ERROR_CODE = "iiko_sales_olap_contract_confirmed_partial"
SALES_DISCOVERY_SAFE_MESSAGE = (
    "iikoServer OLAP SALES contract is confirmed; live day data is summarized only."
)
SALES_FIELDS = [
    "OpenDate.Typed",
    "Department.Id",
    "Storned",
    "OrderDeleted",
    "DishSumInt",
    "DishDiscountSumInt",
    "DiscountSum",
    "IncreaseSum",
    "DishReturnSum",
    "UniqOrderId.OrdersCount",
    "PayTypes.Group",
    "PayTypes.GUID",
    "PayTypes",
    "DishId",
    "DishName",
    "DishSize.Id",
    "DishAmountInt",
]


def default_probe_date(today: date | None = None) -> date:
    return (today or date.today()) - timedelta(days=1)


def parse_business_date(value: str) -> date:
    return date.fromisoformat(value)


def _summarize_result(result: ProbeResult | RawResult | None) -> dict[str, object] | None:
    if result is None:
        return None

    summary: dict[str, object] = {
        "status": result.status.value,
        "error_code": result.error_code,
    }
    if isinstance(result, ProbeResult):
        summary.update(
            {
                "endpoint_reachable": result.endpoint_reachable,
                "authenticated": result.authenticated,
            }
        )
    if isinstance(result, RawResult):
        summary.update(
            {
                "dataset": result.dataset,
                "records_count": result.records_count,
            }
        )
    return summary


def _organization_present(
    organizations_result: RawResult | None,
    organization_id: str | None,
) -> bool | None:
    if organizations_result is None or organizations_result.status is not ResultStatus.PROVEN:
        return None
    if not organization_id:
        return organizations_result.records_count > 0
    if not isinstance(organizations_result.payload, list):
        return None

    for item in organizations_result.payload:
        if isinstance(item, dict) and str(item.get("id")) == organization_id:
            return True
    return False


def build_blocked_sales_discovery_report(
    *,
    requested_date: date,
    organization_id: str | None = None,
    probe_result: ProbeResult | None = None,
    organizations_result: RawResult | None = None,
    error_code: str = SALES_DISCOVERY_ERROR_CODE,
) -> dict[str, Any]:
    return {
        "status": ResultStatus.PARTIAL.value,
        "result_status": ResultStatus.PARTIAL.value,
        "error_code": error_code,
        "safe_message": SALES_DISCOVERY_SAFE_MESSAGE,
        "logical_name": "iiko_sales_report_discovery",
        "sales_source_confirmed": True,
        "endpoint": "/api/v2/reports/olap",
        "http_method": "POST",
        "http_status": None,
        "content_type": "application/json",
        "format": "json",
        "rows_count": None,
        "fields": SALES_FIELDS,
        "organization_id": organization_id,
        "organization_present": _organization_present(organizations_result, organization_id),
        "requested_date": requested_date.isoformat(),
        "date_min": None,
        "date_max": None,
        "probe": _summarize_result(probe_result),
        "organizations": _summarize_result(organizations_result),
        "next_actions": [
            "Run a dry-run import for one closed day.",
            "Verify a day with non-zero refunds before marking refund amounts PROVEN.",
            "Configure explicit PayTypes.GUID mappings for NON_CASH categories when needed.",
        ],
    }


async def run_probe(
    *,
    requested_date: date,
    organization_id: str | None = None,
) -> dict[str, Any]:
    client: IikoClient | None = None
    probe_result: ProbeResult | None = None
    organizations_result: RawResult | None = None

    try:
        settings = get_settings()
        client = settings.build_iiko_client()
        probe_result = await client.probe()
        organizations_result = await client.list_organizations()
    except Exception:
        return build_blocked_sales_discovery_report(
            requested_date=requested_date,
            organization_id=organization_id,
            probe_result=probe_result,
            organizations_result=organizations_result,
            error_code="iiko_sales_discovery_failed_safely",
        )
    finally:
        if client is not None:
            await client.close()

    return build_blocked_sales_discovery_report(
        requested_date=requested_date,
        organization_id=organization_id,
        probe_result=probe_result,
        organizations_result=organizations_result,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Safely probe iikoServer sales API discovery status."
    )
    parser.add_argument(
        "--date",
        type=parse_business_date,
        default=default_probe_date(),
        help="Closed business date to use as discovery context, YYYY-MM-DD.",
    )
    parser.add_argument(
        "--organization-id",
        default=None,
        help="Optional organization id expected in already proven organization discovery.",
    )
    args = parser.parse_args(argv)

    result = asyncio.run(
        run_probe(
            requested_date=args.date,
            organization_id=args.organization_id,
        )
    )
    print(f"iiko sales discovery: {result['status']} ({result['error_code']})")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
