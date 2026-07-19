from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from datetime import date

from apps.core.app.core.config import get_settings
from apps.core.app.db.session import SessionLocal
from apps.core.app.services.daily_coo_report import (
    DailyCooReportConfig,
    DailyCooReportOrchestrator,
)


def parse_date(value: str) -> date:
    return date.fromisoformat(value)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run daily COO report closeout/reconcile.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--closeout", action="store_true")
    mode.add_argument("--reconcile", action="store_true")
    parser.add_argument("--date", type=parse_date)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


async def run(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    settings = get_settings()
    client = settings.build_iiko_client()
    try:
        with SessionLocal() as session:
            service = DailyCooReportOrchestrator(
                session=session,
                client=client,
                config=DailyCooReportConfig(
                    organization_id=settings.iiko_organization_id,
                    business_timezone=settings.business_timezone,
                    payment_category_map=settings.iiko_payment_category_map(),
                ),
            )
            if args.closeout:
                result = await service.closeout(
                    business_date=args.date,
                    dry_run=args.dry_run,
                )
            else:
                result = await service.reconcile(
                    business_date=args.date,
                    dry_run=args.dry_run,
                )
    finally:
        await client.close()

    if args.json:
        print(json.dumps(result.to_json_dict(), ensure_ascii=False, sort_keys=True))
    else:
        print(
            "daily COO report: "
            f"{result.status} mode={result.mode} "
            f"business_date={result.business_date.isoformat()} dry_run={result.dry_run}"
        )
    return 0 if result.status not in {"failed", "dry_run_failed"} else 1


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
