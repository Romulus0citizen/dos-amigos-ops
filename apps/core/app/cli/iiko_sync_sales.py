from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from datetime import date

from apps.core.app.core.config import get_settings
from apps.core.app.db.session import SessionLocal
from apps.core.app.services.iiko_sales import IikoSalesSyncService


def parse_date(value: str) -> date:
    return date.fromisoformat(value)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import read-only iiko sales aggregates.")
    date_group = parser.add_mutually_exclusive_group(required=True)
    date_group.add_argument("--date", type=parse_date)
    date_group.add_argument("--from-date", type=parse_date)
    parser.add_argument("--to-date", type=parse_date)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--organization-id")
    parser.add_argument("--allow-open-day", action="store_true")
    return parser.parse_args(argv)


async def run(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    date_from = args.date or args.from_date
    date_to = args.date or args.to_date
    if date_to is None:
        raise SystemExit("--to-date is required with --from-date")

    settings = get_settings()
    organization_id = args.organization_id or settings.iiko_organization_id
    if not organization_id:
        raise SystemExit("organization id is required")

    client = settings.build_iiko_client()
    try:
        with SessionLocal() as session:
            service = IikoSalesSyncService(
                session=session,
                client=client,
                organization_id=organization_id,
                business_timezone=settings.business_timezone,
                payment_category_map=settings.iiko_payment_category_map(),
            )
            result = await service.sync_range(
                date_from=date_from,
                date_to=date_to,
                dry_run=args.dry_run,
                allow_open_day=args.allow_open_day,
            )
    finally:
        await client.close()

    print(
        "iiko sales sync: "
        f"{result.status.value} {result.date_from.isoformat()}..{result.date_to.isoformat()} "
        f"source_rows={result.source_rows} persisted={result.records_persisted} "
        f"unchanged={result.unchanged} dry_run={result.dry_run}"
    )
    print(json.dumps(result.to_json_dict(), ensure_ascii=False, sort_keys=True))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
