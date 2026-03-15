# Deployment Roadmap

A step-by-step path from the current local dev setup to a live, internet-facing server.
Items are ordered; later steps depend on earlier ones.

---

## Infrastructure decisions (decide first, they shape everything else)

### Server

The backend loads **Kokoro TTS** at startup and **Whisper STT** lazily — both are CPU/GPU
bound.  Choose accordingly:

| Option | Cost | Notes |
|--------|------|-------|
| GPU VPS (Lambda Labs, RunPod, Vast.ai) | ~$0.50–1.50/hr | Best TTS/STT latency; pay-as-you-go; no long-term commitment |
| CPU VPS with ≥8 GB RAM (Hetzner CX32, DigitalOcean Premium CPU) | ~$20–40/mo | Acceptable for low-traffic; Kokoro on CPU is ~1–3x real-time |
| Bare metal / home server | hardware cost | Highest performance per dollar; no egress costs; no uptime SLA |

Recommended for initial launch: **Hetzner CPX41** (8 vCPU, 16 GB RAM, €26/mo) or a
GPU instance if TTS latency matters.

### Domain + TLS

- Register a domain (e.g. `yourdomain.com`).
- Point an A record to the server IP.
- Get a TLS certificate via **Let's Encrypt / certbot** (free, auto-renew).
- nginx handles TLS termination; both Python services run on loopback.

### Process management

Use **systemd** (two units: `pdf-frontend.service`, `pdf-backend.service`).  Simple,
reliable, handles auto-restart and journald logging without Docker overhead.

---

## Phase 1 — Security fixes (must be done before any public traffic)

These are critical gaps from GAP_ANALYSIS.md.  Estimated total: ~6 hours.

| # | Gap | Fix | Est. |
|---|-----|-----|------|
| G1 | `/api/usage` endpoints unauthenticated | Add `X-Session-Id` header check to `frontend/routers/usage.py` (mirror `_require_session` from `lessons.py`) | 30 min |
| G3 | No rate limit on `/auth/login` and `/auth/register` | Add `_login_limiter` (5 attempts / 5 min per email) and `_register_limiter` (10 / hr per IP) in `frontend/routers/auth.py` | 2 hr |
| G6 | `lesson_id` injected unencoded into WS backend URL | Use `urllib.parse.urlencode({"lesson_id": lesson_id})` in `frontend/routers/ws_proxy.py:68`; validate UUID format | 30 min |
| G7 | `run_code` payload has no size limit | Add `if len(code) > 100_000: return error` in `backend/routers/ws_session.py`; cap output at 1 MB | 1 hr |
| G2 | `run_code` has no concurrency limit | Add `asyncio.Semaphore(3)` per session in `SessionState`; reject immediately if full | 1 hr |
| G9 | No password reset flow | Add `/auth/forgot-password` + `/auth/reset-password` endpoints using existing verification token infrastructure | 4 hr |

Do **G1, G3, G6, G7, G2** as a block first (blockers for any external traffic).
G9 (password reset) can follow before marketing the app publicly.

---

## Phase 2 — Server provisioning

### 2.1 — Create server and SSH access

```bash
# On your local machine:
ssh-keygen -t ed25519 -C "deploy@yourdomain.com" -f ~/.ssh/pdf_deploy
# Add ~/.ssh/pdf_deploy.pub to your VPS control panel (Hetzner / DigitalOcean / etc.)
ssh -i ~/.ssh/pdf_deploy root@<SERVER_IP>
```

### 2.2 — Base OS setup (Ubuntu 22.04 or 24.04)

```bash
apt update && apt upgrade -y
apt install -y nginx certbot python3-certbot-nginx git curl build-essential \
               libmagic1 pkg-config bubblewrap ffmpeg

# Create a dedicated non-root user
useradd -m -s /bin/bash appuser
su - appuser
```

### 2.3 — Install uv and Python

```bash
# As appuser:
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.local/bin/env  # or add to .bashrc
```

### 2.4 — Install Node 20 (for build step only — not needed at runtime)

```bash
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
source ~/.nvm/nvm.sh
nvm install 20
```

---

## Phase 3 — Application deployment

### 3.1 — Clone and install

```bash
# As appuser on the server:
git clone <your-repo-url> ~/app
cd ~/app

uv sync              # installs Python deps into .venv
source .venv/bin/activate

# Build the React client
source ~/.nvm/nvm.sh && nvm use 20
cd client && npm ci && npm run build   # outputs to ../frontend/static/
cd ..
```

### 3.2 — Create `.env` file

```bash
cat > ~/app/.env << 'EOF'
ENV=production
ANTHROPIC_API_KEY=sk-ant-...
RESEND_API_KEY=re_...
FROM_EMAIL=noreply@yourdomain.com
APP_URL=https://yourdomain.com
ALLOWED_ORIGINS=https://yourdomain.com
STORAGE_DIR=/var/lib/pdf-to-audio/storage
DB_PATH=/var/lib/pdf-to-audio/storage/db.sqlite3
STT_MODEL_SIZE=medium
LLM_MODEL=claude-sonnet-4-6
DEFAULT_VOICE=af_heart
EOF
chmod 600 ~/app/.env

# Create storage dir (needs to survive re-deploys)
sudo mkdir -p /var/lib/pdf-to-audio/storage
sudo chown appuser:appuser /var/lib/pdf-to-audio/storage
```

### 3.3 — Systemd service units

Create `/etc/systemd/system/pdf-backend.service`:

```ini
[Unit]
Description=pdf-to-audio Backend
After=network.target
Wants=network.target

[Service]
Type=simple
User=appuser
WorkingDirectory=/home/appuser/app
EnvironmentFile=/home/appuser/app/.env
ExecStart=/home/appuser/app/.venv/bin/python -m backend.main
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Create `/etc/systemd/system/pdf-frontend.service`:

```ini
[Unit]
Description=pdf-to-audio Frontend BFF
After=pdf-backend.service
Requires=pdf-backend.service

[Service]
Type=simple
User=appuser
WorkingDirectory=/home/appuser/app
EnvironmentFile=/home/appuser/app/.env
ExecStart=/home/appuser/app/.venv/bin/python -m frontend.main
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable pdf-backend pdf-frontend
sudo systemctl start pdf-backend pdf-frontend
sudo systemctl status pdf-backend pdf-frontend
```

---

## Phase 4 — TLS + nginx

### 4.1 — Obtain certificate

```bash
# Temporarily stop nginx if running; open port 80:
sudo certbot certonly --standalone -d yourdomain.com
```

### 4.2 — nginx config

Create `/etc/nginx/sites-available/pdf-to-audio`:

```nginx
server {
    listen 80;
    server_name yourdomain.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name yourdomain.com;

    ssl_certificate     /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

    # Security headers (complement what the BFF sends)
    add_header X-Frame-Options DENY;
    add_header Referrer-Policy strict-origin-when-cross-origin;

    client_max_body_size 50m;  # PDF uploads

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # WebSocket upgrade
    location /ws/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 3600s;   # hold WS connections open
        proxy_send_timeout 3600s;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/pdf-to-audio /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

---

## Phase 5 — Smoke test

Run these from your local machine (or the server's public IP):

```bash
# Health checks
curl https://yourdomain.com/health
curl http://127.0.0.1:8001/health  # backend (from the server)

# Auth round-trip
curl -X POST https://yourdomain.com/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"test@example.com","password":"Secret123!"}'

# Upload token (requires valid session)
# ... use the app UI for the rest
```

Verify in browser:
- [ ] Register, verify email, log in
- [ ] Upload a PDF and wait for decomposition
- [ ] Start a lesson, speak to the AI, receive audio response
- [ ] Code runner works (if applicable)
- [ ] TLS padlock shows; no mixed-content warnings

---

## Phase 6 — Operational basics

### Backup

SQLite is safe to copy if you use the backup API.  Run nightly via cron:

```bash
# /etc/cron.d/pdf-backup
0 3 * * * appuser sqlite3 /var/lib/pdf-to-audio/storage/db.sqlite3 ".backup '/var/lib/pdf-to-audio/backups/db-$(date +\%Y\%m\%d).sqlite3'"
# Prune backups older than 30 days
0 4 * * * appuser find /var/lib/pdf-to-audio/backups -name "*.sqlite3" -mtime +30 -delete
```

Also back up the `storage/{user_id}/pdfs/` directory to an object store (rclone to S3 or
Backblaze B2) — PDFs are not recoverable if lost.

### Monitoring

Minimal but useful:

1. **UptimeRobot** (free) — ping `https://yourdomain.com/health` every 5 min; alerts
   you if the BFF dies.
2. **Journald** — `journalctl -u pdf-backend -f` and `journalctl -u pdf-frontend -f`
   for real-time logs.
3. **Disk usage** — SQLite and PDFs grow unbounded until G13 (per-user quota) is
   implemented.  Set a cron alert: `df -h | awk '$5 > 80 {print}'` and email yourself.

### Certificate renewal

certbot auto-renews, but test it:

```bash
sudo certbot renew --dry-run
```

Add a deploy hook to reload nginx after renewal:
`/etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh` containing `systemctl reload nginx`.

---

## Phase 7 — First-user hardening (before marketing)

Once the app is running and you've validated it personally:

| Item | Where documented |
|------|-----------------|
| Password reset flow (G9) | GAP_ANALYSIS.md |
| BFF↔backend shared secret (G10) | GAP_ANALYSIS.md |
| Session expiry cleanup job (G5) | GAP_ANALYSIS.md |
| Internal error message scrubbing (G11) | GAP_ANALYSIS.md |
| Upload token TTL cap (G18) | GAP_ANALYSIS.md |
| Disable backend `/docs` in production (G17) | GAP_ANALYSIS.md |
| Thread-leak mitigation for agent timeouts (G4) | GAP_ANALYSIS.md |

---

## Deployment checklist (print and tick)

**Before first deploy:**
- [ ] G1 — `/api/usage` requires session
- [ ] G3 — login and register are rate-limited
- [ ] G6 — `lesson_id` URL-encoded in WS proxy
- [ ] G7 — `run_code` payload size capped
- [ ] G2 — `run_code` concurrency limited per session
- [ ] `ENV=production` set in `.env`
- [ ] `ALLOWED_ORIGINS` set to actual domain
- [ ] `ANTHROPIC_API_KEY` set
- [ ] `RESEND_API_KEY` set (or accept email verification in logs for now)
- [ ] `APP_URL` set to `https://yourdomain.com`
- [ ] `STORAGE_DIR` pointed to persistent volume (not inside app directory)
- [ ] systemd units enabled and started
- [ ] nginx passes `nginx -t`
- [ ] TLS cert issued and https loads
- [ ] `bubblewrap` / `bwrap` installed on server (code runner sandbox)
- [ ] Backup cron jobs scheduled

**Before inviting external users:**
- [ ] G9 — password reset works end-to-end
- [ ] G10 — BFF↔backend shared secret in place
- [ ] G5 — session expiry job running
- [ ] Monitoring / uptime alerting configured
- [ ] Privacy policy / ToS page exists (legal requirement for email collection)

---

*Created: 2026-03-14.*
