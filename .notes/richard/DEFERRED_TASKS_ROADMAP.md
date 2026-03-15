# Deferred Tasks Roadmap

Five items from the Production Readiness Roadmap were deferred because they require
meaningful architectural work or touch compliance concerns that warrant planning before
implementation. This document details what each task entails, why it was deferred, the
concrete implementation plan, and the order of execution.

---

## Recommended execution order

| Priority | Task | Reason |
|----------|------|--------|
| 1 | **#6 — DB-backed session store** | Blocks multi-instance deploy and crash recovery; low risk change |
| 2 | **#22 — Audit log** | Pure additive schema + logging; no behaviour change; enables compliance |
| 3 | **#10 — RBAC** | Depends on knowing who is an admin; cleaner after audit log exists |
| 4 | **#19 — GDPR right-to-erasure** | Depends on having a complete user identity model (#10) |
| 5 | **#20 — Encryption at rest** | Operational concern; can be done in parallel with #19 but blocked on key management decision |

---

## #6 — Replace in-memory session store with DB-backed sessions

### Why deferred
Requires deciding on a persistence strategy (Redis, SQLite, Postgres) without breaking
the existing BFF → backend auth handshake.

### Current state
`frontend/session_store.py` holds a Python dict in the BFF process.  Sessions are
_also_ stored in the backend SQLite `sessions` table (managed by `backend/db/models.py`).
On BFF restart, the in-memory dict is cleared; users get 401 and must re-log in.

### Implementation plan

**Step 1 — Warm-cache fallback (immediate, no new infrastructure)**

When a request arrives with a `session_id` that is not in the local `SessionStore`,
call `GET /internal/sessions/{id}` on the backend before rejecting.  If the backend
confirms the session, re-populate the local entry and proceed.

Changes:
- `frontend/session_store.py` — no changes needed
- `frontend/routers/lessons.py`, `voices.py`, `ws_proxy.py`, etc. — extract a
  shared `async def require_session(session_id, store, http)` async helper that
  performs the fallback lookup and caches the result
- Add `GET /internal/sessions/{id}` to `backend/routers/internal.py` (already exists;
  verify it returns `user_id` and `email`)

This is a low-risk fix that makes BFF restarts transparent to users.

**Step 2 — Persist session metadata to SQLite (no Redis required)**

Add a `frontend_sessions` table to the backend DB (or repurpose the existing `sessions`
table) to store `user_id`, `email`, `created_at`, `last_seen`.  On BFF startup, the
warm-cache fallback (Step 1) already handles recovery without a full scan.  No bulk
reload needed.

**Step 3 — (Future) Redis for horizontal scaling**

If/when multiple BFF instances are needed, swap the fallback lookup for a Redis `GET`.
The interface is already encapsulated in `SessionStore`; only the backing storage changes.
This is a later concern — single-instance with the fallback lookup is sufficient for beta.

### Files to touch
- `backend/routers/internal.py` — verify `GET /internal/sessions/{id}` exists and returns email
- `frontend/routers/` — all routers that call `_require_session`; extract shared async helper

---

## #22 — Audit log

### Why deferred
Pure additive work, but needs agreement on what to log and where to surface it (admin
UI, log aggregator, SIEM).  Low risk to implement at any time.

### Implementation plan

**Schema** — add to `backend/db/schema.sql`:

```sql
CREATE TABLE IF NOT EXISTS audit_events (
    id         TEXT PRIMARY KEY,
    user_id    TEXT,           -- NULL for unauthenticated events (e.g. failed login)
    action     TEXT NOT NULL,  -- see catalogue below
    resource   TEXT,           -- e.g. lesson_id, persona_id
    detail     TEXT,           -- optional JSON with extra context
    ip_addr    TEXT,           -- client IP (BFF extracts from X-Forwarded-For or client)
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_events (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_events (action, created_at DESC);
```

**Event catalogue** (start with these; extend as needed):

| action | trigger | resource |
|--------|---------|----------|
| `user.register` | POST /auth/register | user_id |
| `user.verify_email` | POST /auth/verify | user_id |
| `user.login` | POST /auth/login | user_id |
| `user.login_failed` | POST /auth/login (wrong password/unknown) | email only |
| `user.logout` | POST /auth/logout | user_id |
| `lesson.create` | POST /lessons | lesson_id |
| `lesson.delete` | DELETE /lessons/{id} | lesson_id |
| `persona.create` | POST /personas | persona_id |
| `persona.delete` | DELETE /personas/{id} | persona_id |
| `account.delete` | DELETE /account (GDPR) | user_id |

**Model helper** — add to `backend/db/models.py`:

```python
async def log_audit_event(
    conn: aiosqlite.Connection,
    action: str,
    user_id: str | None = None,
    resource: str | None = None,
    detail: dict | None = None,
    ip_addr: str | None = None,
) -> None:
    await conn.execute(
        """INSERT INTO audit_events (id, user_id, action, resource, detail, ip_addr)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (new_id(), user_id, action, resource,
         json.dumps(detail) if detail else None, ip_addr),
    )
    await conn.commit()
```

**Wire in** — call `log_audit_event` in:
- `backend/routers/internal.py` — register, verify, login/failed, logout
- `backend/routers/lessons.py` — create, delete
- `backend/routers/personas.py` — create, delete

IP address: extract from `Request.client.host` in FastAPI; pass as a query param
through the internal API or add an `X-Client-IP` header in `frontend/routers/` before
forwarding requests to the backend.

**Read endpoint** (admin only, after #10):
`GET /internal/audit?user_id=&action=&limit=100&offset=0`

---

## #10 — RBAC (role-based access control)

### Why deferred
Requires DB schema migration and agreement on the role model before implementing.
Getting this wrong early means painful retro-fitting.

### Proposed role model

Two roles are sufficient for the near term:

| Role | Value | Capabilities |
|------|-------|-------------|
| Regular user | `'user'` | All existing self-service operations |
| Admin | `'admin'` | Delete/edit built-in personas, view audit log, future: manage users |

### Implementation plan

**Schema migration** — add to `backend/db/schema.sql`:

```sql
-- Add role to users; existing rows default to 'user', initial admin set by env
ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user';
```

Because SQLite's `ALTER TABLE` doesn't support adding constraints, also add a
`CHECK (role IN ('user', 'admin'))` in the `CREATE TABLE` statement so new databases
get the constraint.

**Bootstrap first admin** — read `ADMIN_EMAIL` env var at startup in
`backend/main.py` lifespan; if set, `UPDATE users SET role = 'admin' WHERE email = ?`.
No UI needed initially.

**Backend dependency** — add to `backend/db/models.py`:

```python
async def get_user_role(conn, user_id: str) -> str:
    async with conn.execute(
        "SELECT role FROM users WHERE id = ?", (user_id,)
    ) as cur:
        row = await cur.fetchone()
    return row[0] if row else "user"
```

Add a FastAPI dependency:

```python
async def require_admin(user_id: str, conn: Conn) -> None:
    role = await models.get_user_role(conn, user_id)
    if role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
```

**Protect endpoints** — wire `require_admin` into:
- `DELETE /personas/{id}` for personas where `user_id IS NULL` (built-in)
- `GET /internal/audit` (new endpoint from #22)
- Any future user-management endpoints

**Propagate to BFF** — the BFF session store can cache role alongside user_id/email
(add `role: str = 'user'` to `SessionEntry`).  The BFF can then gate admin-only
proxied calls at the BFF layer as a second line of defence, with the backend as
authoritative enforcer.

---

## #19 — GDPR right-to-erasure / data retention

### Why deferred
Needs a complete user identity model (#10) and legal/product decisions on retention
periods before implementation.

### What GDPR requires
- **Right to erasure (Article 17)**: on request, delete all personal data.
- **Right to data portability (Article 20)**: export data in machine-readable format.
- **Retention limits**: don't hold data longer than needed.  Agree a retention policy.

### Implementation plan

**Account deletion endpoint**

```
DELETE /api/account
Header: X-Session-Id: <session>
```

Steps:
1. Validate session → get `user_id`.
2. Log `account.delete` audit event.
3. Delete all PDFs from disk: query `lessons WHERE user_id = ?`, remove each `pdf_path`.
4. `DELETE FROM users WHERE id = ?` — SQLite cascades to: sessions, lessons,
   lesson_sections, messages, personas, email_verifications, audit_events.
5. Invalidate BFF session store entry.
6. Return 204.

Add to `backend/routers/internal.py` (backend-side execution) and proxy from
`frontend/routers/` with session validation.

**Data export endpoint**

```
GET /api/account/export
Header: X-Session-Id: <session>
```

Returns JSON:
```json
{
  "user": { "email": "...", "created_at": "..." },
  "lessons": [{ "title": "...", "created_at": "...", "sections": [...] }],
  "messages": [{ "lesson_title": "...", "role": "...", "content": "..." }]
}
```

Omit `password_hash` and internal IDs.  Zip with PDFs if portability of source
material is required (check with legal — PDFs may be third-party copyrighted).

**Automatic data retention**

Add a nightly cleanup job (use `apscheduler` or a simple `asyncio` background task):
- Delete lessons with `completed = 1` and `updated_at < datetime('now', '-180 days')`.
- Delete unverified users older than 30 days.
- Delete expired sessions (`last_seen < datetime('now', '-30 days')`).

Expose retention periods as env vars: `LESSON_RETENTION_DAYS`, `SESSION_RETENTION_DAYS`.

**Privacy policy** — document what is collected, why, and the erasure procedure.
This is a blocker for #31 (no privacy policy / ToS) from Phase 4.

---

## #20 — Encryption at rest

### Why deferred
Key management strategy must be decided before implementation.  A poor encryption
scheme (e.g. key baked into code or stored beside the data) provides no security.

### Scope of sensitive data

| Data | Location | Sensitivity |
|------|----------|-------------|
| SQLite DB | `storage/db.sqlite3` | High — emails, password hashes, conversation history |
| PDF files | `storage/{user_id}/pdfs/` | Medium — user-supplied documents |
| Audio (ephemeral) | in-memory | Low — not persisted |

### Recommended approach

**Option A — Filesystem-level encryption (recommended for production)**

Use LUKS (Linux) or an encrypted volume at the host level.  The application code has
zero changes.  Key is managed by the OS/cloud provider (e.g. AWS EBS encryption, GCP
persistent disk encryption).  Simplest and most auditable.

Steps:
- In deployment docs: require storage volume to be on an encrypted filesystem.
- Enforce at startup: if `ENV=production` and `STORAGE_DIR` is not on an encrypted
  mount, log a prominent warning (checking this is platform-specific; document it
  rather than enforce in code).

**Option B — Application-level PDF encryption**

If filesystem encryption is not available, encrypt PDF files before writing to disk
using `cryptography.fernet.Fernet`.

```python
# backend/services/encryption.py
from cryptography.fernet import Fernet
import os

def _get_key() -> bytes:
    key = os.environ.get("PDF_ENCRYPTION_KEY")
    if not key:
        raise RuntimeError("PDF_ENCRYPTION_KEY env var not set")
    return key.encode()

def encrypt_pdf(data: bytes) -> bytes:
    return Fernet(_get_key()).encrypt(data)

def decrypt_pdf(data: bytes) -> bytes:
    return Fernet(_get_key()).decrypt(data)
```

Wire into:
- `backend/routers/lessons.py` — encrypt on upload, decrypt on render.

Key rotation: Fernet supports multi-key with `MultiFernet`.

**SQLite DB**: Option A covers this.  SQLCipher is the option if filesystem encryption
is unavailable, but it requires replacing `aiosqlite` with a SQLCipher-compatible
driver (`sqlcipher3`, which has complex build requirements on some platforms).  Recommend
Option A + document that the DB path should live on an encrypted volume.

**Key management**: never commit keys to git.  Use a secrets manager
(AWS Secrets Manager, HashiCorp Vault, or at minimum a `.env` file excluded from git
and protected by OS file permissions).

---

## Summary checklist

- [ ] **#6** — Implement warm-cache session fallback (async `require_session` helper)
- [ ] **#6** — Document/plan Redis path for horizontal scaling
- [ ] **#22** — Add `audit_events` schema + `log_audit_event` helper
- [ ] **#22** — Wire audit calls into register, login, logout, create/delete lesson/persona
- [ ] **#10** — Add `role` column to `users` schema
- [ ] **#10** — Bootstrap initial admin via `ADMIN_EMAIL` env var at startup
- [ ] **#10** — Add `require_admin` FastAPI dependency; protect built-in persona deletion
- [ ] **#19** — Implement `DELETE /api/account` with PDF cleanup + DB cascade
- [ ] **#19** — Implement `GET /api/account/export`
- [ ] **#19** — Add nightly retention cleanup background task
- [ ] **#20** — Decide on filesystem encryption vs. application-level (recommend filesystem)
- [ ] **#20** — Document encryption requirements in deployment guide
- [ ] **#20** — (If needed) implement `encrypt_pdf` / `decrypt_pdf` with `Fernet`

---

*Created: 2026-03-14.*
