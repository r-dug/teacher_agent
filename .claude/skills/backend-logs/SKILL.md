---
name: backend-logs
description: Tail, filter, and analyze the backend log. Use when debugging backend behavior, errors, or agent turns.
allowed-tools: Bash, Read
---

The backend log is at `storage/backend.log`. It is only written when the
backend is started with `LOG_FILE=1`. Each line is a JSON object.

## To start the backend with file logging:
```bash
LOG_FILE=1 /home/richard/.local/bin/uv run python -m backend.main
```

## Understand $ARGUMENTS and respond accordingly:

- **Empty** — tail the last 50 lines, parse them, and give a plain-English
  summary of what happened (errors first, then key events).

- **A number** (e.g. `200`) — tail that many lines and summarize.

- **A log level** (`error`, `warning`, `debug`) — filter to that level and show results.

- **A logger name or fragment** (e.g. `ws_session`, `fallback`, `tts`) —
  filter lines where `"logger"` contains that string.

- **A keyword or phrase** (e.g. `agent turn`, `provider failed`, `timeout`) —
  grep for that string across the full log and show matching lines with context.

- **A combination** (e.g. `error ws_session`) — apply all applicable filters together.

## Core commands to build from:

```bash
# Tail last N lines
tail -n 50 storage/backend.log

# Filter by level
grep '"level": "ERROR"' storage/backend.log | tail -n 20

# Filter by logger name
grep '"logger": "backend.routers.ws_session"' storage/backend.log | tail -n 20

# Fuzzy filter by any field
grep 'fallback' storage/backend.log | tail -n 20

# Pretty-print a single line
tail -n 1 storage/backend.log | python -m json.tool

# Count errors in the last session
grep '"level": "ERROR"' storage/backend.log | wc -l
```

## If the log file doesn't exist:
Tell the user to restart the backend with `LOG_FILE=1`.

## General approach:
Parse the JSON, don't just dump raw lines at the user. Summarize patterns,
highlight errors and warnings, and call out anything that looks like a
provider failure, exception, or unexpected turn sequence.
