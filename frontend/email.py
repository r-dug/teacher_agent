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
        "html": _verification_email_html(verify_url),
    })


_BASE = "https://tutorail.app"
_IMG = f"{_BASE}/images"


def _step(number: str, title: str, body: str, img_filename: str) -> str:
    img_url = f"{_IMG}/{img_filename.replace(' ', '%20')}"
    return f"""
    <tr>
      <td style="padding:24px 0 8px 0;">
        <table cellpadding="0" cellspacing="0" border="0" width="100%">
          <tr>
            <td style="width:28px;vertical-align:top;padding-top:2px;">
              <div style="width:24px;height:24px;border-radius:50%;background:#2563eb;color:#fff;
                          font-size:13px;font-weight:700;text-align:center;line-height:24px;">{number}</div>
            </td>
            <td style="padding-left:12px;">
              <p style="margin:0 0 4px 0;font-size:15px;font-weight:600;color:#111;">{title}</p>
              <p style="margin:0 0 12px 0;font-size:14px;color:#555;">{body}</p>
            </td>
          </tr>
        </table>
        <img src="{img_url}" alt="{title}" width="540"
             style="display:block;width:100%;max-width:540px;border:1px solid #e5e7eb;border-radius:8px;" />
      </td>
    </tr>"""


def _verification_email_html(verify_url: str) -> str:
    steps = (
        _step("1", "Your learning dashboard",
              "After signing in you'll see your courses and individual lessons. "
              'Click <strong>+ Add Lesson</strong> to get started, or create a course first to keep related lessons together.',
              "Screenshot from 2026-03-15 21-55-30.png")
        + _step("2", "Create a course (optional)",
                "Give it a name and an optional description — useful for grouping a textbook's chapters "
                "or a series of related PDFs.",
                "Screenshot from 2026-03-15 21-56-21.png")
        + _step("3", "Upload a PDF and start a lesson",
                "Choose a PDF from your computer, give the lesson a name, and hit "
                "<strong>Upload &amp; Start</strong>. Tutorail will ask about your goals, then build a personalised curriculum.",
                "Screenshot from 2026-03-15 21-57-22.png")
        + _step("4", "Tutorail prepares your lesson",
                "Your document is analysed and broken into sections tailored to what you told us. "
                "Then your AI tutor takes over — ask questions, draw diagrams, run code, and more.",
                "Screenshot from 2026-03-15 22-05-27.png")
        + _step("5", "Interactive exercises keep you engaged",
                "Your tutor doesn't just lecture — it asks questions, sets timed challenges, and "
                "opens a sketchpad when it wants you to draw or write something out.",
                "Screenshot from 2026-03-15 22-11-22.png")
        + _step("6", "Slides from your PDF appear in context",
                "When it helps to see the source material, Tutorail displays the relevant page "
                "right in the lesson so you always know where the content comes from.",
                "Screenshot from 2026-03-15 22-50-21.png")
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f9fafb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <table cellpadding="0" cellspacing="0" border="0" width="100%" style="background:#f9fafb;padding:32px 16px;">
    <tr><td align="center">
      <table cellpadding="0" cellspacing="0" border="0" width="600"
             style="background:#fff;border-radius:12px;border:1px solid #e5e7eb;overflow:hidden;max-width:600px;">

        <!-- Header -->
        <tr>
          <td style="background:#2563eb;padding:28px 40px;">
            <p style="margin:0;font-size:22px;font-weight:700;color:#fff;letter-spacing:-0.3px;">Tutorail</p>
          </td>
        </tr>

        <!-- Body -->
        <tr><td style="padding:32px 40px 8px 40px;">
          <h1 style="margin:0 0 8px 0;font-size:22px;font-weight:700;color:#111;">Welcome — please verify your email</h1>
          <p style="margin:0 0 24px 0;font-size:15px;color:#555;line-height:1.6;">
            Thanks for signing up! Click the button below to verify your address and activate your account.
            The link expires in <strong>24 hours</strong>.
          </p>

          <!-- CTA button -->
          <table cellpadding="0" cellspacing="0" border="0">
            <tr>
              <td style="border-radius:8px;background:#2563eb;">
                <a href="{verify_url}"
                   style="display:inline-block;padding:12px 28px;font-size:15px;font-weight:600;
                          color:#fff;text-decoration:none;border-radius:8px;">
                  Verify my email
                </a>
              </td>
            </tr>
          </table>

          <p style="margin:16px 0 0 0;font-size:13px;color:#888;">
            Or copy this URL: <a href="{verify_url}" style="color:#2563eb;word-break:break-all;">{verify_url}</a>
          </p>
        </td></tr>

        <!-- Divider -->
        <tr><td style="padding:32px 40px 0 40px;">
          <hr style="border:none;border-top:1px solid #e5e7eb;margin:0;" />
          <h2 style="margin:24px 0 0 0;font-size:17px;font-weight:700;color:#111;">Getting started</h2>
          <p style="margin:6px 0 0 0;font-size:14px;color:#555;">Here's how to go from sign-in to your first AI-powered lesson in minutes.</p>
        </td></tr>

        <!-- Steps -->
        <tr><td style="padding:0 40px 32px 40px;">
          <table cellpadding="0" cellspacing="0" border="0" width="100%">
            {steps}
          </table>
        </td></tr>

        <!-- Footer -->
        <tr>
          <td style="background:#f9fafb;border-top:1px solid #e5e7eb;padding:20px 40px;">
            <p style="margin:0;font-size:12px;color:#999;line-height:1.6;">
              You're receiving this because someone signed up for Tutorail with this address.
              If that wasn't you, you can safely ignore this email.
            </p>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""
