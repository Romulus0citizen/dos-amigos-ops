from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from datetime import date

from apps.core.app.core.config import get_settings
from apps.core.app.db.session import SessionLocal
from apps.core.app.services.iiko_sales_automation import (
    IikoSalesAutomationService,
    PostgresAdvisorySalesAutomationLock,
    create_sales_automation_config_from_settings,
)


def parse_date(value: str) -> date:
    return date.fromisoformat(value)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one-shot iiko sales automation.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--run-due", action="store_true")
    mode.add_argument("--date", type=parse_date)
    mode.add_argument("--backfill", action="store_true")
    mode.add_argument("--rebuild-outbox", action="store_true")
    parser.add_argument("--backfill-days", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-publish", action="store_true")
    parser.add_argument("--retry-partial", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


async def run(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    settings = get_settings()
    client = settings.build_iiko_client()
    try:
        with SessionLocal() as session:
            config = create_sales_automation_config_from_settings(settings)
            service = IikoSalesAutomationService(
                session=session,
                client=client,
                config=config,
                lock=PostgresAdvisorySalesAutomationLock(session),
            )
            if args.run_due:
                result = await service.run_due(
                    dry_run=args.dry_run,
                    force=args.force,
                    no_publish=args.no_publish,
                    retry_partial=args.retry_partial,
                )
            elif args.date:
                result = await service.run_date(
                    args.date,
                    dry_run=args.dry_run,
                    force=args.force,
                    no_publish=args.no_publish,
                )
            elif args.backfill:
                target = service.previous_business_date()
                result = await service.run_backfill(
                    target_date=target,
                    backfill_days=args.backfill_days or settings.sales_backfill_max_days,
                    dry_run=args.dry_run,
                    no_publish=args.no_publish,
                    retry_partial=args.retry_partial,
                )
            else:
                result = service.rebuild_outbox(dry_run=args.dry_run)
    finally:
        await client.close()

    if not args.json:
        print(
            "iiko sales automation: "
            f"{result.status} processed={result.days_processed} "
            f"outbox_created={result.outbox_created} dry_run={result.dry_run}"
        )
    print(json.dumps(result.to_json_dict(), ensure_ascii=False, sort_keys=True))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
