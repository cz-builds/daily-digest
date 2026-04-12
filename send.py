"""Send digest email via Resend."""
import os
import resend

resend.api_key = os.environ.get("RESEND_API_KEY", "")


def send_digest(to_email, subject, html):
    if not resend.api_key:
        print("[send] RESEND_API_KEY not set, skipping")
        return False
    try:
        resend.Emails.send({
            "from": os.environ.get("FROM_EMAIL", "Daily Digest <digest@pandabrief.com>"),
            "to": [to_email],
            "subject": subject,
            "html": html,
        })
        print(f"[send] Delivered to {to_email}")
        return True
    except Exception as e:
        print(f"[send] Failed: {e}")
        return False
