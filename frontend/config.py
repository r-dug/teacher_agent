"""Frontend server configuration."""

from __future__ import annotations

import os


class Settings:
    HOST: str = os.getenv("FRONTEND_HOST", "0.0.0.0")
    PORT: int = int(os.getenv("FRONTEND_PORT", "8000"))

    # Set ENV=production to enable production safety checks.
    ENV: str = os.getenv("ENV", "development")

    BACKEND_HTTP: str = os.getenv("BACKEND_HTTP", "http://127.0.0.1:8001")
    BACKEND_WS: str = os.getenv("BACKEND_WS", "ws://127.0.0.1:8001")

    # Rate limiting: max requests per session before throttling
    RATE_LIMIT_CAPACITY: int = int(os.getenv("RATE_LIMIT_CAPACITY", "60"))
    RATE_LIMIT_REFILL: float = float(os.getenv("RATE_LIMIT_REFILL", "1.0"))  # tokens/sec

    # CORS: default to wildcard for LAN dev access.
    # In production, set ALLOWED_ORIGINS=https://yourapp.example.com (no wildcard).
    ALLOWED_ORIGINS: list[str] = os.getenv(
        "ALLOWED_ORIGINS", "*"
    ).split(",")

    # TLS: paths to cert/key for HTTPS.  When set, uvicorn serves wss:// so
    # the client can connect WebSockets directly without a Vite proxy.
    SSL_CERTFILE: str | None = os.getenv("SSL_CERTFILE", None)
    SSL_KEYFILE: str | None = os.getenv("SSL_KEYFILE", None)

    # Email / auth
    # When RESEND_API_KEY is absent, verification URLs are logged to stdout
    # instead of emailed (dev / test mode).
    RESEND_API_KEY: str | None = os.getenv("RESEND_API_KEY", None)
    FROM_EMAIL: str = os.getenv("FROM_EMAIL", "noreply@example.com")
    # Public URL of the frontend — used to build email verification links.
    APP_URL: str = os.getenv("APP_URL", "https://localhost:5173")


settings = Settings()
