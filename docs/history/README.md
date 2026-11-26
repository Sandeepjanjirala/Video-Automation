# Project history

Records kept from the October–November 2026 rebuild, when the studio moved from FastAPI, React,
SQLite and Alembic to Django, Django templates with jQuery, and PostgreSQL. They describe stages
and components that no longer exist and are not setup instructions; the current setup is in the
[README](../../README.md).

| Document | What it records |
|---|---|
| [DJANGO_MIGRATION_PLAN.md](DJANGO_MIGRATION_PLAN.md) | The staged migration plan (M1–M8) and what each stage verified |
| [M8_FRONTEND_PARITY.md](M8_FRONTEND_PARITY.md) | Feature parity between the old React frontend and the Django templates |
| [M9_LEGACY_REMOVAL_AUDIT.md](M9_LEGACY_REMOVAL_AUDIT.md) | The inventory of legacy code before its removal |
| [M9_SCHEMA_DIFF.md](M9_SCHEMA_DIFF.md) | Alembic schema compared with the Django migrations |
| [CUTOVER_AND_ROLLBACK.md](CUTOVER_AND_ROLLBACK.md) | The SQLite to PostgreSQL cutover and rollback runbook |
| [UPGRADE_RULES.md](UPGRADE_RULES.md), [UPGRADE_STATUS.md](UPGRADE_STATUS.md) | Working rules and status notes during the upgrade |
| [AUDIT_2026-10-30.md](AUDIT_2026-10-30.md) | A full audit and repair pass of the Django version |
| [codex_prompts/](codex_prompts/) | Task briefs written for an AI coding assistant during the C1–C5 phases |
