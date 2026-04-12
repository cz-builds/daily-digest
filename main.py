"""Daily Digest: personal AI-curated briefing on AI, Hardware & Space."""
import os
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

import db
from fetch import fetch_all
from process import score_all_unscored, prepare_top_items
from render import render
from send import send_digest


def main():
    db.init()

    print("=== Step 1: Fetch ===")
    fetch_all()

    print("\n=== Step 2: Score ===")
    score_all_unscored()

    print("\n=== Step 3: Summarize Top Items ===")
    items = prepare_top_items(n=10)
    if not items:
        print("[digest] No items passed scoring. Done.")
        return

    print(f"\n=== Step 4: Render ({len(items)} items) ===")
    subject, html = render(items)
    print(f"Subject: {subject}")

    print("\n=== Step 5: Send ===")
    to_email = os.environ.get("DIGEST_EMAIL", "")
    if to_email:
        send_digest(to_email, subject, html)
    else:
        print("[send] DIGEST_EMAIL not set, skipping send")

    # Mark as sent
    issue_id = datetime.now(timezone.utc).strftime("%Y%m%d")
    db.mark_sent([it["id"] for it in items], issue_id)

    print("\n=== Done ===")


if __name__ == "__main__":
    main()
