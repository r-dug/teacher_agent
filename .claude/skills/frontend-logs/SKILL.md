---
name: frontend-logs
description: Tail, filter, and analyze the frontend BFF server log. Use when debugging auth, WS proxy, API routing, or session issues.
allowed-tools: Bash, Read
---

The frontend BFF log is at `storage/frontend.log`. It is only written when the
frontend is started with `LOG_FILE=1`. Each line is a JSON object.

## To start the frontend BFF with file logging:
```bash
LOG_FILE=1 /home/richard/.local/bin/uv run python -m frontend.main
```

## Understand $ARGUMENTS and respond accordingly:

- **Empty** — tail the last 50 lines, parse them, and give a plain-English
  summary of what happened (errors first, then key events).

- **A number** (e.g. `200`) — tail that many lines and summarize.

- **A log level** (`error`, `warning`, `debug`) — filter to that level and show results.

- **A logger name or fragment** (e.g. `ws_proxy`, `auth`, `email`) —
  filter lines where `"logger"` contains that string.

- **A keyword or phrase** (e.g. `session expired`, `upload token`, `proxy`) —
  grep for that string across the full log and show matching lines with context.

- **A combination** (e.g. `error ws_proxy`) — apply all applicable filters together.

## Core commands to build from:

```bash
# Tail last N lines
tail -n 50 storage/frontend.log

# Filter by level
grep '"level": "ERROR"' storage/frontend.log | tail -n 20

# Filter by logger name
grep '"logger": "frontend.routers.ws_proxy"' storage/frontend.log | tail -n 20

# Fuzzy filter by any field
grep 'upload_token' storage/frontend.log | tail -n 20

# Pretty-print a single line
tail -n 1 storage/frontend.log | python -m json.tool

# Count errors
grep '"level": "ERROR"' storage/frontend.log | wc -l
```

## If the log file doesn't exist:
Tell the user to restart the frontend with `LOG_FILE=1`.

## General approach:
Parse the JSON, don't just dump raw lines at the user. Summarize patterns,
highlight errors and warnings, and call out anything that looks like an auth
failure, proxy disconnect, or session/token problem.
