"""Send digest email via Resend, with per-recipient unsubscribe support.

Unsubscribe flow:
- Each email contains a per-recipient link to pandabrief.com/api/digest-unsubscribe?e=<email>&t=<token>
- Token is HMAC-SHA256(email, UNSUBSCRIBE_SECRET) truncated to 16 bytes, base64url
- Same UNSUBSCRIBE_SECRET is used by PandaBrief's Cloudflare endpoint to validate
- Blocked emails are stored in DIGEST_UNSUBSCRIBED KV namespace
- get_blocklist() fetches the current blocklist via a protected API
"""
import base64
import hashlib
import hmac
import os
from urllib.parse import quote

import requests
import resend

resend.api_key = os.environ.get("RESEND_API_KEY", "")


# Where the unsubscribe page + API live (PandaBrief Cloudflare Pages site).
def _unsub_base() -> str:
    return os.environ.get("UNSUB_BASE_URL", "https://pandabrief.com").rstrip("/")


def _unsub_token(email: str, secret: str) -> str:
    """HMAC-SHA256(email, secret) truncated to 16 bytes, base64url encoded.
    Must match functions/api/digest-unsubscribe.js on the PandaBrief side.
    """
    mac = hmac.new(secret.encode("utf-8"), email.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac[:16]).rstrip(b"=").decode("ascii")


def unsubscribe_url(email: str) -> str:
    """Build a per-recipient unsubscribe URL. Empty string if not configured."""
    secret = os.environ.get("UNSUBSCRIBE_SECRET", "")
    if not secret:
        return ""
    token = _unsub_token(email.strip().lower(), secret)
    return f"{_unsub_base()}/api/digest-unsubscribe?e={quote(email)}&t={token}"


def get_blocklist() -> set[str]:
    """Fetch the current unsubscribe blocklist from the protected API.
    Returns an empty set on any failure (fail-open is acceptable here — a
    transient outage shouldn't stop the whole digest; recipients can always
    click unsubscribe again).
    """
    secret = os.environ.get("SUBSCRIBERS_SECRET", "")
    if not secret:
        print("[blocklist] SUBSCRIBERS_SECRET not set, skipping blocklist filter")
        return set()
    url = f"{_unsub_base()}/api/digest-unsubscribed?key={secret}"
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        emails = {e.strip().lower() for e in data.get("emails", []) if e}
        print(f"[blocklist] Loaded {len(emails)} unsubscribed email(s)")
        return emails
    except Exception as e:
        print(f"[blocklist] Failed to fetch (fail-open): {e}")
        return set()


def send_digest(to_email: str, subject: str, html: str) -> bool:
    if not resend.api_key:
        print("[send] RESEND_API_KEY not set, skipping")
        return False
    try:
        unsub = unsubscribe_url(to_email)
        personalized_html = html.replace("__UNSUB_URL__", unsub or f"{_unsub_base()}/digest-unsubscribe.html")

        payload = {
            "from": os.environ.get("FROM_EMAIL", "Daily Digest <digest@pandabrief.com>"),
            "to": [to_email],
            "subject": subject,
            "html": personalized_html,
        }
        if unsub:
            # RFC 2369 + RFC 8058: Gmail/Outlook show a native "Unsubscribe" button
            payload["headers"] = {
                "List-Unsubscribe": f"<{unsub}>",
                "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
            }

        resend.Emails.send(payload)
        print(f"[send] Delivered to {to_email}")
        return True
    except Exception as e:
        print(f"[send] Failed: {e}")
        return False
