"""
Email sending for the frontend server.

When RESEND_API_KEY is configured, sends real emails via the Resend API.
When not configured (dev / test), logs the verification URL to stdout so
developers can complete the flow without an email provider.

Two transactional emails:
  1. Verification email  — sent on register/resend. Plain, minimal, no images.
                           Maximises deliverability; one job: get the link clicked.
  2. Welcome email       — sent once after the link is clicked. Contains the
                           getting-started screenshots as inline CID attachments
                           so they render without Gmail's "Display images" prompt.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

log = logging.getLogger(__name__)

_IMG_DIR = Path(__file__).parent / "static" / "images"

# (content_id, filename) for the 6 onboarding screenshots
_STEP_IMAGES: list[tuple[str, str]] = [
    ("step1", "Screenshot from 2026-03-15 21-55-30.png"),
    ("step2", "Screenshot from 2026-03-15 21-56-21.png"),
    ("step3", "Screenshot from 2026-03-15 21-57-22.png"),
    ("step4", "Screenshot from 2026-03-15 22-05-27.png"),
    ("step5", "Screenshot from 2026-03-15 22-11-22.png"),
    ("step6", "Screenshot from 2026-03-15 22-50-21.png"),
]


def _inline_attachments() -> list[dict]:
    attachments = []
    for cid, filename in _STEP_IMAGES:
        path = _IMG_DIR / filename
        if not path.exists():
            log.warning("Email image missing: %s", path)
            continue
        attachments.append({
            "filename": filename,
            "content": list(path.read_bytes()),
            "content_id": cid,
            "inline": True,
        })
    return attachments


# ── public API ─────────────────────────────────────────────────────────────────

async def send_verification_email(to_email: str, verify_url: str) -> None:
    """Minimal verification email — just the link, no images."""
    from .config import settings

    if not settings.RESEND_API_KEY:
        log.warning("[EMAIL DEV MODE] Verification link for %s:\n  %s", to_email, verify_url)
        print(f"\n[EMAIL DEV] Verify {to_email}:\n  {verify_url}\n", flush=True)
        return

    await asyncio.to_thread(_send_verification_via_resend, to_email, verify_url, settings)


async def send_welcome_email(to_email: str) -> None:
    """Onboarding email with screenshots — sent once after the user verifies."""
    from .config import settings

    if not settings.RESEND_API_KEY:
        log.info("[EMAIL DEV MODE] Welcome email would be sent to %s", to_email)
        return

    await asyncio.to_thread(_send_welcome_via_resend, to_email, settings)


async def send_password_reset_email(to_email: str, reset_url: str) -> None:
    from .config import settings

    if not settings.RESEND_API_KEY:
        log.warning("[EMAIL DEV MODE] Password reset link for %s:\n  %s", to_email, reset_url)
        print(f"\n[EMAIL DEV] Reset password for {to_email}:\n  {reset_url}\n", flush=True)
        return

    await asyncio.to_thread(_send_reset_via_resend, to_email, reset_url, settings)


# ── Resend senders ─────────────────────────────────────────────────────────────

def _send_verification_via_resend(to_email: str, verify_url: str, settings) -> None:
    import resend

    resend.api_key = settings.RESEND_API_KEY
    resend.Emails.send({
        "from": settings.FROM_EMAIL,
        "to": [to_email],
        "subject": "Verify your email — Tutorail",
        "text": (
            "Welcome to Tutorail!\n\n"
            "Click the link below to verify your email and activate your account.\n"
            "The link expires in 24 hours.\n\n"
            f"{verify_url}\n\n"
            "If you didn't sign up for Tutorail, you can safely ignore this email."
        ),
        "html": _verification_html(verify_url),
    })


def _send_welcome_via_resend(to_email: str, settings) -> None:
    import resend

    resend.api_key = settings.RESEND_API_KEY
    resend.Emails.send({
        "from": settings.FROM_EMAIL,
        "to": [to_email],
        "subject": "You're in — here's how to get started with Tutorail",
        "text": (
            "Welcome to Tutorail!\n\n"
            "Here's how to go from sign-in to your first AI-powered lesson in minutes:\n\n"
            "1. Your learning dashboard\n"
            "   After signing in you'll see your courses and individual lessons.\n\n"
            "2. Create a course (optional)\n"
            "   Group related lessons together under a course.\n\n"
            "3. Upload a PDF and start a lesson\n"
            "   Choose a PDF, give the lesson a name, and hit Upload & Start.\n\n"
            "4. Tutorail prepares your lesson\n"
            "   Your document is analysed and broken into sections tailored to your goals.\n\n"
            "5. Interactive exercises keep you engaged\n"
            "   Your tutor asks questions, sets timed challenges, and opens a sketchpad.\n\n"
            "6. Slides from your PDF appear in context\n"
            "   Tutorail shows the relevant source page right in the lesson.\n\n"
            "Head to https://tutorail.app to get started."
        ),
        "html": _welcome_html(),
        "attachments": _inline_attachments(),
    })


def _send_reset_via_resend(to_email: str, reset_url: str, settings) -> None:
    import resend

    resend.api_key = settings.RESEND_API_KEY
    resend.Emails.send({
        "from": settings.FROM_EMAIL,
        "to": [to_email],
        "subject": "Reset your password — Tutorail",
        "text": (
            "We received a request to reset your Tutorail password.\n\n"
            "Click the link below to set a new password. The link expires in 1 hour.\n\n"
            f"{reset_url}\n\n"
            "If you didn't request this, you can safely ignore this email."
        ),
        "html": (
            "<p>We received a request to reset your Tutorail password.</p>"
            "<p>Click the link below to set a new password. "
            "The link expires in 1 hour.</p>"
            f'<p><a href="{reset_url}">Reset my password</a></p>'
            f"<p>Or copy this URL: {reset_url}</p>"
            "<p>If you didn't request this, you can safely ignore this email.</p>"
        ),
    })


# ── HTML templates ─────────────────────────────────────────────────────────────

def _base_html(header_content: str, body_content: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f9fafb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <table cellpadding="0" cellspacing="0" border="0" width="100%" style="background:#f9fafb;padding:32px 16px;">
    <tr><td align="center">
      <table cellpadding="0" cellspacing="0" border="0" width="600"
             style="background:#fff;border-radius:12px;border:1px solid #e5e7eb;overflow:hidden;max-width:600px;">
        <tr>
          <td style="background:#2563eb;padding:28px 40px;">
            <p style="margin:0;font-size:22px;font-weight:700;color:#fff;letter-spacing:-0.3px;">Tutorail</p>
            {header_content}
          </td>
        </tr>
        {body_content}
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


def _verification_html(verify_url: str) -> str:
    body = f"""
        <tr><td style="padding:32px 40px 40px 40px;">
          <h1 style="margin:0 0 8px 0;font-size:22px;font-weight:700;color:#111;">Verify your email</h1>
          <p style="margin:0 0 24px 0;font-size:15px;color:#555;line-height:1.6;">
            Thanks for signing up! Click the button below to activate your account.
            The link expires in <strong>24 hours</strong>.
          </p>
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
            Or copy this URL into your browser:<br>
            <a href="{verify_url}" style="color:#2563eb;word-break:break-all;">{verify_url}</a>
          </p>
          <p style="margin:20px 0 0 0;font-size:13px;color:#888;line-height:1.5;">
            Can't find this email? Check your <strong>spam or junk folder</strong> —
            first-time senders sometimes land there.
          </p>
        </td></tr>"""
    return _base_html("", body)


def _step(number: str, title: str, body: str, cid: str) -> str:
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
        <img src="cid:{cid}" alt="{title}" width="540"
             style="display:block;width:100%;max-width:540px;border:1px solid #e5e7eb;border-radius:8px;" />
      </td>
    </tr>"""


def _welcome_html() -> str:
    steps = (
        _step("1", "Your learning dashboard",
              "After signing in you'll see your courses and individual lessons. "
              'Click <strong>+ Add Lesson</strong> to get started, or create a course first.',
              "step1")
        + _step("2", "Create a course (optional)",
                "Give it a name and an optional description — useful for grouping a textbook's chapters "
                "or a series of related PDFs.",
                "step2")
        + _step("3", "Upload a PDF and start a lesson",
                "Choose a PDF from your computer, give the lesson a name, and hit "
                "<strong>Upload &amp; Start</strong>. Tutorail will ask about your goals, then build a personalised curriculum.",
                "step3")
        + _step("4", "Tutorail prepares your lesson",
                "Your document is analysed and broken into sections tailored to what you told us. "
                "Then your AI tutor takes over — ask questions, draw diagrams, run code, and more.",
                "step4")
        + _step("5", "Interactive exercises keep you engaged",
                "Your tutor doesn't just lecture — it asks questions, sets timed challenges, and "
                "opens a sketchpad when it wants you to draw or write something out.",
                "step5")
        + _step("6", "Slides from your PDF appear in context",
                "When it helps to see the source material, Tutorail displays the relevant page "
                "right in the lesson so you always know where the content comes from.",
                "step6")
    )

    body = f"""
        <tr><td style="padding:32px 40px 8px 40px;">
          <h1 style="margin:0 0 8px 0;font-size:22px;font-weight:700;color:#111;">You're all set — welcome!</h1>
          <p style="margin:0 0 0 0;font-size:15px;color:#555;line-height:1.6;">
            Here's how to go from sign-in to your first AI-powered lesson in minutes.
          </p>
        </td></tr>
        <tr><td style="padding:0 40px 32px 40px;">
          <table cellpadding="0" cellspacing="0" border="0" width="100%">
            {steps}
          </table>
        </td></tr>"""

    return _base_html(
        '<p style="margin:6px 0 0 0;font-size:14px;color:#93c5fd;">Your account is now active.</p>',
        body,
    )
