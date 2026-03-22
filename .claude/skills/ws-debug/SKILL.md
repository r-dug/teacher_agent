---
name: ws-debug
description: Tail and analyze the WebSocket debug log. Use when debugging message flow between client, BFF, and backend.
allowed-tools: Bash, Read
---

The WS debug log is at `storage/ws_debug.log`. It is only written when the
frontend server is started with `WS_DEBUG_LOG=1`.

## To start the frontend with logging enabled:
```bash
WS_DEBUG_LOG=1 /home/richard/.local/bin/uv run python -m frontend.main
```

## To read the last N frames:
```bash
tail -n ${ARGUMENTS:-50} storage/ws_debug.log | python -m json.tool
```

## To filter by direction:
```bash
grep 'c→b' storage/ws_debug.log | tail -n 20   # client → backend
grep 'b→c' storage/ws_debug.log | tail -n 20   # backend → client
```

## To filter by event type:
```bash
grep '"event": "$ARGUMENTS"' storage/ws_debug.log | tail -n 20
```

## To clear the log before a new session:
```bash
> storage/ws_debug.log
```

If $ARGUMENTS looks like an event name (e.g. "audio_chunk", "transcription"),
filter the log for that event. If $ARGUMENTS is a number, tail that many lines.
If $ARGUMENTS is empty, tail the last 50 lines and summarize what you see.
