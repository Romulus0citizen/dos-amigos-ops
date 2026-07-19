from __future__ import annotations

import os
from datetime import date

import pytest

from ops.telegram.report_sender import (
    ReportOutboxItem,
    ReportSenderConfig,
    load_bot_env_file,
    run_once,
    split_telegram_message,
)


class FakeCoreClient:
    def __init__(self, reports: list[ReportOutboxItem]) -> None:
        self.reports = {report.id: report for report in reports}
        self.statuses = {report.id: "pending" for report in reports}
        self.pending_calls: list[tuple[date | None, bool]] = []
        self.delivered_ids: list[str] = []
        self.failed: list[tuple[str, str, str]] = []

    async def get_pending(
        self,
        *,
        limit: int,
        business_date: date | None,
        include_failed: bool,
    ) -> list[ReportOutboxItem]:
        self.pending_calls.append((business_date, include_failed))
        return [
            report
            for report in self.reports.values()
            if (
                self.statuses[report.id] == "pending"
                or (include_failed and self.statuses[report.id] == "failed")
            )
            and (business_date is None or report.business_date == business_date)
        ][:limit]

    async def mark_delivered(self, report_id: str) -> None:
        self.statuses[report_id] = "delivered"
        self.delivered_ids.append(report_id)

    async def mark_failed(
        self,
        report_id: str,
        *,
        error_code: str,
        error_message: str,
    ) -> None:
        self.statuses[report_id] = "failed"
        self.failed.append((report_id, error_code, error_message))


class FakeTelegramClient:
    def __init__(self, *, fail_chat_id: str | None = None) -> None:
        self.fail_chat_id = fail_chat_id
        self.sent: list[tuple[str, str]] = []

    async def send_message(self, *, chat_id: str, text: str) -> None:
        self.sent.append((chat_id, text))
        if chat_id == self.fail_chat_id:
            raise RuntimeError(f"telegram chat {chat_id} failed")


def report_item(report_id: str = "report-1") -> ReportOutboxItem:
    return ReportOutboxItem(
        id=report_id,
        business_date=date(2026, 7, 16),
        payload_markdown="Dos Amigos — итоги 16.07.2026",
        delivery_attempts=0,
    )


async def test_sender_delivers_to_two_allowed_ids_and_marks_delivered() -> None:
    core = FakeCoreClient([report_item()])
    telegram = FakeTelegramClient()
    config = ReportSenderConfig(allowed_ids=["1001", "1002"])

    result = await run_once(config=config, core_client=core, telegram_client=telegram)

    assert result.status == "delivered"
    assert result.considered == 1
    assert result.delivered == 1
    assert result.failed == 0
    assert result.messages_sent == 2
    assert telegram.sent == [
        ("1001", "Dos Amigos — итоги 16.07.2026"),
        ("1002", "Dos Amigos — итоги 16.07.2026"),
    ]
    assert core.delivered_ids == ["report-1"]
    assert core.failed == []


async def test_sender_repeated_run_does_not_resend_delivered_report() -> None:
    core = FakeCoreClient([report_item()])
    telegram = FakeTelegramClient()
    config = ReportSenderConfig(allowed_ids=["1001", "1002"])

    first = await run_once(config=config, core_client=core, telegram_client=telegram)
    second = await run_once(config=config, core_client=core, telegram_client=telegram)

    assert first.status == "delivered"
    assert second.status == "empty"
    assert len(telegram.sent) == 2
    assert core.delivered_ids == ["report-1"]


async def test_sender_marks_failed_when_one_recipient_fails_without_leaking_chat_id() -> None:
    core = FakeCoreClient([report_item()])
    telegram = FakeTelegramClient(fail_chat_id="1002")
    config = ReportSenderConfig(allowed_ids=["1001", "1002"])

    result = await run_once(config=config, core_client=core, telegram_client=telegram)

    assert result.status == "failed"
    assert result.delivered == 0
    assert result.failed == 1
    assert core.delivered_ids == []
    assert len(core.failed) == 1
    assert core.failed[0][0] == "report-1"
    assert core.failed[0][1] == "telegram_delivery_failed"
    assert "1002" not in core.failed[0][2]


async def test_sender_retries_failed_report_only_with_explicit_retry_failed_flag() -> None:
    core = FakeCoreClient([report_item()])
    failing_telegram = FakeTelegramClient(fail_chat_id="1002")
    normal_config = ReportSenderConfig(allowed_ids=["1001", "1002"])

    first = await run_once(
        config=normal_config,
        core_client=core,
        telegram_client=failing_telegram,
    )
    second = await run_once(
        config=normal_config,
        core_client=core,
        telegram_client=FakeTelegramClient(),
    )
    retry = await run_once(
        config=ReportSenderConfig(allowed_ids=["1001", "1002"], retry_failed=True),
        core_client=core,
        telegram_client=FakeTelegramClient(),
    )
    after_success = await run_once(
        config=ReportSenderConfig(allowed_ids=["1001", "1002"], retry_failed=True),
        core_client=core,
        telegram_client=FakeTelegramClient(),
    )

    assert first.status == "failed"
    assert second.status == "empty"
    assert retry.status == "delivered"
    assert after_success.status == "empty"
    assert list(core.reports) == ["report-1"]
    assert core.delivered_ids == ["report-1"]
    assert core.statuses == {"report-1": "delivered"}


async def test_sender_dry_run_sends_nothing_and_does_not_mutate_outbox() -> None:
    core = FakeCoreClient([report_item()])
    telegram = FakeTelegramClient()
    config = ReportSenderConfig(allowed_ids=["1001", "1002"], dry_run=True)

    result = await run_once(config=config, core_client=core, telegram_client=telegram)

    assert result.status == "dry_run"
    assert result.dry_run is True
    assert result.considered == 1
    assert result.messages_sent == 0
    assert telegram.sent == []
    assert core.delivered_ids == []
    assert core.failed == []
    assert core.statuses == {"report-1": "pending"}


def test_split_telegram_message_keeps_chunks_under_limit() -> None:
    chunks = split_telegram_message("A" * 4100, limit=4096)

    assert len(chunks) == 2
    assert all(len(chunk) <= 4096 for chunk in chunks)


def test_load_bot_env_file_loads_without_overriding_existing_values(tmp_path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("BOT_TOKEN=from-file\nALLOWED_IDS=1001,1002\n", encoding="utf-8")
    monkeypatch.setenv("BOT_TOKEN", "already-set")
    monkeypatch.delenv("ALLOWED_IDS", raising=False)

    load_bot_env_file(env_file)

    assert os.environ["BOT_TOKEN"] == "already-set"
    assert os.environ["ALLOWED_IDS"] == "1001,1002"


def test_load_bot_env_file_missing_file_fails_safely(tmp_path) -> None:
    with pytest.raises(SystemExit) as exc_info:
        load_bot_env_file(tmp_path / "missing.env")

    assert str(exc_info.value) == "BOT_ENV_FILE is missing"
