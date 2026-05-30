"""
email_service.py — Transactional email delivery via Resend API

Handles:
  - Absence notifications to parents
  - Student welcome / login setup emails
  - Admin email audit logging to Firestore

Setup:
  1. Sign up free at https://resend.com
  2. Get your API key
  3. Add to .env:  RESEND_API_KEY=re_xxxxx
  4. Optional:     EMAIL_FROM=attendance@yourdomain.com
                  PORTAL_URL=https://smart-attendance-ai-7139f.web.app
"""

import os
import requests
from datetime import datetime

# ── Config (loaded from .env via app.py) ────────────────────────────────────
RESEND_API_KEY  = os.environ.get("RESEND_API_KEY", "")
EMAIL_FROM      = os.environ.get("EMAIL_FROM", "onboarding@resend.dev")
PORTAL_URL      = os.environ.get("PORTAL_URL", "https://smart-attendance-ai-7139f.web.app")
_RESEND_ENDPOINT = "https://api.resend.com/emails"


# ════════════════════════════════════════════════════════════════════════════
#  CORE SENDER
# ════════════════════════════════════════════════════════════════════════════

def _send(to: str, subject: str, html: str) -> tuple[bool, str]:
    """
    Send one email via Resend REST API.
    Returns (success, message_id_or_error).
    Gracefully degrades when RESEND_API_KEY is not set.
    """
    if not RESEND_API_KEY:
        print(f"[EMAIL] RESEND_API_KEY not configured — skipping to {to} | {subject}")
        return False, "not_configured"

    if not to or "@" not in to:
        print(f"[EMAIL] Invalid recipient address: '{to}'")
        return False, "invalid_address"

    try:
        resp = requests.post(
            _RESEND_ENDPOINT,
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type":  "application/json",
            },
            json={
                "from":    EMAIL_FROM,
                "to":      [to],
                "subject": subject,
                "html":    html,
            },
            timeout=12,
        )

        if resp.status_code in (200, 201, 202):
            msg_id = resp.json().get("id", "sent")
            print(f"[EMAIL] Delivered ✓ → {to} | id={msg_id} | {subject}")
            return True, msg_id

        error = resp.text[:300]
        print(f"[EMAIL] API error {resp.status_code} → {to} | {error}")
        return False, f"http_{resp.status_code}:{error[:100]}"

    except requests.exceptions.Timeout:
        print(f"[EMAIL] Timeout sending to {to}")
        return False, "timeout"
    except Exception as exc:
        msg = str(exc)[:200]
        print(f"[EMAIL] Exception sending to {to}: {msg}")
        return False, msg


# ════════════════════════════════════════════════════════════════════════════
#  EMAIL TEMPLATES
# ════════════════════════════════════════════════════════════════════════════

def _base_html(content: str) -> str:
    """Wraps email body in a branded, mobile-friendly shell."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  body{{margin:0;padding:0;background:#f1f5f9;font-family:Inter,Arial,sans-serif;}}
  .wrapper{{max-width:540px;margin:32px auto;border-radius:14px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,.1);}}
  .header{{background:linear-gradient(135deg,#0f172a 0%,#1e3a5f 100%);padding:28px 32px;}}
  .header h1{{color:#38bdf8;margin:0;font-size:20px;font-weight:700;letter-spacing:.4px;}}
  .header p{{color:#94a3b8;margin:4px 0 0;font-size:12px;}}
  .body{{background:#fff;padding:28px 32px;}}
  .info-table{{width:100%;border-collapse:collapse;margin:16px 0;background:#f8fafc;border-radius:10px;overflow:hidden;}}
  .info-table td{{padding:10px 14px;font-size:13px;color:#475569;border-bottom:1px solid #e2e8f0;}}
  .info-table td:first-child{{font-weight:600;width:120px;color:#334155;}}
  .info-table tr:last-child td{{border-bottom:none;}}
  .status-absent{{color:#ef4444;font-weight:700;}}
  .footer{{background:#f8fafc;padding:14px 32px;border-top:1px solid #e2e8f0;}}
  .footer p{{color:#94a3b8;font-size:11px;margin:0;}}
  .btn{{display:inline-block;margin-top:20px;padding:11px 24px;background:#38bdf8;color:#0f172a;border-radius:8px;text-decoration:none;font-weight:700;font-size:13px;}}
</style>
</head>
<body>
<div class="wrapper">
  <div class="header">
    <h1>Smart AI Attendance</h1>
    <p>Automated Notification System</p>
  </div>
  <div class="body">{content}</div>
  <div class="footer">
    <p>This is an automated message from the Smart AI Attendance System. Please do not reply to this email.</p>
  </div>
</div>
</body>
</html>"""


def send_absence_notification(
    parent_email: str,
    student_name: str,
    subject_name: str,
    class_id: str,
    date_str: str,
) -> tuple[bool, str]:
    """
    Send an absence alert to a parent/guardian.
    Returns (success, message_id_or_error).
    """
    content = f"""
    <p style="color:#334155;font-size:15px;line-height:1.7;margin:0 0 16px;">
        Dear Parent / Guardian,
    </p>
    <p style="color:#334155;font-size:15px;line-height:1.7;margin:0 0 20px;">
        <strong>{student_name}</strong> was marked
        <strong class="status-absent">Absent</strong>
        for today's class. Please review the details below.
    </p>
    <table class="info-table">
        <tr><td>Student</td><td><strong>{student_name}</strong></td></tr>
        <tr><td>Class</td><td>{class_id}</td></tr>
        <tr><td>Subject</td><td>{subject_name}</td></tr>
        <tr><td>Date</td><td>{date_str}</td></tr>
        <tr><td>Status</td><td class="status-absent">Absent</td></tr>
    </table>
    <p style="color:#64748b;font-size:13px;line-height:1.6;margin:16px 0 0;">
        If you believe this is an error, please contact your institution's administrative office.
    </p>
    """
    email_subject = f"Attendance Alert — {student_name} was absent on {date_str}"
    return _send(parent_email, email_subject, _base_html(content))


def send_student_welcome(
    student_email: str,
    student_name: str,
    portal_url: str = PORTAL_URL,
) -> tuple[bool, str]:
    """
    Send a welcome / login-setup email to a newly enrolled student.
    The actual password reset link comes from Firebase Auth separately.
    This is an informational welcome email.
    """
    content = f"""
    <p style="color:#334155;font-size:15px;line-height:1.7;margin:0 0 16px;">
        Hi <strong>{student_name}</strong>,
    </p>
    <p style="color:#334155;font-size:15px;line-height:1.7;margin:0 0 20px;">
        Your Smart AI Attendance account has been created.
        You will shortly receive a separate email with a link to set up your password.
    </p>
    <p style="color:#334155;font-size:14px;line-height:1.7;margin:0 0 16px;">
        Once your password is set, you can access the student attendance portal:
    </p>
    <a class="btn" href="{portal_url}/student/mark.html">Open Attendance Portal</a>
    <p style="color:#64748b;font-size:12px;line-height:1.6;margin:20px 0 0;">
        Use this link only during active class sessions authorised by your teacher.
    </p>
    """
    email_subject = "Your Smart AI Attendance Account is Ready"
    return _send(student_email, email_subject, _base_html(content))


def send_student_onboarding_email(
    student_email: str,
    student_name: str,
    reset_link: str,
) -> tuple[bool, str]:
    """
    Send an email containing a secure out-of-band password reset link via Resend.
    """
    content = f"""
    <p style="color:#334155;font-size:15px;line-height:1.7;margin:0 0 16px;">
        Hi <strong>{student_name}</strong>,
    </p>
    <p style="color:#334155;font-size:15px;line-height:1.7;margin:0 0 20px;">
        Your Smart AI Attendance student login has been successfully created.
        To set up your password and log in, please click the button below:
    </p>
    <a class="btn" href="{reset_link}" style="background:#38bdf8;color:#0f172a;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:700;font-size:13px;display:inline-block;">Set Up Password</a>
    <p style="color:#64748b;font-size:12px;line-height:1.6;margin:24px 0 0;">
        This link is secure. If the button above does not work, copy and paste this URL into your browser:<br/>
        <span style="word-break:break-all;color:#38bdf8;">{reset_link}</span>
    </p>
    """
    email_subject = "Set Up Your Smart AI Attendance Account"
    return _send(student_email, email_subject, _base_html(content))



# ════════════════════════════════════════════════════════════════════════════
#  FIRESTORE EMAIL AUDIT LOG
# ════════════════════════════════════════════════════════════════════════════

def log_email(
    db,
    email_type: str,
    recipient: str,
    success: bool,
    msg_id_or_error: str,
    extra: dict | None = None,
):
    """
    Write a delivery record to Firestore `email_logs` collection for audit/debug.

    email_type: "absence_notification" | "student_welcome" | "password_reset"
    """
    if not db:
        return
    try:
        from firebase_admin import firestore as _fs
        record = {
            "type":       email_type,
            "recipient":  recipient,
            "status":     "sent" if success else "failed",
            "ref":        msg_id_or_error,
            "sentAt":     _fs.SERVER_TIMESTAMP,
        }
        if extra:
            record.update(extra)
        db.collection("email_logs").add(record)
    except Exception as exc:
        print(f"[EMAIL] Log write failed: {exc}")
