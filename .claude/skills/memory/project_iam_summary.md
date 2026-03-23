---
name: IAM implementation summary
description: Backend + BFF + UI IAM (admin management) — what was added and where
type: project
---

Phase 1 + 2 complete. 191 tests passing, 1 skipped.

**Why:** Admin seeding was hardcoded; added config-driven ADMIN_EMAILS, shared authz, and full promote/demote UI.

**How to apply:** Use `require_admin` from `backend/authz.py` for any new admin-only backend endpoints. Use `require_admin_session` from `frontend/routers/admin_guard.py` for BFF admin routes.

## Backend
- `backend/config.py` — `ADMIN_EMAILS` parsed from env (comma-sep, normalized)
- `backend/authz.py` — `require_admin`, shared authz helpers
- `backend/db/models.py:647` — `list_users_for_admin_iam`, `count_admin_users`, `set_user_admin`; `seed_admin_users(conn, settings.ADMIN_EMAILS)` (grant-only)
- `backend/routers/iam.py` — `GET /admin/iam/users`, `PATCH /admin/iam/users/{id}/admin`; 404/409 for missing/self/last-admin; `bootstrap_managed` flag + `warning` metadata
- `backend/main.py` — includes `iam.router`; calls `seed_admin_users(conn, settings.ADMIN_EMAILS)`

## BFF
- `frontend/routers/admin_guard.py` — `require_admin_session`: freshness re-validation against backend
- `frontend/routers/iam.py` — thin proxy for IAM endpoints; forwards authoritative `actor_user_id`
- `frontend/routers/courses.py`, `lessons.py`, `usage.py` — use fresh admin guard, forward actor user id
- `frontend/main.py:113` — includes IAM router

## Client
- `client/src/pages/IamPage.tsx:29` — user table, promote/demote, demotion confirmation, self-demote disabled, warning banner
- `client/src/App.tsx:134` — routed `/admin/iam`
- `client/src/pages/HomePage.tsx:134` — IAM nav button (admin-only, next to Usage)
- `client/src/lib/types.ts:131` — IAM types
