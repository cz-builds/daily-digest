"""Fetch RSS feeds and store new items."""
import os
import feedparser
import requests
import yaml
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from time import mktime

import db

MAX_AGE_HOURS = int(os.environ.get("MAX_AGE_HOURS", "48"))
SOURCES_FILE = Path(__file__).parent / "sources.yaml"
UA = "Mozilla/5.0 (compatible; DailyDigestBot/1.0)"


def load_sources():
    with open(SOURCES_FILE, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _fetch_one(url):
    resp = requests.get(url, timeout=15, headers={"User-Agent": UA})
    resp.raise_for_status()
    return feedparser.parse(resp.content)


def _parse_pub_date(entry):
    raw = entry.get("published") or entry.get("updated") or ""
    if not raw:
        ts = entry.get("published_parsed") or entry.get("updated_parsed")
        if ts:
            return datetime.fromtimestamp(mktime(ts), tz=timezone.utc)
        return None
    try:
        return parsedate_to_datetime(raw)
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(raw, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


def fetch_all():
    cfg = load_sources()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)
    total_new = 0
    total_stale = 0
    for src in cfg.get("rss", []):
        try:
            feed = _fetch_one(src["url"])
        except Exception as e:
            print(f"[fetch] {src['name']} failed: {e}")
            continue
        new = 0
        stale = 0
        for entry in feed.entries[:30]:
            title = (entry.get("title") or "").strip()
            url = (entry.get("link") or "").strip()
            if not title or not url:
                continue
            pub_date = _parse_pub_date(entry)
            if pub_date and pub_date < cutoff:
                stale += 1
                continue
            summary = (entry.get("summary") or "")[:1500]
            published = entry.get("published") or entry.get("updated") or ""
            db.upsert_item(src["name"], src.get("category", ""), title, url, summary, published)
            new += 1
        if stale:
            print(f"[fetch] {src['name']}: {new} fresh, {stale} stale skipped")
        else:
            print(f"[fetch] {src['name']}: {new} items")
        total_new += new
        total_stale += stale
    print(f"[fetch] Total: {total_new} fresh, {total_stale} stale skipped")
    return total_new
