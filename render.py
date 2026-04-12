"""Render the digest as bilingual HTML email."""
from datetime import datetime, timezone
from jinja2 import Template

TEMPLATE = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Daily Digest — {{ date }}</title></head>
<body style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;max-width:640px;margin:0 auto;padding:24px;color:#222;line-height:1.6;background:#fff;">

<div style="border-bottom:2px solid #111;padding-bottom:12px;margin-bottom:24px;">
  <h1 style="margin:0;font-size:20px;">Daily Digest</h1>
  <p style="margin:4px 0 0;color:#666;font-size:13px;">{{ date }} · {{ items|length }} items · AI / Hardware / Space</p>
</div>

{% for cat, cat_items in grouped.items() %}
<div style="margin-bottom:28px;">
  <h2 style="font-size:13px;text-transform:uppercase;color:#888;letter-spacing:1px;border-bottom:1px solid #eee;padding-bottom:4px;margin:0 0 12px;">{{ cat }}</h2>
  {% for it in cat_items %}
  <div style="margin-bottom:22px;">
    <h3 style="font-size:15px;margin:0 0 2px;">
      <a href="{{ it.url }}" style="color:#111;text-decoration:none;">{{ it.title }}</a>
    </h3>
    {% if it.title_zh %}
    <p style="margin:0 0 6px;font-size:13px;color:#666;">{{ it.title_zh }}</p>
    {% endif %}
    <p style="margin:0 0 4px;color:#444;font-size:13px;">{{ it.summary_en or it.summary }}</p>
    {% if it.summary_zh %}
    <p style="margin:0 0 6px;color:#555;font-size:13px;">{{ it.summary_zh }}</p>
    {% endif %}
    {% if it.why_care %}
    <p style="margin:0 0 2px;color:#2a7ae2;font-size:12px;">→ {{ it.why_care }}</p>
    {% endif %}
    {% if it.why_care_zh %}
    <p style="margin:0 0 4px;color:#2a7ae2;font-size:12px;">→ {{ it.why_care_zh }}</p>
    {% endif %}
    <p style="margin:0;font-size:11px;color:#aaa;">{{ it.source }} · <a href="{{ it.url }}" style="color:#999;">Read full article →</a></p>
  </div>
  {% endfor %}
</div>
{% endfor %}

<hr style="border:none;border-top:1px solid #eee;margin:24px 0 12px;">
<p style="font-size:11px;color:#aaa;text-align:center;">
  Auto-curated from {{ sources_count }} sources by Gemini. Delivered at 06:00 Beijing time.
</p>
</body>
</html>
"""


def render(items):
    date = datetime.now(timezone.utc).strftime("%B %d, %Y")

    grouped = {}
    for it in items:
        cat = it.get("category") or "Other"
        grouped.setdefault(cat, []).append(it)

    order = ["AI Research", "AI & Tech", "Hardware", "Space", "Other"]
    sorted_grouped = {}
    for cat in order:
        if cat in grouped:
            sorted_grouped[cat] = grouped[cat]
    for cat in grouped:
        if cat not in sorted_grouped:
            sorted_grouped[cat] = grouped[cat]

    sources_count = len(set(it["source"] for it in items))
    html = Template(TEMPLATE).render(
        date=date, items=items, grouped=sorted_grouped, sources_count=sources_count
    )
    headline = items[0]["title"][:50] if items else "Today"
    subject = f"Daily Digest · {date} · {headline}"
    return subject, html
