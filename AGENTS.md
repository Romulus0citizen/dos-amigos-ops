# AGENTS.md

## Purpose

This is the deterministic Dos Amigos operational core, not a general chatbot repository.

## Boundaries

- Hermes: dialogue, routing, explanations, approvals UI.
- Dos Amigos Core: integrations, persistence, calculations, audit, permissions.
- iiko: external source system.
- Google Drive: documents and reports, not a transactional database.

## Rules

1. Never commit secrets or unredacted production payloads.
2. iiko is read-only until an explicit approval boundary is implemented and tested.
3. The LLM must not calculate or overwrite official financial facts.
4. Every sync must be idempotent and auditable.
5. Raw and canonical data must remain separate.
6. Production Hermes must not be modified without an approved deployment plan.
7. Use branches and pull requests.
8. Add tests with implementation.
9. Update ADRs when architecture changes.
10. Log trace IDs and statuses, never credentials.
