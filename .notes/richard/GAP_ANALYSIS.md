# Production Readiness Gap Analysis

Re-evaluation of all existing plans after a fresh read of the codebase.
Issues are grouped by severity. Items marked **NEW** do not appear in any existing roadmap.

---

## Critical — fix before any external traffic

### G1 ★ NEW — Usage endpoints are completely unauthenticated

`GET /api/usage` and `DELETE /api/usage` (wired in both `frontend/routers/usage.py`
and `backend/routers/usage.py`) have no session check.  Any anonymous HTTP client can:

- Read token consumption and estimated API cost for every session.
- Reset the entire usage counter, destroying billing visibility.

**Fix**: add `X-Session-Id` validation to `frontend/routers/usage.py` (mirror the pattern
in `frontend/routers/lessons.py`).  The backend `/usage` route is loopback-only so the
BFF gate is sufficient; no backend change needed.

---

### G2 ★ NEW — `run_code` WS event has no rate limiting

`backend/routers/ws_session.py` dispatches `run_code` events to
`_handle_run_code(websocket, msg)` with `asyncio.create_task(...)` and no token check.
An authenticated WS client can fire hundreds of `run_code` events per second, each
spawning a subprocess (or a `uv run` Python-ML warmup).  The code runner's per-execution
timeout (10 – 60 s) limits individual jobs, but concurrent job count is unbounded, which
can exhaust the process pool and file descriptor table.

**Fix**: maintain a per-session `_code_run_semaphore` (e.g. `asyncio.Semaphore(3)`) in
`SessionState` and acquire it before spawning `_handle_run_code`.  Reject with an error
event if the semaphore cannot be acquired immediately.

---

### G3 ★ NEW — No rate limiting on `/auth/login` and `/auth/register`

The existing rate limiters (`_resend_limiter`, `_verify_limiter`) cover resend and verify
only.  `/auth/login` is completely unrestricted, enabling credential stuffing.
`/auth/register` is unrestricted, enabling mass account creation / email enumeration at
scale.

**Fix**:
- Login: `_login_limiter = RateLimiter(capacity=5.0, refill_rate=5/300.0)` keyed by
  normalised email (5 attempts per 5 minutes).
- Register: `_register_limiter = RateLimiter(capacity=10.0, refill_rate=10/3600.0)`
  keyed by IP (`request.client.host`), requiring `Request` as a parameter.

---

### G4 ★ NEW — `asyncio.timeout` on `asyncio.to_thread` leaks threads

The timeouts added to `run_turn`, `run_intro_turn`, and `decompose_pdf` in
`backend/services/agent.py` are wrapped with `asyncio.timeout()`.  When the timeout
fires, the Python *awaitable* is cancelled and `TimeoutError` propagates, but the
underlying OS thread running the synchronous LLM call **continues running** in the thread
pool — Python cannot cancel threads.  Under a sustained API stall (e.g. Anthropic outage),
every new turn spawns another stuck thread until the thread pool is exhausted (default
size: `min(32, os.cpu_count() + 4)`).

**Fix**: `asyncio.timeout` is still correct for propagating the error to the caller.  Add
a `threading.Event` cancellation signal to `TeachingAgent.run_turn()` and check it at
iteration boundaries (e.g. after each `stream.next()` chunk); set the event from the
timeout handler in an `except TimeoutError` block.  This is the only reliable way to
stop a thread in Python.  As an interim measure, log a warning and track the thread count
in the `app_state` so an operator can detect exhaustion.

---

## High — fix before inviting external users

### G5 ★ NEW — No session expiry

Sessions stored in the backend `sessions` table are never expired.  The `last_seen`
column is updated on every WS connection but no cleanup job runs.  A stolen session token
remains valid indefinitely.  Under the planned GDPR erasure (#19), sessions would be
cascade-deleted with the user, but a dormant account's sessions are never cleaned up.

**Fix**: add a startup background task (or extend the planned #19 retention job) that
runs nightly: `DELETE FROM sessions WHERE last_seen < datetime('now', '-30 days')`.
Make the TTL configurable via `SESSION_RETENTION_DAYS` env var.

---

### G6 ★ NEW — `lesson_id` not URL-encoded in WS proxy

`frontend/routers/ws_proxy.py` line 67–69 interpolates `lesson_id` directly into the
backend WS URL without URL-encoding:

```python
backend_url = (
    f"{settings.BACKEND_WS}/ws/{session_id}"
    f"?lesson_id={lesson_id}"
)
```

If `lesson_id` contains `&`, `#`, or `%`, extra query parameters could be injected into
the backend connection URL.  Lesson IDs are UUIDs (constrained by the BFF create flow),
but no validation enforces this at the WS layer.

**Fix**: use `urllib.parse.urlencode({"lesson_id": lesson_id})` for the query string, and
validate that `lesson_id` matches a UUID pattern before proxying.

---

### G7 ★ NEW — `run_code` code payload has no size limit

`code = msg.get("code", "")` in `_handle_run_code` has no upper bound.  The 50 MB
`tool_result` check added earlier covers that event type but `run_code` is a different
branch.  A client can submit arbitrarily large code strings.  The file written to
`tmpdir` could be hundreds of MB, and the compile step for C/Rust/TypeScript would
attempt to process it.

**Fix**: add `if len(code) > 100_000: ...` (100 KB is generous for a teaching exercise)
and return an error event before writing to disk.  Also cap code runner output: track
cumulative bytes yielded per execution and truncate after 1 MB.

---

### G8 ★ NEW — Deferred plan gap: session re-hydration does not recover `email`

The `DEFERRED_TASKS_ROADMAP.md` #6 plan describes a warm-cache fallback that calls
`GET /internal/sessions/{session_id}` to re-populate `SessionEntry` on BFF restart.
However, `SessionCreateResponse` (the response model for that endpoint) only returns
`session_id` and `user_id`.  It does not return `email`.

`SessionEntry.email` is returned by `GET /api/auth/me`.  After a BFF restart, re-hydrated
entries would have `email = ""`, breaking the `/auth/me` endpoint for existing sessions.

**Fix (two options)**:
- A: Extend `SessionCreateResponse` and `GET /internal/sessions/{id}` to also return
  `email` (requires a JOIN with the `users` table in the backend).
- B: Make `GET /auth/me` fetch email from the backend lazily if `entry.email` is empty
  (same pattern as `_get_user_id` in `frontend/routers/lessons.py`).

Option B is lower risk and matches the existing pattern.

---

### G9 ★ NEW — No password reset flow

There is no `POST /auth/forgot-password` or `POST /auth/reset-password` endpoint.  A
user who forgets their password has no self-service recovery path.  This is a support
burden and a blocker for general availability.

The existing verification token infrastructure (`email_verifications` table,
`create_verification_token`, `consume_verification_token`) can be reused with a different
token type.

**Fix**:
1. Add `token_type TEXT NOT NULL DEFAULT 'verify'` to `email_verifications` (or a
   separate `password_reset_tokens` table).
2. `POST /auth/forgot-password { email }` — generate token, send email, always return 200
   (don't reveal whether email exists; same pattern as resend).
3. `POST /auth/reset-password { token, new_password }` — consume token, hash password,
   `UPDATE users SET password_hash = ? WHERE id = ?`.

---

### G10 — Internal API has no shared secret between BFF and backend

The backend is bound to `127.0.0.1:8001` (loopback only, production-appropriate).  But
there is no shared secret or mTLS between BFF and backend.  If the backend is ever
accidentally exposed (misconfigured reverse proxy, firewall rule, or future containerised
deployment where loopback isolation doesn't hold), all `/internal/` routes — including
password hash retrieval (`GET /internal/auth/user`) — are wide open.

The existing roadmap mentions this under "prototype assumptions" but doesn't plan a fix.

**Fix**: add a `BACKEND_SHARED_SECRET` env var; BFF sends it as an `X-Internal-Token`
header; backend validates it in a middleware and returns 403 if missing/wrong.  This is
a one-day change with high security value.

---

## Medium — fix before general availability

### G11 ★ NEW — Error messages leak internal state

`backend/routers/lessons.py:68`:
```python
raise HTTPException(status_code=500, detail=f"PDF render error: {exc}")
```
The raw Python exception (which can include file paths, module names, line numbers) is
sent to the client.  Similar patterns may exist in other exception handlers.

**Fix**: log the full exception server-side, return a generic message to the client:
`"PDF render error — please try again"`.  Audit all `HTTPException(status_code=5xx)`
calls and ensure they don't embed `str(exc)`.

---

### G12 ★ NEW — Rate limiters reset on BFF restart

`_resend_limiter` and `_verify_limiter` in `frontend/routers/auth.py` are module-level
singletons, destroyed when the BFF process restarts.  An attacker who triggers a restart
(or simply waits for a deploy) immediately regains their full rate limit budget.

**Fix**: persist rate-limit state to the backend DB (a `rate_limits` table keyed by
`(key, limiter_name)` with `tokens` and `last_refill` columns), or — simpler for
now — accept the limitation and document it, since rate limiters also exist in the
existing session limiter and the same problem applies there.  This is the same
architectural gap as #6 (in-memory store) and shares the same Redis fix.

---

### G13 ★ NEW — No per-user storage quota

A user can upload unlimited PDFs.  Each PDF is stored under `STORAGE_DIR/{user_id}/pdfs/`.
There is no check on the total storage consumed per user.  A user with generous disk space
could fill the server volume.

**Fix**: track cumulative storage per user.  Simplest approach: count rows in `lessons`
where `pdf_path IS NOT NULL` and enforce a per-user lesson limit (e.g. 50 active lessons).
For byte-level enforcement, store `pdf_size_bytes` in the `lessons` table and sum it per
user before accepting uploads.

---

### G14 ★ NEW — Code runner AST check can be bypassed

The `_ast_check` in `backend/services/code_runner.py` blocks direct `import os` and
`eval()` calls, but several bypass patterns work in Python:

- `importlib.import_module("os")` — `importlib` is not in `_BLOCKED_MODULES`
- `__builtins__["eval"](...)` — attribute access, not a bare call to `eval`
- `getattr(__builtins__, "exec")(...)` — `getattr` is not blocked
- Dynamic string construction: `mod = "o"+"s"; __import__(mod)` — AST check sees no `import os`

The bubblewrap sandbox is the real defence; the AST check is belt-and-suspenders.  But
advertising the AST check as a security layer is misleading.

**Fix**: document that the AST check is a convenience filter, not a security boundary.
The security boundary is bwrap.  Add a prominent startup warning if bwrap is not found
(currently just a log warning that is easily missed).

---

### G15 — Deferred plan gap: #6 warm-cache fallback doesn't cover all BFF routers

The deferred plan lists `frontend/routers/lessons.py` and a few others, but
`frontend/routers/ws_proxy.py` also calls `store.get(session_id)` directly at line 52
and returns 4001 immediately.  WS connections after a BFF restart will fail.

`frontend/routers/personas.py` and `voices.py` may have similar patterns.

**Fix**: the shared `require_session` async helper from the deferred plan must be applied
to ALL BFF routers that call `store.get()`, including the WS proxy.  The WS upgrade
path makes this slightly more complex (can't use HTTP 401 after the WS is accepted);
the check must run before `await websocket.accept()` or return a WS close code.

---

## Low / Observations

### G16 — `asyncio.timeout` on `tool_result` wait

`run_turn` now times out at 120 s.  If the agent is waiting on a `tool_result`
(the student is drawing on the sketchpad), the 120 s timeout will fire and cancel the
agent's turn.  Long drawing sessions or paused exercises will hit this.  Consider
separating the "LLM API call" timeout from the "waiting for human input" timeout, or
making the timeout configurable per-turn-type.

---

### G17 — `backend/main.py` docs endpoint is open

`backend/main.py` sets `docs_url="/docs"` and notes "no public docs in production".
But the backend is loopback-only anyway, so this is only exploitable if the backend is
accidentally exposed (see G10).  If G10 is fixed (shared secret), this becomes moot.
Otherwise, set `docs_url=None` in production mode.

---

### G18 — `UploadToken.ttl_seconds` is caller-controlled

`POST /internal/upload_tokens` accepts `ttl_seconds: int = 300` from the BFF.
The BFF always passes the default, but nothing prevents passing `ttl_seconds=86400`
(24 hours) or even a very large value.  The token is consumed on first use, so this is
low risk, but the backend should cap the TTL regardless.

**Fix**: `ttl_seconds = min(body.ttl_seconds, 600)` in the backend handler.

---

## Summary of what the existing roadmap misses

| Gap | Severity | In roadmap? |
|-----|----------|-------------|
| G1 — Usage endpoints unauthenticated | Critical | ❌ Missing |
| G2 — `run_code` no rate limiting | Critical | ❌ Missing |
| G3 — Login/register no rate limiting | Critical | ❌ Missing |
| G4 — `asyncio.timeout` leaks threads | Critical | ❌ Missing |
| G5 — No session expiry | High | ❌ Missing |
| G6 — `lesson_id` URL injection in WS proxy | High | ❌ Missing |
| G7 — `run_code` code size unbounded | High | ❌ Missing |
| G8 — Session re-hydration loses email | High | ❌ Deferred plan gap |
| G9 — No password reset | High | ❌ Missing |
| G10 — No BFF↔backend shared secret | High | ⚠️ Mentioned but unplanned |
| G11 — Internal error messages leak paths | Medium | ❌ Missing |
| G12 — Rate limiters reset on restart | Medium | ⚠️ Same root cause as #6 |
| G13 — No per-user storage quota | Medium | ❌ Missing |
| G14 — AST check bypassable | Medium | ❌ Missing |
| G15 — Warm-cache fallback incomplete coverage | Medium | ❌ Deferred plan gap |
| G16 — Timeout fires during human-input wait | Low | ❌ Missing |
| G17 — Docs endpoint open if backend exposed | Low | ❌ Missing |
| G18 — Upload token TTL caller-controlled | Low | ❌ Missing |

---

## Revised execution order

Incorporating the gaps above into the deferred task sequence:

| Step | Work | Replaces / extends |
|------|------|--------------------|
| 1 | Fix G1 (authenticate /usage) | New, 30 min |
| 2 | Fix G3 (rate-limit login/register) | New, 2 hrs |
| 3 | Fix G6 (URL-encode lesson_id in WS proxy) | New, 30 min |
| 4 | Fix G7 (code + output size limits) | New, 1 hr |
| 5 | Fix G2 (run_code semaphore) | New, 1 hr |
| 6 | Fix G9 (password reset flow) | New, 4 hrs |
| 7 | Fix G10 (BFF↔backend shared secret) | New, 2 hrs |
| 8 | Fix G5 (session expiry cleanup) | Extend #19 plan |
| 9 | Fix G11, G17, G18 (minor hardening) | New, 2 hrs total |
| 10 | Fix G4 (thread cancellation signal) | Extends #21 |
| 11 | Implement #6 (session warm-cache fallback) + G8 + G15 | Deferred #6 |
| 12 | Implement #22 (audit log) | Deferred #22 |
| 13 | Implement #10 (RBAC) | Deferred #10 |
| 14 | Implement #19 (GDPR erasure + retention) | Deferred #19 |
| 15 | Decide/implement #20 (encryption at rest) | Deferred #20 |

---

*Created: 2026-03-14. Based on full read of backend/, frontend/, and existing roadmap docs.*
