# Production Readiness Roadmap

Issues are ordered by priority. Phases 1–2 must be done before any public exposure.
Phase 3 before general availability. Phase 4 is ongoing maintenance.

---

## Phase 1 — Blockers (complete before any external traffic)

These were identified as **Critical** severity and are now fixed in the codebase.

| # | Issue | Status | Files |
|---|-------|--------|-------|
| 1 | **Authorization bypass** — lesson GET/PATCH/DELETE/save endpoints had no ownership check; any authenticated user could read/modify/delete any other user's lesson | ✅ Fixed | `backend/routers/lessons.py`, `frontend/routers/lessons.py` |
| 2 | **PDF image endpoint unauthenticated** — `GET /lessons/{id}/page/{n}` had no ownership check; combined with #1 allowed full PDF exfiltration | ✅ Fixed | `backend/routers/lessons.py`, `frontend/routers/lessons.py`, `client/src/components/SlideViewer.tsx` |
| 3 | **WebSocket ownership bypass** — WS handler validated the session but not that `lesson.user_id == session.user_id` | ✅ Fixed | `backend/routers/ws_session.py` |
| 4 | **Path traversal in PDF render** — `pdf_full_path` was not canonicalized; no check it stayed within `STORAGE_DIR` | ✅ Fixed | `backend/routers/lessons.py` |

---

## Phase 2 — High Priority (complete before beta / inviting any external users)

| # | Issue | Severity | Category | Status | Notes |
|---|-------|----------|----------|--------|-------|
| 5 | **Dynamic SQL in `update_lesson`** — f-string column name construction; whitelist is fragile and could be bypassed during refactors | High | Security | ✅ Fixed | `backend/db/models.py` — replaced with `_LESSON_UPDATE_SQL` dict of static per-field statements |
| 6 | **In-memory session store** — all sessions lost on server restart; incompatible with multi-instance deploy | High | Reliability | ⏳ Deferred | `frontend/session_store.py` — replace with Redis or DB-backed store |
| 7 | **Wildcard CORS default** — `ALLOWED_ORIGINS` env var defaults to `"*"` if unset; CSRF risk | High | Security | ✅ Fixed | `frontend/main.py` — raises `RuntimeError` on startup if `ENV=production` and ALLOWED_ORIGINS is `*` |
| 8 | **No rate limiting on auth endpoints** — `/auth/resend` and `/auth/verify` accept unlimited attempts; enables email enumeration and token brute-force | High | Security | ✅ Fixed | `frontend/routers/auth.py` — `_resend_limiter` (3/hr per email), `_verify_limiter` (10/min per token prefix) |
| 9 | **Weak email validation regex** — regex accepts many invalid addresses, no RFC 5322 compliance | High | Data Quality | ✅ Fixed | `frontend/routers/auth.py` — replaced with `email-validator` package (`validate_email()`) |
| 10 | **No RBAC** — no admin vs. user distinction; any user could delete built-in personas if the endpoint check were ever weakened | High | Security | ⏳ Deferred | Plan roles before user growth makes retro-fitting painful |

---

## Phase 3 — Production Hardening (complete before general availability)

| # | Issue | Severity | Category | Status | Notes |
|---|-------|----------|----------|--------|-------|
| 11 | **No input validation on tool results** — client-supplied tool results (drawings, video frames) accepted without size or format checks | Medium | Reliability / DoS | ✅ Fixed | `backend/routers/ws_session.py` — 50 MB limit enforced on raw `tool_result` payload before dispatch |
| 12 | **No backend WebSocket frame size limit** — frontend sets 4 MB, backend didn't set one | Medium | DoS | ✅ Fixed | `backend/main.py` — `ws_max_size=4*1024*1024` on uvicorn |
| 13 | **HTTPS not enforced** — no HSTS header, no HTTP→HTTPS redirect | Medium | Security | ✅ Fixed | `frontend/main.py` — `Strict-Transport-Security: max-age=31536000; includeSubDomains` via `_SecurityHeadersMiddleware` |
| 14 | **Content-Security-Policy missing** — no CSP header set; reduces XSS protection | Low → Medium | Security | ✅ Fixed | `frontend/main.py` — `Content-Security-Policy` header added in `_SecurityHeadersMiddleware` |
| 15 | **No X-Content-Type-Options header** — MIME sniffing not disabled | Low | Security | ✅ Fixed | `frontend/main.py` and `backend/main.py` — `X-Content-Type-Options: nosniff` on both apps |
| 16 | **No pagination on lesson listing** — `list_lessons` returns all records; DoS risk at scale | Medium | Reliability | ✅ Fixed | `backend/db/models.py` — `LIMIT`/`OFFSET` params (default 50, max 200); `backend/routers/lessons.py` exposes `limit`/`offset` query params |
| 17 | **Persona instructions injected raw into LLM prompt** — no length cap or sanitisation; prompt injection attack surface | Medium | Security | ✅ Fixed | `backend/routers/personas.py` — 1 000-char limit enforced in `create_persona` |
| 18 | **No rate limiting on verification token attempts** — token brute-force possible (tokens are 32-byte secrets so slow, but still) | Medium | Security | ✅ Fixed | `frontend/routers/auth.py` — `_verify_limiter` covers this (same fix as #8) |
| 19 | **No data retention / GDPR right-to-erasure** — lessons and messages are kept indefinitely | Medium | Compliance | ⏳ Deferred | Scheduled cleanup job; cascade-delete endpoint; data export for GDPR portability |
| 20 | **No encryption at rest** — SQLite DB and PDFs stored unencrypted | Medium | Security | ⏳ Deferred | Evaluate SQLCipher or filesystem-level encryption; document key management |
| 21 | **No request timeouts** — PDF decomposition and `run_turn()` can hang forever | Low | Reliability | ✅ Fixed | `backend/services/agent.py` — `asyncio.timeout(300)` on decompose; `asyncio.timeout(120)` per turn and intro turn |
| 22 | **No audit log** — no record of who created/deleted lessons, changed passwords, verified email | Low | Compliance | ⏳ Deferred | Add `audit_events` table; log sensitive operations with `user_id`, `action`, `timestamp` |

---

## Phase 4 — Ongoing / Maintenance

| # | Issue | Category | Notes |
|---|-------|----------|-------|
| 23 | **No dependency vulnerability scanning** | Security | Add `pip-audit` and `npm audit` to CI; set up Dependabot |
| 24 | **No request ID correlation** | Observability | Generate `X-Request-ID` on the BFF; propagate to backend; include in all log lines |
| 25 | **Hardcoded config defaults** | Configuration | Fail-fast on startup if security-sensitive env vars are absent in non-dev environments |
| 26 | **No database backup strategy** | Operations | Daily copy of `db.sqlite3` to object storage; document point-in-time recovery procedure |
| 27 | **Inefficient N+1 queries on lesson load** | Performance | `get_lesson` + `get_sections` + `get_messages` = 3 round-trips; consolidate with JOINs |
| 28 | **No API versioning** | Maintainability | Prefix all routes with `/api/v1/`; plan breaking-change policy before first external client |
| 29 | **No E2E integration tests** | QA | Cross-user access tests (user A cannot reach user B's lesson); WS reconnect scenarios |
| 30 | **No security test suite** | QA | Automated tests for auth bypass, CSRF, oversized payloads |
| 31 | **No privacy policy / ToS** | Legal / Compliance | Document data collected, retention period, deletion rights before onboarding users |
| 32 | **TypeScript `any` types** | Code Quality | Enable `strict: true` in `tsconfig.app.json`; remove remaining `any` annotations |

---

## Quick-reference: env vars to set before production

```
ANTHROPIC_API_KEY=...          # never commit; use secrets manager
ALLOWED_ORIGINS=https://yourdomain.com
ENV=production                 # enables CORS/wildcard fail-fast guard
STORAGE_DIR=/data/storage
DB_PATH=/data/storage/db.sqlite3
BACKEND_HOST=127.0.0.1
FRONTEND_ORIGIN=https://yourdomain.com
```

---

*Last updated: 2026-03-14. Phase 2 + Phase 3 (all except 6, 10, 19, 20, 22) completed.*
