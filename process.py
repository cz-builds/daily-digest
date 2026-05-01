"""Score and summarize items using Gemini with model fallback chain."""
import json
import os
import re
import time

from google import genai

import db
from fetch import load_sources

_client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY", ""))

# Two chains tuned per task:
# - Scoring is bulk + low-stakes → prefer the model with the most generous
#   RPM/RPD limits (flash-lite: 30 RPM / 1500 RPD).
# - Summarizing is low-volume + quality-sensitive → start with the best model
#   and fall back to cheaper ones only if needed.
_SCORE_CHAIN = ["gemini-2.0-flash-lite", "gemini-2.0-flash", "gemini-2.5-flash"]
_SUMMARIZE_CHAIN = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.0-flash-lite"]

_RATE_LIMIT_MARKERS = ("429", "RESOURCE_EXHAUSTED", "quota", "rate_limit", "rate limit")
# Markers that *strongly* suggest the daily quota is exhausted (vs per-minute).
# Gemini's error message usually includes "PerDay" or "per day" for RPD breaches.
_DAILY_LIMIT_MARKERS = ("perday", "per day", "per-day", "daily")

# Models we've concluded are dead for the rest of this run (RPD exhausted).
# Module-level state, reset on each fresh process (i.e. each Actions run).
_exhausted_models: set[str] = set()


def _is_rate_limit(err: str) -> bool:
    e = err.lower()
    return any(m in e for m in _RATE_LIMIT_MARKERS)


def _is_daily_limit(err: str) -> bool:
    e = err.lower()
    return any(m in e for m in _DAILY_LIMIT_MARKERS)


def _chat(prompt, max_tokens=2000, max_retries=2, chain=None):
    """Call Gemini with automatic fallback.

    Rate-limit handling distinguishes per-minute from per-day:
      - per-minute (RPM/TPM): sleep ~65s and retry the SAME model once. If that
        also fails, mark exhausted for this run.
      - per-day (RPD): mark exhausted immediately, no point waiting.
    """
    chain = chain or _SUMMARIZE_CHAIN
    for model in chain:
        if model in _exhausted_models:
            continue

        rpm_retried = False
        for attempt in range(max_retries):
            try:
                resp = _client.models.generate_content(model=model, contents=prompt)
                return resp.text or ""
            except Exception as e:
                err = str(e)
                if _is_rate_limit(err):
                    if _is_daily_limit(err) or rpm_retried:
                        # Either explicitly daily, or we already waited and
                        # retried — give up on this model for the run.
                        _exhausted_models.add(model)
                        remaining = [m for m in chain if m not in _exhausted_models]
                        if remaining:
                            print(f"[llm] {model} exhausted → falling back to {remaining[0]}")
                        else:
                            print(f"[llm] all models exhausted")
                        break
                    # First 429 on this model: assume per-minute, sleep and retry once.
                    print(f"[llm] {model} rate-limited (likely per-minute), sleeping 65s and retrying...")
                    time.sleep(65)
                    rpm_retried = True
                    continue

                # Non-rate-limit error: short backoff, retry up to max_retries.
                if attempt < max_retries - 1:
                    wait = 2 ** attempt + 1
                    print(f"[llm] {model} error (attempt {attempt+1}/{max_retries}), retrying in {wait}s: {e}")
                    time.sleep(wait)
                else:
                    print(f"[llm] {model} failed after {max_retries} attempts: {e}")
                    break
    return ""


def _strip_fence(text):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text


def score_batch(items):
    cfg = load_sources()
    focus = "\n".join(f"- {f}" for f in cfg.get("focus_areas", []))
    payload = [
        {"id": it["id"], "title": it["title"], "source": it["source"],
         "snippet": (it.get("summary") or "")[:300]}
        for it in items
    ]
    prompt = f"""You are curating a daily digest for a student studying IC design who is deeply interested in AI, hardware/semiconductors, and space technology.

Focus areas:
{focus}

Score each item from 1 to 10:
- 10 = must-read breakthrough, will change how I think or what I build
- 7-9 = highly relevant, novel insight, important industry move
- 4-6 = somewhat interesting but common knowledge or tangential
- 1-3 = noise, clickbait, off-topic, or something I can easily find elsewhere

Strongly prefer:
- Primary sources (papers, official announcements) over commentary
- Technical depth over surface-level news
- Contrarian or non-obvious takes over consensus views
- Hacker News posts with high engagement (implies community finds it valuable)

For arxiv papers: score higher if the paper has practical implications (new efficient architecture, compression technique, hardware-aware optimization), lower if it's purely theoretical with no near-term application.

Return ONLY a JSON array: [{{"id": "...", "score": 8.5}}, ...]

Items:
{json.dumps(payload, ensure_ascii=False)}
"""
    text = _strip_fence(_chat(prompt, chain=_SCORE_CHAIN))
    try:
        results = json.loads(text)
    except json.JSONDecodeError:
        print(f"[score] failed to parse: {text[:200]}")
        return []
    return [(r["id"], float(r["score"])) for r in results if "id" in r and "score" in r]


def score_all_unscored(batch_size=20):
    items = db.unscored_items(limit=200)
    print(f"[score] {len(items)} items to score")
    for i in range(0, len(items), batch_size):
        if i > 0:
            time.sleep(4)
        batch = items[i:i+batch_size]
        for iid, score in score_batch(batch):
            db.update_score(iid, score)
        print(f"[score] batch {i//batch_size + 1} done")


def summarize(item):
    prompt = f"""You are writing a bilingual (Chinese + English) brief for a student studying IC/FPGA design who is learning about AI and space.

Article:
Title: {item['title']}
Source: {item['source']}
Category: {item.get('category', '')}
Snippet: {(item.get('summary') or '')[:1500]}
URL: {item['url']}

Produce the following in JSON:
1. "title_zh" — Chinese translation of the title (concise, max 40 chars, MUST be in Chinese)
2. "summary" — 2-3 sentences in ENGLISH ONLY: what happened, why it's significant. Be specific with names, numbers, technical details. Do NOT hallucinate. Do NOT write Chinese here.
3. "summary_zh" — same meaning as "summary" but written in CHINESE ONLY. Do NOT copy the English text.
4. "why_care" — 1 sentence in ENGLISH ONLY: what can I DO with this info? Practical and specific.
5. "why_care_zh" — same meaning as "why_care" but written in CHINESE ONLY.

If the article is about a paper, mention the key technical contribution.
If it's about industry news, mention the strategic implication.
If it's about space, mention the engineering detail that matters.

Return ONLY JSON: {{"title_zh": "...", "summary": "...", "summary_zh": "...", "why_care": "...", "why_care_zh": "..."}}
"""
    text = _strip_fence(_chat(prompt, max_tokens=500, chain=_SUMMARIZE_CHAIN))
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        print(f"[summarize] failed: {text[:200]}")
        return {}


def prepare_top_items(n=10):
    candidates = db.top_candidates(limit=n * 3)
    print(f"[digest] {len(candidates)} candidates above threshold")
    enriched = []
    for i, it in enumerate(candidates[:n + 3]):
        if i > 0:
            time.sleep(4)  # Stay under Gemini's 20 RPM free-tier limit
        data = summarize(it)
        if not data:
            continue
        db.attach_summary(
            it["id"], data.get("title_zh", ""), data.get("summary", ""),
            data.get("summary_zh", ""), data.get("why_care", ""), data.get("why_care_zh", ""),
        )
        it.update(data)
        enriched.append(it)
    return enriched[:n]
