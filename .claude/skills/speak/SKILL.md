---
name: speak
description: Generate text and speak it aloud using the OpenAI TTS API. Plays audio through the system speaker.
allowed-tools: Bash
---

Produce a response as text, then speak it using the OpenAI TTS API.

## Steps

1. Decide what to say. If $ARGUMENTS is provided, use it as the topic or content to speak. Otherwise say something contextually appropriate or creative.

2. Compose the spoken text (keep it natural, conversational — this is audio, not markdown).

3. Call the OpenAI TTS API and play the result:

```bash
set -a; source .env 2>/dev/null; set +a
python3 - <<'PYEOF'
import os, subprocess, urllib.request, json, tempfile

text = """SPOKEN_TEXT_HERE"""

api_key = os.getenv("OPENAI_API_KEY", "")

payload = json.dumps({
    "model": "tts-1",
    "input": text,
    "voice": "nova",
    "response_format": "mp3",
}).encode()

req = urllib.request.Request(
    "https://api.openai.com/v1/audio/speech",
    data=payload,
    headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    },
)

with urllib.request.urlopen(req) as resp:
    audio = resp.read()

with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
    f.write(audio)
    tmp = f.name

for player in ["mpg123", "ffplay", "aplay", "paplay", "cvlc"]:
    if subprocess.run(["which", player], capture_output=True).returncode == 0:
        args = [player]
        if player == "ffplay":
            args += ["-nodisp", "-autoexit", "-loglevel", "quiet"]
        if player in ("cvlc",):
            args += ["--play-and-exit", "--quiet"]
        args.append(tmp)
        subprocess.run(args)
        break
else:
    print(f"Audio saved to {tmp} — no player found (install mpg123 or ffplay).")

PYEOF
```

4. Replace `SPOKEN_TEXT_HERE` with the actual text before running. Keep the text clean — no markdown, no asterisks, no bullet points.

## Voice options
Default voice is `nova` (warm, clear). Others: `alloy`, `echo`, `fable`, `onyx`, `shimmer`.
To use a different voice, pass it as part of $ARGUMENTS (e.g. `/speak onyx tell me about the weather`).

## If no audio player is found
Suggest: `sudo apt install mpg123`
