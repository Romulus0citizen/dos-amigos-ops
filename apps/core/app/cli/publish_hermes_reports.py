from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from datetime import date

from apps.core.app.core.config import get_settings
from apps.core.app.db.session import SessionLocal
from apps.core.app.services.iiko_sales_automation import (
    DisabledHermesReportPublisher,
    HermesReportPublisher,
    MockHermesReportPublisher,
)
from apps.core.app.services.publish_hermes_reports import HermesOutboxPublishService


def parse_date(value: str) -> date:
    return date.fromisoformat(value)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish queued reports for Hermes.")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--date", type=parse_date)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def build_publisher(mode: str) -> HermesReportPublisher:
    if mode == "mock":
        return MockHermesReportPublisher()
    return DisabledHermesReportPublisher()


async def run(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    settings = get_settings()
    with SessionLocal() as session:
        result = await HermesOutboxPublishService(
            session=session,
            publisher=build_publisher(settings.hermes_delivery_mode),
        ).publish_pending(
            limit=args.limit,
            business_date=args.date,
            retry_failed=args.retry_failed,
            dry_run=args.dry_run,
        )

    if not args.json:
        print(
            "hermes reports publish: "
            f"{result.status} considered={result.considered} "
            f"delivered={result.delivered} failed={result.failed} dry_run={result.dry_run}"
        )
    print(json.dumps(result.to_json_dict(), ensure_ascii=False, sort_keys=True))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
