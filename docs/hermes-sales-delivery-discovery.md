# Hermes Sales Delivery Discovery

## Status

`HERMES_DELIVERY_MODE=disabled` is the only safe production default for S1.6.1a.

Dos Amigos Core now creates deterministic sales report outbox records, but the real Hermes delivery contract is not confirmed.

## Ready Data

Each `sales_daily` outbox record contains:

- `payload_json` with schema version `1.0`, Decimal values as strings, source checksum, warnings, payments, and top products.
- `payload_markdown` with a Russian human-readable daily sales report.
- `idempotency_key` in the form `sales_daily:{organization_id}:{business_date}:{source_checksum}`.

No guest data, comments, tokens, passwords, or raw iiko payloads are included.

## Contract To Confirm

Before S1.6.1b can enable real delivery, confirm:

- Whether Hermes pulls from Core or Core pushes to Hermes.
- Authentication and authorization mechanism.
- Exact endpoint, queue, file, or database contract.
- Idempotency handling on Hermes side.
- Delivery acknowledgement shape.
- Error and retry semantics.
- Message length and formatting limits.

## Possible Models

- Pull model: Hermes calls a read-only Core endpoint to fetch pending reports.
- Push model: Core calls a documented Hermes endpoint.
- Shared queue model: both systems use an explicitly approved queue.

## Forbidden Until Confirmed

- Do not invent a Hermes HTTP endpoint.
- Do not call Telegram APIs directly.
- Do not read production Hermes secrets.
- Do not write into `/opt/hermes` or `/opt/hermes-bots`.
- Do not open new external ports.
- Do not disable TLS.
- Do not change firewall or production Hermes deployment.

## S1.6.1b Stop Condition

Keep delivery disabled if the real Hermes contract, authentication, or acknowledgement flow is not proven without reading or exposing production secrets.
