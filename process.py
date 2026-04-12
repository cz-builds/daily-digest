"""Score and summarize items using Gemini with model fallback chain."""
import json
import os
import re
import time

from google import genai

import db
from fetch import load_sources

_client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY", ""))

# Model priority: best first, fallback to cheaper models on rate limit
_MODEL_CHAIN = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.0-flash-lite"]
_RATE_LIMIT_MARKERS = ("429", "RESOURCE_EXHAUSTED", "quota", "rate_limit")
_exhausted_models: set[str] = set()


def _chat(prompt, max_tokens=2000, max_retries=2):
    for model in _MODEL_CHAIN:
        if model in _exhausted_models:
            continue
        for attempt in range(max_retries):
            try:
                resp = _client.models.generate_content(model=model, contents=prompt)
                return resp.text or ""
            except Exception as e:
                err = str(e).lower()
                if any(m in err for m in _RATE_LIMIT_MARKERS):
                    _exhausted_models.add(model)
                    remaining = [m for m in _MODEL_CHAIN if m not in _exhausted_models]
                    if remaining:
                        print(f"[llm] {model} rate-limited → falling back to {remaining[0]}")
                    else:
                        print(f"[llm] all models exhausted")
                    break
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
    text = _strip_fence(_chat(prompt))
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
    text = _strip_fence(_chat(prompt, max_tokens=500))
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
