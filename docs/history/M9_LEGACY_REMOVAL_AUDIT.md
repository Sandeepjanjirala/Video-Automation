# Telugu Reel Studio — Stage M9 Legacy Removal Audit & Inventory

**Date**: 2026-10-14  
**Baseline Tag**: `django-m8-complete` (`3466cc5`)  
**Objective**: Comprehensive classification of all legacy frameworks (React, TypeScript, FastAPI, SQLAlchemy, Alembic) across the entire codebase prior to removal.

---

## 1. Inventory & Classification Matrix

| Component / File / Directory | Technology | Classification | Justification & Action Plan |
|---|---|---|---|
| `frontend/app.tsx` | React 19 / TypeScript | **LEGACY REMOVE** | Obsolete React single-page application. Superseded by Django Templates (`templates/studio/`) + jQuery (`studio/static/js/`). Delete file. |
| `frontend/style.css` | CSS | **LEGACY REMOVE** | Obsolete stylesheet written for React components. Superseded by `studio/static/app.css`. Delete file. |
| `studio/static/app.js` | React / esbuild Bundle | **LEGACY REMOVE** | 74 KB compiled React production bundle. Delete file. Zero React bundles served. |
| `frontend/package.json` (`react`, `react-dom`, `typescript`, `esbuild`) | Node Dependencies | **LEGACY REMOVE** | Application build dependencies no longer required. Remove from `package.json`. |
| `frontend/package.json` (`jsdom`, `@testing-library/dom`, `jquery`) | Node Test Tooling | **TEST ONLY** | Required exclusively for headless browser automation test runners (`tests/jquery_ui_smoke.cjs`, `tests/m8_e2e_workflow_*.cjs`). Retain. |
| `config/urls.py` (`/react-legacy/`) | Django Routing | **LEGACY REMOVE** | Legacy React fallback route. Remove path definition and view handler. |
| `core/views.py` (`serve_ui`) | Django View | **LEGACY REMOVE** | Serves static React index.html. Remove from views. Root `/` exclusively serves `serve_m8_ui`. |
| `tests/browser_e2e_render.cjs` | React E2E Test | **TEST ONLY (OBSOLETE)** | Tests obsolete React bundle `studio/static/app.js`. Superseded by `tests/m8_e2e_workflow_a.cjs` and `tests/m8_e2e_workflow_b.cjs`. Delete or retire. |
| `tests/ui_smoke.cjs` | React Smoke Test | **TEST ONLY (OBSOLETE)** | Early smoke test for React DOM. Superseded by `tests/jquery_ui_smoke.cjs`. Delete or retire. |
| `studio/api.py` | FastAPI Application | **LEGACY REMOVE** | Complete FastAPI application and routers. Full 100% parity achieved by Django DRF in M1-M7. Delete file. |
| `run.py` (`--fastapi`, `STUDIO_SERVER=fastapi`) | Runtime CLI | **LEGACY REMOVE** | Remove FastAPI server execution branch and CLI argument. Keep standard Django startup. |
| `requirements.txt` (`fastapi`) | Python Dependency | **LEGACY REMOVE** | Remove `fastapi` from requirements. |
| `requirements.txt` (`uvicorn`) | Python Dependency | **ACTIVE REQUIRED / OPTIONAL** | Useful for ASGI production servers. Inspect whether Django ASGI or test runner uses it. |
| `tests/test_django_m1.py` (`test_fastapi_coexistence_and_accessibility`) | Pytest | **LEGACY REMOVE** | Coexistence check against port 8787. Remove as FastAPI port 8787 is retired. |
| `tests/test_django_m3.py` (FastAPI comparison tests) | Pytest | **TEST ONLY (CONVERT)** | Tests asserting exact parity by making HTTP requests to `:8787`. Convert to standalone Django contract validation tests without `:8787` dependency. |
| `tests/test_django_m4.py` (FastAPI media/thumbnail comparison tests) | Pytest | **TEST ONLY (CONVERT)** | Convert HTTP `:8787` comparisons to direct Django contract tests. |
| `tests/test_django_m6.py` (`test_fastapi_django_mutation_parity`) | Pytest | **TEST ONLY (CONVERT)** | Convert to pure Django mutation contract test. |
| `tests/test_workflow.py` (`from fastapi.testclient import TestClient`) | Pytest | **TEST ONLY (CONVERT)** | Convert to Django `APIClient` / `Client`. |
| `studio/store.py` (SQLAlchemy queries & sessions) | Persistence Layer | **ACTIVE REQUIRED -> MIGRATE** | Active store used by Celery tasks, dispatcher, and providers. Migrate to Django ORM (`core.models`) and Django database connection. |
| `studio/dispatcher.py` (SQLAlchemy session & queries) | Outbox Dispatcher | **ACTIVE REQUIRED -> MIGRATE** | Migrate to Django ORM (`core.models.OutboxEvent`) and Django connection. |
| `studio/ratelimit.py` (SQLAlchemy queries) | Usage Tracking | **ACTIVE REQUIRED -> MIGRATE** | Migrate to Django database connection or Django ORM. |
| `studio/importer.py` (SQLAlchemy engine & session) | Data Import Tool | **ACTIVE REQUIRED -> MIGRATE** | Migrate to Django ORM. |
| `studio/db/models.py` | SQLAlchemy Models | **LEGACY REMOVE** | Superseded by Django ORM models in `core/models.py`. Retire once store and dispatcher are migrated. |
| `studio/db/session.py` | SQLAlchemy Engine / Pool | **LEGACY REMOVE** | Superseded by Django connection pool and `django.db.connection`. Retire once store is migrated. |
| `alembic.ini` | Migration Tooling | **LEGACY REMOVE** | Alembic configuration file. Retire once Django baseline migration is applied and owns schema. |
| `studio/db/migrations/` | Alembic Version Scripts | **DOCUMENTATION/HISTORY -> RETIRE** | Historical Alembic migration versions (`0001`, `0002`, `0003`). Freeze and retire. Preserved in git tag `django-m8-complete`. |
| `requirements.txt` (`alembic`, `sqlalchemy`) | Python Dependencies | **LEGACY REMOVE** | Remove from `requirements.txt` after store migration and Django migration ownership transfer. |
| `THIRD_PARTY_NOTICES.txt` | Legal / Documentation | **DOCUMENTATION/HISTORY** | Documents historical packages. Update or keep for legal attribution. |
| `docs/*.md` | Documentation | **DOCUMENTATION/HISTORY** | Architectural documents and records of past milestones. Retain with updated status. |

---

## 2. Dependency Graph & Action Sequence

1. **Safety Checkpoint (Step 1)**: Verified backup `studio_pre_m9_20261014T142517Z.dump` created and verified in `studio_m9_disposable`.
2. **Legacy Frontend Removal (Steps 3-5)**:
   - Remove `frontend/app.tsx`, `frontend/style.css`, `studio/static/app.js`.
   - Remove `/react-legacy/` from `config/urls.py` and `core/views.py`.
   - Clean `frontend/package.json` to keep only Node test tools (`jsdom`, `@testing-library/dom`, `jquery`).
3. **FastAPI Removal (Steps 6-8)**:
   - Remove `studio/api.py`.
   - Remove `--fastapi` from `run.py`.
   - Convert FastAPI parity tests in `tests/test_django_m*.py` and `tests/test_workflow.py` to pure Django contract tests.
4. **Active Store Migration (Steps 9-10)**:
   - Migrate `studio/store.py`, `studio/dispatcher.py`, `studio/ratelimit.py`, and `studio/tasks.py` to use Django ORM (`core.models`) and `django.db.connection`.
   - Verify Celery workers and dispatcher boot and process work cleanly without importing SQLAlchemy.
5. **Django Schema Ownership Transfer (Steps 11-16)**:
   - Document exact PostgreSQL schema comparison against Django models.
   - Create Django baseline migration for `core`.
   - Test baseline migration against an empty disposable PostgreSQL database (`studio_m9_fresh`).
   - Run schema diff comparing fresh Django-created DB vs Alembic-created DB.
   - Test against restored copy `studio_m9_disposable`.
   - Establish Django baseline migration as applied to the live schema and switch `managed = True` on appropriate models.
   - Retire Alembic from active workflow.
6. **SQLAlchemy & Alembic Package Removal (Steps 18-19)**:
   - Remove `studio/db/session.py`, `studio/db/models.py`.
   - Remove `sqlalchemy` and `alembic` from `requirements.txt`.
7. **Verification & Real Video Reels (Steps 29-33)**:
   - Run complete regression suite (all tests passing).
   - Run `tests/jquery_ui_smoke.cjs`.
   - Execute real Workflow A & Workflow B rendering new 1080x1920 reels.
8. **Final Secret Audit, Commit & Tag (Steps 37-39)**:
   - Verify zero secrets in git diff.
   - Commit: `django: complete final architecture migration` and tag: `django-m9-complete`.
