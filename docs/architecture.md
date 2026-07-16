# Architecture

```text
Telegram / jobs
      |
      v
Hermes Gateway
      |
      v
Dos Amigos Core API
  - integrations
  - persistence
  - calculations
  - audit
  - permissions
      |
      +-- PostgreSQL
      +-- iiko adapters
      +-- report adapters
```

Hermes does not access PostgreSQL directly.

## Data layers

1. Raw: immutable source responses with hashes and trace IDs.
2. Canonical: normalized business entities.
3. Management: deterministic metrics, alerts, tasks, and reports.

Sprint 1 implements the service boundary and raw layer.
