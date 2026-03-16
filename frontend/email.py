"""
Email sending for the frontend server.

When RESEND_API_KEY is configured, sends real emails via the Resend API.
When not configured (dev / test), logs the verification URL to stdout so
developers can complete the flow without an email provider.
"""

from __future__ import annotations

import asyncio
import logging

log = logging.getLogger(__name__)


async def send_verification_email(to_email: str, verify_url: str) -> None:
    """Send an email verification link to the user."""
    from .config import settings

    if not settings.RESEND_API_KEY:
        # Dev mode: print link so it can be copy-pasted.
        log.warning(
            "[EMAIL DEV MODE] Verification link for %s:\n  %s",
            to_email,
            verify_url,
        )
        print(f"\n[EMAIL DEV] Verify {to_email}:\n  {verify_url}\n", flush=True)
        return

    await asyncio.to_thread(_send_via_resend, to_email, verify_url, settings)


async def send_password_reset_email(to_email: str, reset_url: str) -> None:
    """Send a password reset link to the user."""
    from .config import settings

    if not settings.RESEND_API_KEY:
        log.warning(
            "[EMAIL DEV MODE] Password reset link for %s:\n  %s",
            to_email,
            reset_url,
        )
        print(f"\n[EMAIL DEV] Reset password for {to_email}:\n  {reset_url}\n", flush=True)
        return

    await asyncio.to_thread(_send_reset_via_resend, to_email, reset_url, settings)


def _send_reset_via_resend(to_email: str, reset_url: str, settings) -> None:
    import resend

    resend.api_key = settings.RESEND_API_KEY
    resend.Emails.send({
        "from": settings.FROM_EMAIL,
        "to": [to_email],
        "subject": "Reset your password — Tutorail",
        "html": (
            "<p>We received a request to reset your Tutorail password.</p>"
            "<p>Click the link below to set a new password. "
            "The link expires in 1 hour.</p>"
            f'<p><a href="{reset_url}">Reset my password</a></p>'
            f"<p>Or copy this URL: {reset_url}</p>"
            "<p>If you didn't request this, you can safely ignore this email.</p>"
        ),
    })


def _send_via_resend(to_email: str, verify_url: str, settings) -> None:
    import resend

    resend.api_key = settings.RESEND_API_KEY
    resend.Emails.send({
        "from": settings.FROM_EMAIL,
        "to": [to_email],
        "subject": "Verify your email — Tutorail",
        "html": (
            "<p>Thanks for signing up for Tutorail!</p>"
            "<p>Click the link below to verify your email address. "
            "The link expires in 24 hours.</p>"
            f'<p><a href="{verify_url}">Verify my email</a></p>'
            f"<p>Or copy this URL: {verify_url}</p>"
        ),
    })
