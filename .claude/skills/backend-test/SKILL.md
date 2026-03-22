---
name: backend-test
description: Run the backend test suite. Accepts an optional path argument (e.g. /backend-test tests/backend/test_ws_session.py).
allowed-tools: Bash
---

Run the backend tests using the correct uv path:

```bash
/home/richard/.local/bin/uv run pytest $ARGUMENTS tests/ -q
```

If $ARGUMENTS is empty, default to `tests/ -q`.
If $ARGUMENTS is a specific file or directory, run that target with `-v` instead.

## On failure, check:
1. Is the virtualenv synced? Run `/home/richard/.local/bin/uv sync --dev` if dependencies look missing.
2. Auth tests in `tests/frontend/test_auth.py` fail due to a known `/api` prefix mismatch — not a regression, can be ignored.
3. Integration tests may require a live DB. If skipped unexpectedly, check env vars.
4. If a test was recently passing and now fails after a model/schema change, check if the enrollment/template split (v2 migration) is involved.
