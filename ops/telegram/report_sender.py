from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol, cast

from dotenv import load_dotenv

DEFAULT_BOT_DIR = "/opt/hermes-bots/dos-amigos"
DEFAULT_CORE_URL = "http://127.0.0.1:8090"
DEFAULT_LIMIT = 20
TELEGRAM_MESSAGE_LIMIT = 4096
MIN_RECIPIENT_KEY_SECRET_LENGTH = 32

_AUTH_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_TOKEN_ASSIGNMENT_RE = re.compile(r"\b(token|bot_token|authorization)=\S+", re.IGNORECASE)
_NUMERIC_ID_RE = re.compile(r"\b\d+\b")


@dataclass(frozen=True)
class ReportOutboxItem:
    id: str
    business_date: date
    payload_markdown: str
    delivery_attempts: int

    @classmethod
    def from_json_dict(cls, value: dict[str, object]) -> ReportOutboxItem:
        return cls(
            id=str(value["id"]),
            business_date=date.fromisoformat(str(value["business_date"])),
            payload_markdown=str(value["payload_markdown"]),
            delivery_attempts=int(cast(str | int, value["delivery_attempts"])),
        )


@dataclass(frozen=True)
class ReportSenderConfig:
    allowed_ids: list[str]
    recipient_key_secret: str
    business_date: date | None = None
    dry_run: bool = False
    retry_failed: bool = False
    limit: int = DEFAULT_LIMIT


@dataclass(frozen=True)
class ReportSenderResult:
    status: str
    considered: int
    delivered: int
    failed: int
    messages_sent: int
    dry_run: bool = False

    def to_json_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "considered": self.considered,
            "delivered": self.delivered,
            "failed": self.failed,
            "messages_sent": self.messages_sent,
            "dry_run": self.dry_run,
        }


class ReportOutboxCoreClient(Protocol):
    async def get_pending(
        self,
        *,
        limit: int,
        business_date: date | None,
        include_failed: bool,
    ) -> list[ReportOutboxItem]:
        raise NotImplementedError

    async def mark_delivered(self, report_id: str) -> None:
        raise NotImplementedError

    async def mark_failed(
        self,
        report_id: str,
        *,
        error_code: str,
        error_message: str,
    ) -> None:
        raise NotImplementedError

    async def register_recipients(
        self,
        report_id: str,
        *,
        recipient_keys: list[str],
    ) -> dict[str, str]:
        raise NotImplementedError

    async def mark_recipient_delivered(self, report_id: str, *, recipient_key: str) -> None:
        raise NotImplementedError

    async def mark_recipient_failed(
        self,
        report_id: str,
        *,
        recipient_key: str,
        error_code: str,
        error_message: str,
    ) -> None:
        raise NotImplementedError


class TelegramClient(Protocol):
    async def send_message(self, *, chat_id: str, text: str) -> None:
        raise NotImplementedError


class HttpReportOutboxClient:
    def __init__(
        self,
        *,
        core_url: str = DEFAULT_CORE_URL,
        internal_token: str = "",
    ) -> None:
        self.core_url = core_url.rstrip("/")
        self.internal_token = internal_token

    async def get_pending(
        self,
        *,
        limit: int,
        business_date: date | None,
        include_failed: bool,
    ) -> list[ReportOutboxItem]:
        query: dict[str, str] = {"limit": str(limit)}
        if business_date is not None:
            query["business_date"] = business_date.isoformat()
        if include_failed:
            query["include_failed"] = "true"
        data = await self._request("GET", "/api/v1/internal/report-outbox/pending", query=query)
        if not isinstance(data, list):
            raise RuntimeError("core API returned unexpected pending response")
        return [ReportOutboxItem.from_json_dict(item) for item in data if isinstance(item, dict)]

    async def mark_delivered(self, report_id: str) -> None:
        await self._request("POST", f"/api/v1/internal/report-outbox/{report_id}/delivered")

    async def mark_failed(
        self,
        report_id: str,
        *,
        error_code: str,
        error_message: str,
    ) -> None:
        await self._request(
            "POST",
            f"/api/v1/internal/report-outbox/{report_id}/failed",
            body={
                "error_code": error_code,
                "error_message": error_message,
            },
        )

    async def register_recipients(
        self,
        report_id: str,
        *,
        recipient_keys: list[str],
    ) -> dict[str, str]:
        data = await self._request(
            "POST",
            f"/api/v1/internal/report-outbox/{report_id}/recipients",
            body={"recipient_keys": recipient_keys},
        )
        if not isinstance(data, dict) or not isinstance(data.get("recipients"), dict):
            raise RuntimeError("core API returned unexpected recipient response")
        recipients = cast(dict[object, object], data["recipients"])
        return {str(key): str(value) for key, value in recipients.items()}

    async def mark_recipient_delivered(self, report_id: str, *, recipient_key: str) -> None:
        await self._request(
            "POST",
            f"/api/v1/internal/report-outbox/{report_id}/recipients/{recipient_key}/delivered",
        )

    async def mark_recipient_failed(
        self,
        report_id: str,
        *,
        recipient_key: str,
        error_code: str,
        error_message: str,
    ) -> None:
        await self._request(
            "POST",
            f"/api/v1/internal/report-outbox/{report_id}/recipients/{recipient_key}/failed",
            body={
                "error_code": error_code,
                "error_message": error_message,
            },
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        body: dict[str, object] | None = None,
    ) -> object:
        return await asyncio.to_thread(self._request_sync, method, path, query=query, body=body)

    def _request_sync(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None,
        body: dict[str, object] | None,
    ) -> object:
        url = f"{self.core_url}{path}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        payload = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Accept": "application/json"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if self.internal_token:
            headers["Authorization"] = f"Bearer {self.internal_token}"
        request = urllib.request.Request(url, data=payload, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"core API returned HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError("core API is unavailable") from exc
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))


class TelegramBotClient:
    def __init__(self, *, bot_token: str) -> None:
        from telegram import Bot  # type: ignore[import-not-found]

        self.bot = Bot(bot_token)

    async def send_message(self, *, chat_id: str, text: str) -> None:
        await self.bot.send_message(chat_id=chat_id, text=text)


class DryRunTelegramClient:
    async def send_message(self, *, chat_id: str, text: str) -> None:
        return None


def parse_allowed_ids(value: str) -> list[str]:
    return [part for part in re.split(r"[\s,;]+", value.strip()) if part]


def recipient_key_for_chat_id(
    chat_id: str,
    *,
    secret: str,
) -> str:
    return hmac.new(secret.encode("utf-8"), chat_id.encode("utf-8"), hashlib.sha256).hexdigest()


def resolve_bot_env_file() -> Path:
    if os.environ.get("BOT_ENV_FILE"):
        return Path(os.environ["BOT_ENV_FILE"])
    bot_dir = os.environ.get("DOS_AMIGOS_BOT_DIR", DEFAULT_BOT_DIR)
    return Path(bot_dir) / ".env"


def load_bot_env_file(path: Path) -> None:
    if not path.is_file():
        raise SystemExit("BOT_ENV_FILE is missing")
    load_dotenv(dotenv_path=path, override=False)


def safe_error_message(exc: BaseException) -> str:
    message = str(exc) or type(exc).__name__
    message = _AUTH_RE.sub("[redacted_auth]", message)
    message = _TOKEN_ASSIGNMENT_RE.sub("[redacted_secret]", message)
    message = _NUMERIC_ID_RE.sub("[redacted_id]", message)
    return message[:500]


def split_telegram_message(text: str, *, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    if limit <= 0:
        raise ValueError("limit must be positive")
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        split_at = remaining.rfind("\n\n", 0, limit + 1)
        if split_at <= 0:
            split_at = remaining.rfind("\n", 0, limit + 1)
        if split_at <= 0:
            split_at = limit
        chunk = remaining[:split_at].rstrip()
        if not chunk:
            chunk = remaining[:limit]
            split_at = limit
        chunks.append(chunk)
        remaining = remaining[split_at:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


async def run_once(
    *,
    config: ReportSenderConfig,
    core_client: ReportOutboxCoreClient,
    telegram_client: TelegramClient,
) -> ReportSenderResult:
    reports = await core_client.get_pending(
        limit=config.limit,
        business_date=config.business_date,
        include_failed=config.retry_failed,
    )
    if not reports:
        return ReportSenderResult(
            status="empty",
            considered=0,
            delivered=0,
            failed=0,
            messages_sent=0,
            dry_run=config.dry_run,
        )
    if config.dry_run:
        return ReportSenderResult(
            status="dry_run",
            considered=len(reports),
            delivered=0,
            failed=0,
            messages_sent=0,
            dry_run=True,
        )

    delivered = 0
    failed = 0
    messages_sent = 0
    for report in reports:
        recipient_keys = [
            recipient_key_for_chat_id(chat_id, secret=config.recipient_key_secret)
            for chat_id in config.allowed_ids
        ]
        recipient_statuses = await core_client.register_recipients(
            report.id,
            recipient_keys=recipient_keys,
        )
        report_failed = False
        failed_recipient_key: str | None = None
        try:
            chunks = split_telegram_message(report.payload_markdown)
            for chat_id, recipient_key in zip(config.allowed_ids, recipient_keys, strict=True):
                if recipient_statuses.get(recipient_key) == "delivered":
                    continue
                failed_recipient_key = recipient_key
                for chunk in chunks:
                    await telegram_client.send_message(chat_id=chat_id, text=chunk)
                    messages_sent += 1
                await core_client.mark_recipient_delivered(
                    report.id,
                    recipient_key=recipient_key,
                )
                recipient_statuses[recipient_key] = "delivered"
                failed_recipient_key = None
        except Exception as exc:
            report_failed = True
            if failed_recipient_key is not None:
                await core_client.mark_recipient_failed(
                    report.id,
                    recipient_key=failed_recipient_key,
                    error_code="telegram_delivery_failed",
                    error_message=safe_error_message(exc),
                )
            failed += 1
            await core_client.mark_failed(
                report.id,
                error_code="telegram_delivery_failed",
                error_message=safe_error_message(exc),
            )
        if not report_failed and all(
            recipient_statuses.get(recipient_key) == "delivered" for recipient_key in recipient_keys
        ):
            delivered += 1
            await core_client.mark_delivered(report.id)

    status = "delivered"
    if failed:
        status = "failed"
    return ReportSenderResult(
        status=status,
        considered=len(reports),
        delivered=delivered,
        failed=failed,
        messages_sent=messages_sent,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Send pending Dos Amigos reports to Telegram.")
    parser.add_argument("--once", action="store_true", help="Run one delivery pass and exit.")
    parser.add_argument("--date", help="Filter pending reports by business date YYYY-MM-DD.")
    parser.add_argument(
        "--dry-run", action="store_true", help="Read pending reports without sending."
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable summary.")
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Include failed reports in this explicit retry pass.",
    )
    parser.add_argument(
        "--limit", type=int, default=DEFAULT_LIMIT, help="Maximum reports to fetch."
    )
    return parser


async def async_main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_bot_env_file(resolve_bot_env_file())
    business_date = date.fromisoformat(args.date) if args.date else None
    allowed_ids = parse_allowed_ids(os.environ.get("ALLOWED_IDS", ""))
    if not allowed_ids:
        raise SystemExit("ALLOWED_IDS is required")
    recipient_key_secret = os.environ.get("REPORT_RECIPIENT_KEY_SECRET", "")
    if not recipient_key_secret:
        raise SystemExit("REPORT_RECIPIENT_KEY_SECRET is required")
    if len(recipient_key_secret) < MIN_RECIPIENT_KEY_SECRET_LENGTH:
        raise SystemExit("REPORT_RECIPIENT_KEY_SECRET must be at least 32 characters")

    config = ReportSenderConfig(
        allowed_ids=allowed_ids,
        recipient_key_secret=recipient_key_secret,
        business_date=business_date,
        dry_run=args.dry_run,
        retry_failed=args.retry_failed,
        limit=args.limit,
    )
    core_client = HttpReportOutboxClient(
        core_url=os.environ.get("CORE_URL", DEFAULT_CORE_URL),
        internal_token=os.environ.get("REPORT_OUTBOX_INTERNAL_TOKEN", ""),
    )
    if args.dry_run:
        telegram_client: TelegramClient = DryRunTelegramClient()
    else:
        bot_token = os.environ.get("BOT_TOKEN", "")
        if not bot_token:
            raise SystemExit("BOT_TOKEN is required")
        telegram_client = TelegramBotClient(bot_token=bot_token)

    result = await run_once(
        config=config,
        core_client=core_client,
        telegram_client=telegram_client,
    )
    if args.json:
        print(json.dumps(result.to_json_dict(), ensure_ascii=False))
    else:
        print(
            "status={status} considered={considered} delivered={delivered} "
            "failed={failed} messages_sent={messages_sent} dry_run={dry_run}".format(
                **result.to_json_dict()
            )
        )
    return 1 if result.status == "failed" else 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    sys.exit(main())
