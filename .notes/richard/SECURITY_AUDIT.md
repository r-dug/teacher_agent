# Security Audit — pdf-to-audio

**Auditor**: Claude Sonnet 4.6 (cybersecurity review)
**Date**: 2026-03-14
**Scope**: All planning documents in `.notes/`, all source code in `backend/`, `frontend/`, `shared/`, and `client/`
**Methodology**: Static analysis of source code + review of existing gap analysis and deployment roadmap

This audit independently verifies the gaps already captured in `GAP_ANALYSIS.md` and
`PRODUCTION_ROADMAP.md`, adds new findings those documents missed, and evaluates the
deployment plan in `DEPLOYMENT_ROADMAP.md` for additional exposure.

---

## Executive Summary

The codebase has had a meaningful first-pass security hardening (Phases 1–3 of
`PRODUCTION_ROADMAP.md`). Authorization bypasses, path traversal, SQL injection, and
several DoS vectors are fixed. What remains is a cluster of medium-to-critical issues
concentrated in three areas:

1. **Missing authentication on operational endpoints** (usage API, WS auth timing)
2. **Unbounded resource consumption** in the code runner (no semaphore, no code size cap)
3. **Deployment configuration gaps** (no BFF↔backend shared secret, CSP weaknesses, docs exposed)

None of the pre-existing critical issues (G1–G4 in GAP_ANALYSIS.md) appear fixed in the
code at time of audit. They must be resolved before public traffic.

---

## Part 1 — Verification of Known Issues

### ✅ CONFIRMED CRITICAL: G1 — Usage endpoints unauthenticated

**Code location**: [frontend/routers/usage.py](../../../frontend/routers/usage.py)

```python
@router.get("/usage")
async def get_usage():      # ← no session check
    http = get_http()
    resp = await http.get("/usage")
    ...

@router.delete("/usage")
async def reset_usage():    # ← no session check
    http = get_http()
    resp = await http.delete("/usage")
    ...
```

Any anonymous HTTP client can read API token consumption and billing data for the entire
server, or reset it, destroying billing visibility. This is publicly routed at `GET /api/usage`
and `DELETE /api/usage`.

**Impact**: Information disclosure (billing data), integrity loss (usage counter).
**Fix**: Add `X-Session-Id` validation mirroring `frontend/routers/lessons.py`.

---

### ✅ CONFIRMED CRITICAL: G2 — `run_code` unlimited concurrency + G7 — no code size limit

**Code location**: [backend/routers/ws_session.py:441-442](../../../backend/routers/ws_session.py#L441-L442) and [L458](../../../backend/routers/ws_session.py#L458)

```python
elif event == "run_code":
    asyncio.create_task(_handle_run_code(websocket, msg))  # ← no semaphore

async def _handle_run_code(websocket: WebSocket, msg: dict) -> None:
    inv_id = msg.get("invocation_id", "")
    code = msg.get("code", "")           # ← no size cap
    runtime = msg.get("runtime", "python")
```

An authenticated WS client can fire an unbounded number of `run_code` events
simultaneously. Each spawns a subprocess (or a `uv run` warmup for python-ml). There is no
check on `len(code)` before writing it to disk and passing it to a compiler or interpreter.

A 100 MB code string submitted to the `rust` runtime would trigger `rustc` with a 100 MB
source file, outside bubblewrap (compilation runs outside the sandbox — see Finding F1).

**Impact**: DoS via process/FD exhaustion; disk exhaustion; server crash.
**Fix**: `asyncio.Semaphore(3)` per session in `SessionState`; `if len(code) > 100_000: reject`.

---

### ✅ CONFIRMED CRITICAL: G3 — No rate limiting on `/auth/login` and `/auth/register`

**Code location**: [frontend/routers/auth.py:116-145](../../../frontend/routers/auth.py#L116-L145) and [L167-L193](../../../frontend/routers/auth.py#L167-L193)

`_resend_limiter` and `_verify_limiter` are defined at module level, but neither
`register()` nor `login()` consult any limiter. The login handler calls `bcrypt.checkpw`
in a thread, so each attempt is slow-ish (~100–300 ms) but concurrent goroutines saturate
the thread pool. An attacker can run thousands of parallel login attempts from multiple IPs.

**Impact**: Credential stuffing, account takeover, thread pool exhaustion.
**Fix**: Per-email limiter on login (5/5 min), per-IP limiter on register (10/hr).

---

### ✅ CONFIRMED CRITICAL: G6 — `lesson_id` not URL-encoded in WS proxy

**Code location**: [frontend/routers/ws_proxy.py:66-69](../../../frontend/routers/ws_proxy.py#L66-L69)

```python
backend_url = (
    f"{settings.BACKEND_WS}/ws/{session_id}"
    f"?lesson_id={lesson_id}"          # ← raw interpolation
)
```

`lesson_id` is taken directly from the query string of the client WebSocket upgrade
request and interpolated raw into the backend URL. A value like
`00000000-0000-0000-0000-000000000000&evil=injected` would add an extra query parameter
to the backend connection. Lesson IDs are UUIDs at creation time, but the WS proxy does
not re-validate this.

**Impact**: Query parameter injection into the loopback WS URL.
**Fix**: `urllib.parse.urlencode({"lesson_id": lesson_id})`; validate UUID format first.

---

### ✅ CONFIRMED HIGH: G10 — No shared secret between BFF and backend

**Code location**: [backend/config.py](../../../backend/config.py)

```python
class Settings:
    FRONTEND_ORIGIN: str = os.getenv("FRONTEND_ORIGIN", "http://127.0.0.1:8000")
    # ← no BACKEND_SHARED_SECRET
```

The backend trusts all HTTP/WS traffic arriving on `127.0.0.1:8001` with no secret. The
only protection is the loopback binding itself. A misconfigured nginx rule, a future
container deployment, or any process running on the same host can call
`GET /internal/auth/user?email=...` to retrieve password hashes.

The internal router exposes:
- `GET /internal/auth/user` — returns `password_hash` in plaintext
- `POST /internal/auth/register` — creates users
- `POST /internal/sessions` — creates sessions for arbitrary user IDs
- `DELETE /internal/sessions/{id}` — destroys any session

**Impact**: Privilege escalation, authentication bypass, account takeover if loopback is
ever reachable by an untrusted process.
**Fix**: `X-Internal-Token` header; backend validates via middleware; one env var.

---

## Part 2 — New Findings (not in existing documents)

### F1 — MEDIUM: Compiled language sources run through compiler OUTSIDE bubblewrap

**Code location**: [backend/services/code_runner.py:296-336](../../../backend/services/code_runner.py#L296-L336)

The comment in `code_runner.py` documents this intentionally:

> "Compiled languages (C, C++, Rust, TypeScript) are compiled OUTSIDE bubblewrap
> (needs filesystem write access), then only the build artefact is exposed inside
> the sandbox via the /code bind-mount."

However, the security implication is understated. The compile step for C, C++, Rust, and
TypeScript runs with:
- Full filesystem access (no sandbox)
- No network restriction
- No resource limits (CPU, memory, disk)
- Compiler timeout only (30–60 s)

While the compiler itself is a trusted binary, this is significant because:
1. A malicious Rust `build.rs` script would be executed by `rustc` with full OS access.
   (The current `rustc` invocation does not pass `build.rs`, but the pattern is fragile.)
2. A C file with `#include "/etc/passwd"` triggers a disk read during preprocessing.
3. TypeScript compiler plugins or `tsconfig.json` pointing to arbitrary paths are not
   restricted.

The AST check applies only to Python. Non-Python code has no pre-execution filter.

**Impact**: Partial sandbox escape during compilation phase for compiled languages.
**Fix**: Use `rlimit` (via `resource` module or `systemd-run --scope`) to cap compile-step
memory and CPU; or run compilation inside a separate restricted container. Short-term:
document that the compile-step sandbox boundary is weaker than the run-step boundary.

---

### F2 — MEDIUM: CSP `'unsafe-inline'` defeats XSS protection

**Code location**: [frontend/main.py:66-76](../../../frontend/main.py#L66-L76)

```python
"Content-Security-Policy": (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "   # ← defeats CSP for scripts
    "style-src 'self' 'unsafe-inline'; "    # ← defeats CSP for styles
    ...
)
```

`'unsafe-inline'` for `script-src` allows any inline `<script>` tag to execute. This
eliminates the primary XSS mitigation that CSP provides. A CSP with `'unsafe-inline'` is
essentially equivalent to no CSP at all for XSS defence.

The comment says this is "needed by Vite-built assets in prod". Vite 5+ supports
CSP-compatible output via `build.cssCodeSplit` and nonce injection. The `'unsafe-inline'`
should be replaced with:
- A per-request nonce (requires server-side template injection into `index.html`)
- Or `'strict-dynamic'` + nonces for a simpler migration path

**Impact**: CSP header present but provides near-zero XSS protection.
**Fix**: Switch to nonce-based CSP or `'strict-dynamic'`. At minimum, remove
`'unsafe-inline'` from `script-src` (style inline is less critical).

---

### F3 — MEDIUM: `connect-src wss: ws:` allows WebSocket exfiltration to any host

**Code location**: [frontend/main.py:72](../../../frontend/main.py#L72)

```python
"connect-src 'self' wss: ws:; "   # ← wss: and ws: match ANY domain
```

`wss:` and `ws:` are scheme-only directives that match any WebSocket host. If an XSS
vulnerability existed, an attacker's payload could open a WebSocket to `wss://attacker.com`
and exfiltrate session data or conversation contents. This should be restricted to `'self'`.

**Impact**: In combination with XSS, enables data exfiltration via WebSocket.
**Fix**: Replace `wss: ws:` with `'self'` (the WS connection is always same-origin in
production).

---

### F4 — MEDIUM: Login timing oracle enables email enumeration

**Code location**: [frontend/routers/auth.py:167-193](../../../frontend/routers/auth.py#L167-L193)

```python
async def login(body: LoginRequest):
    ...
    resp = await http.get("/internal/auth/user", params={"email": email})

    if resp.status_code == 404:
        raise HTTPException(status_code=401, detail="Invalid email or password")
        # ↑ fast path — no bcrypt, returns in ~5–20 ms

    ...
    match = await asyncio.to_thread(
        bcrypt.checkpw, body.password.encode(), stored_hash.encode()
    )
    if not match:
        raise HTTPException(status_code=401, detail="Invalid email or password")
        # ↑ slow path — bcrypt takes ~100–300 ms
```

Both paths return the same HTTP status and error message, but the response time differs
by ~100–250 ms. An attacker submitting a list of email addresses can statistically
determine which are registered by comparing response times. This enables email
enumeration at scale with no rate limit in place (G3).

**Impact**: Email enumeration attack (worsened by G3 absence of rate limiting).
**Fix**: Always run bcrypt, even for unknown users. Cache a dummy hash at startup:

```python
_DUMMY_HASH = bcrypt.hashpw(b"dummy", bcrypt.gensalt())

# In login(), if 404:
await asyncio.to_thread(bcrypt.checkpw, body.password.encode(), _DUMMY_HASH)
raise HTTPException(status_code=401, detail="Invalid email or password")
```

---

### F5 — MEDIUM: BFF `/docs` endpoint publicly accessible

**Code location**: [frontend/main.py:53](../../../frontend/main.py#L53)

```python
app = FastAPI(
    ...
    docs_url="/docs",   # ← publicly accessible API explorer
    ...
)
```

The FastAPI Swagger UI at `https://yourdomain.com/docs` is served to any anonymous
visitor. It documents every endpoint, parameter, schema, and response model. This
significantly reduces attacker reconnaissance effort and is standard practice to disable
in production.

`GAP_ANALYSIS.md` G17 notes this for the backend's `/docs` endpoint (which is
loopback-only and therefore lower risk). The frontend's `/docs` is the higher-risk
instance — it is public-facing — and is not mentioned.

**Impact**: Full API surface disclosure to unauthenticated users.
**Fix**: `docs_url=None` in production (`if settings.ENV == "production": docs_url=None`).

---

### F6 — LOW: WebSocket connection accepted before session validation

**Code location**: [frontend/routers/ws_proxy.py:48-56](../../../frontend/routers/ws_proxy.py#L48-L56)

```python
async def ws_proxy(...):
    await websocket.accept()      # ← HTTP 101 sent here

    entry = store.get(session_id) # ← auth check happens after accept
    if entry is None:
        await websocket.send_json({"event": "error", "message": "Invalid session"})
        await websocket.close(code=4001)
        return
```

The WS upgrade is accepted (HTTP 101 response sent) before the session is validated. An
unauthenticated client therefore briefly holds an open WebSocket connection. This matters
because:
1. Connection state is established server-side before rejection, costing resources.
2. Some intermediaries (e.g., load balancers) may treat the 101 as "connection
   established" and affect timeout/billing counters.
3. The close-after-accept pattern is less clean than rejecting at the HTTP handshake level.

FastAPI does not expose a pre-accept hook for WebSocket endpoints, but the session check
can be moved to a query-parameter-validated HTTP dependency that returns 401 before
the upgrade completes (using `HTTPException` before `accept()`). This is a known FastAPI
pattern.

**Impact**: Minor resource waste; unauthenticated clients briefly hold connections.
**Fix**: Move session validation before `await websocket.accept()`:
```python
entry = store.get(session_id)
if entry is None:
    raise HTTPException(status_code=401, detail="Invalid session")  # pre-accept
await websocket.accept()
```

---

### F7 — LOW: `code_error` event leaks raw exception messages to the client

**Code location**: [backend/routers/ws_session.py:483-488](../../../backend/routers/ws_session.py#L483-L488)

```python
except Exception as exc:
    log.exception("_handle_run_code raised: %s", exc)
    try:
        await websocket.send_json(
            {"event": "code_error", "invocation_id": inv_id, "message": str(exc)}
        )   # ↑ raw exception string sent to client
```

`str(exc)` can include file paths, internal module names, and implementation details.
This is the same pattern flagged in `GAP_ANALYSIS.md` G11 for HTTP exceptions but missed
in the WebSocket handler.

**Impact**: Internal path/module information disclosure.
**Fix**: Log full exception; send a generic message to the client: `"Code execution error — please try again"`.

---

### F8 — LOW: bwrap mounts full `/dev` and `/proc`

**Code location**: [backend/services/code_runner.py:157-160](../../../backend/services/code_runner.py#L157-L160)

```python
"--proc", "/proc",
"--dev", "/dev",
```

`--dev /dev` mounts the host's device tree inside the sandbox. On many Linux systems this
includes `/dev/mem`, `/dev/kmem`, `/dev/sda*`, and `/dev/kvm`. While namespace isolation
prevents most exploitation, privileged escape techniques sometimes use device access.

`--proc /proc` provides full process visibility inside the sandbox. A sandboxed script can
enumerate all running processes on the host via `/proc`, leaking system information.

**Recommended configuration**:
```
--dev-bind /dev/null /dev/null
--dev-bind /dev/urandom /dev/urandom
--dev-bind /dev/zero /dev/zero
--tmpfs /proc  (or omit entirely for most code)
```

**Impact**: Information disclosure (process list); minimal escalation risk with current kernel.
**Fix**: Replace `--dev /dev` with minimal device mounts; use `--tmpfs /proc` unless
the code explicitly needs `/proc/self/`.

---

### F9 — LOW: No startup warning when bwrap is absent in production

**Code location**: [backend/services/code_runner.py:373-383](../../../backend/services/code_runner.py#L373-L383)

```python
if _BWRAP:
    full_cmd = _bwrap_cmd(run_inner_cmd, tmpdir, runtime)
else:
    log.warning(
        "bwrap not found — running code unsandboxed. "
        "Install bubblewrap: sudo apt install bubblewrap"
    )
    full_cmd = [c.replace("/code/", tmpdir + "/") for c in run_inner_cmd]
```

If `bwrap` is not installed (e.g., forgotten during server provisioning), the service
silently falls back to running user-submitted code completely unsandboxed with full OS
access. The warning is a `log.warning()` that appears in journald — easy to miss.

`GAP_ANALYSIS.md` G14 mentions this but suggests only "a prominent startup warning".
Given the severity of unsandboxed arbitrary code execution, this should be a hard startup
failure in production.

**Impact**: Total sandbox bypass → arbitrary code execution on the host if bwrap is absent.
**Fix**:
```python
if settings.ENV == "production" and not _BWRAP:
    raise RuntimeError(
        "bwrap (bubblewrap) is required in production for sandboxed code execution. "
        "Install with: sudo apt install bubblewrap"
    )
```

---

### F10 — INFO: Password minimum length is weak (8 characters)

**Code location**: [frontend/routers/auth.py:64-67](../../../frontend/routers/auth.py#L64-L67)

```python
def _validate_password(password: str) -> None:
    if len(password) < 8:
        raise HTTPException(status_code=422, detail="Password must be at least 8 characters")
```

NIST SP 800-63B (2017) recommends a minimum of 8 characters, but also recommends
checking against known-breached password lists. OWASP recommends 12 characters minimum
for new applications. 8 characters is the absolute floor, not a recommended target.

There is also no maximum length check. Submitting a 1 MB password string causes bcrypt to
run on whatever the underlying C library truncates (typically 72 bytes for bcrypt), but
the `body.password.encode()` still allocates the full buffer in Python memory.

**Impact**: Weak passwords accepted; potential memory allocation amplification via bcrypt.
**Fix**: Minimum 12 characters; maximum 128 characters; optionally check against
[HaveIBeenPwned password API](https://haveibeenpwned.com/API/v3#PwnedPasswords) (k-anonymity model, privacy-safe).

---

## Part 3 — Evaluation of Deployment Plan

The `DEPLOYMENT_ROADMAP.md` is generally well-structured. Specific observations:

### D1 — GOOD: Non-root `appuser` is correct

Creating a dedicated `appuser` (Phase 2.2) is the right approach. Verify that
`appuser` cannot `sudo` and is not in the `docker` group.

### D2 — GOOD: loopback binding for backend

`HOST=127.0.0.1` for the backend is the correct default. Verify this is set in the
`.env` file and not overridden.

### D3 — CONCERN: `.env` file stored in app directory

```bash
cat > ~/app/.env << 'EOF'
ANTHROPIC_API_KEY=sk-ant-...
```

Storing `.env` inside the cloned repo directory (`~/app/`) risks accidental inclusion in
git commits, log output, or backup archives. Prefer `/etc/pdf-to-audio/env` owned by
root with mode 0640, readable by `appuser` via group membership. Reference it with
`EnvironmentFile=/etc/pdf-to-audio/env` in the systemd unit.

Also: the `BACKEND_SHARED_SECRET` env var does not exist yet but should be added to the
deployment `.env` template before it is implemented (G10 fix).

### D4 — CONCERN: nginx WebSocket `proxy_read_timeout 3600s`

```nginx
proxy_read_timeout 3600s;
proxy_send_timeout 3600s;
```

A 1-hour timeout per WebSocket connection means a single idle-open connection holds nginx
worker resources for an hour. If an attacker opens thousands of WS connections and does
nothing, they can exhaust nginx's worker pool. The BFF already handles ping/pong via the
`websockets` library (20 s intervals), so nginx should be able to detect dead connections
sooner.

Consider adding:
```nginx
proxy_read_timeout 600s;   # 10 min; BFF pings every 20s so true idle = disconnect
```

And confirm nginx's `keepalive_timeout` is set to a reasonable value.

### D5 — CONCERN: No UFW / firewall rules in provisioning steps

Phase 2.2 installs packages and creates a user but does not configure a firewall. The
deployment guide should include:

```bash
ufw default deny incoming
ufw allow 22/tcp    # SSH
ufw allow 80/tcp    # HTTP (certbot + redirect)
ufw allow 443/tcp   # HTTPS
ufw enable
```

Without this, port 8000 (BFF) and 8001 (backend) are only protected by the binding
address, not by firewall rules. A future configuration mistake (e.g., `HOST=0.0.0.0`)
would immediately expose the backend.

### D6 — CONCERN: Backup cron uses `sqlite3 .backup` without WAL checkpoint

```bash
sqlite3 /var/lib/pdf-to-audio/storage/db.sqlite3 ".backup '/path/to/backup.sqlite3'"
```

The `.backup` command is online-safe (it uses the SQLite backup API), but with WAL mode
enabled, a backup captured mid-WAL-cycle may not include the latest committed transactions
that are still in the WAL file. Add a WAL checkpoint before backup:

```bash
sqlite3 /var/lib/pdf-to-audio/storage/db.sqlite3 "PRAGMA wal_checkpoint(TRUNCATE); .backup '/path/backup.sqlite3'"
```

### D7 — CONCERN: `certbot --standalone` requires stopping nginx first

The Phase 4.1 command:
```bash
sudo certbot certonly --standalone -d yourdomain.com
```

`--standalone` starts its own HTTP server on port 80, which conflicts with nginx if nginx
is running. The guide mentions "Temporarily stop nginx if running" but nginx may not be
running at first deploy. Subsequent renewals (auto-renew) can use `--nginx` plugin or a
renewal hook that temporarily stops nginx. The deployment guide should be explicit about
this to avoid renewal failures.

Use `--nginx` plugin instead for production:
```bash
sudo certbot --nginx -d yourdomain.com
```

---

## Part 4 — Risk Matrix

| ID | Finding | Severity | Status | Effort |
|----|---------|----------|--------|--------|
| G1 | `/api/usage` unauthenticated | **Critical** | ❌ Not fixed | 30 min |
| G2 | `run_code` unlimited concurrency | **Critical** | ❌ Not fixed | 1 hr |
| G3 | No rate limit on login/register | **Critical** | ❌ Not fixed | 2 hr |
| G6 | `lesson_id` URL injection in WS proxy | **Critical** | ❌ Not fixed | 30 min |
| G7 | `run_code` payload size unlimited | **Critical** | ❌ Not fixed | 30 min |
| G10 | No BFF↔backend shared secret | **High** | ❌ Not fixed | 2 hr |
| F4 | Login timing oracle (email enumeration) | **Medium** | 🆕 New | 30 min |
| F1 | Compile step runs outside bubblewrap | **Medium** | 🆕 New | 4 hr |
| F2 | CSP `'unsafe-inline'` defeats XSS protection | **Medium** | 🆕 New | 2 hr |
| F3 | `connect-src wss: ws:` too permissive | **Medium** | 🆕 New | 15 min |
| F5 | BFF `/docs` publicly accessible | **Medium** | 🆕 New | 15 min |
| G5 | No session expiry | **High** | ❌ Not fixed | 1 hr |
| G9 | No password reset flow | **High** | ❌ Not fixed | 4 hr |
| G11 | Error messages leak internal paths | **Medium** | ❌ Not fixed | 1 hr |
| G13 | No per-user storage quota | **Medium** | ❌ Not fixed | 2 hr |
| G14 | AST check bypassable (bwrap is real defence) | **Medium** | ❌ Not fixed (by design) | — |
| F7 | `code_error` leaks exception strings via WS | **Low** | 🆕 New | 15 min |
| F6 | WS accepted before session validation | **Low** | 🆕 New | 1 hr |
| F8 | bwrap mounts full `/dev` and `/proc` | **Low** | 🆕 New | 1 hr |
| F9 | No hard failure if bwrap absent in production | **Low** | 🆕 New | 15 min |
| F10 | Weak password minimum (8 chars, no max) | **Info** | 🆕 New | 30 min |
| D3 | `.env` in app directory | **Low** | 🆕 New | 30 min |
| D4 | nginx WS timeout too long (DoS amplification) | **Low** | 🆕 New | 15 min |
| D5 | No UFW firewall in provisioning steps | **Medium** | 🆕 New | 30 min |
| D6 | SQLite backup doesn't checkpoint WAL | **Low** | 🆕 New | 15 min |

---

## Part 5 — Recommended Fix Order

### Block 0 — Before any external traffic (critical, ~5 hours total)

1. **G1** — Authenticate `/api/usage` (30 min)
2. **G3** — Rate-limit login + register (2 hr)
3. **G6** — URL-encode `lesson_id` + UUID validate (30 min)
4. **G7 + G2** — Code size cap + semaphore (1.5 hr)
5. **F9** — Hard production failure if bwrap absent (15 min)
6. **F5** — Disable BFF `/docs` in production (15 min)
7. **D5** — UFW firewall rules in deployment guide (30 min)

### Block 1 — Before inviting external users (~8 hours total)

8. **G10** — BFF↔backend shared secret (2 hr)
9. **F4** — Login timing oracle fix (30 min)
10. **F3** — Restrict CSP `connect-src` to `'self'` (15 min)
11. **G9** — Password reset flow (4 hr)
12. **G5** — Session expiry cleanup job (1 hr)
13. **F7** — Scrub exception strings from WS error events (15 min)
14. **G11** — Scrub exception strings from HTTP error events (1 hr)

### Block 2 — Before general availability

15. **F2** — Nonce-based CSP (replaces `'unsafe-inline'`) (2 hr)
16. **G13** — Per-user storage quota (2 hr)
17. **F8** — Minimal bwrap device mounts (1 hr)
18. **F6** — WS pre-accept session validation (1 hr)
19. **D4** — nginx WS timeout reduction (15 min)
20. **D3** — Move `.env` to `/etc/` (30 min)

### Block 3 — Deferred (existing plan)

21. G4 — Thread leak mitigation (asyncio.timeout + threading.Event)
22. #6 — DB-backed session store
23. #22 — Audit log
24. #10 — RBAC
25. #19 — GDPR erasure
26. #20 — Encryption at rest

---

## Part 6 — What the Existing Plans Get Right

- Authorization ownership checks are in place (Phases 1–2 complete): user A cannot read
  user B's lesson, WS, or PDF.
- Path traversal in PDF rendering is fixed.
- Dynamic SQL is eliminated.
- Wildcard CORS is blocked in production.
- HSTS and `X-Content-Type-Options` are set.
- Email validation uses `email-validator` (RFC-compliant).
- bcrypt for passwords (correct algorithm choice).
- Session tokens use `secrets.token_urlsafe(32)` (256 bits, cryptographically secure).
- bubblewrap sandbox for the code runner is the correct defence layer.
- Loopback-only backend binding is correct.
- The deployment plan uses systemd + nginx + certbot (appropriate stack; no unnecessary
  complexity).

---

*Audit conducted against codebase state as of 2026-03-14.*
*All file references are relative to the project root.*
