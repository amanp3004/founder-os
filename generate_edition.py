"""
Founder OS — Daily Edition Generator
Pulls startup news, curates it with Gemini (free tier) according to the
Atlas Editorial Manifesto, and writes the result as JSON for the website
to render.

Run manually:
    GEMINI_API_KEY=AIza... python generate_edition.py

Get a free key (no credit card) at https://aistudio.google.com/apikey

In production this is run automatically every morning by the GitHub Actions
workflow in .github/workflows/daily-edition.yml
"""

import os
import re
import json
import datetime
import feedparser
import requests

# ---------------------------------------------------------------------------
# 1. CONFIG
# ---------------------------------------------------------------------------

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
MODEL = os.environ.get("FOUNDEROS_MODEL", "gemini-2.5-flash")

if not GEMINI_API_KEY:
    raise SystemExit(
        "GEMINI_API_KEY environment variable is not set.\n"
        "Get a free key (no credit card needed) at https://aistudio.google.com/apikey"
    )

RSS_SOURCES = {
    "TechCrunch": "https://techcrunch.com/feed/",
    "YourStory": "https://yourstory.com/feed",
    "Inc42": "https://inc42.com/feed/",
    "Entrackr": "https://entrackr.com/feed",
}

HN_API = "https://hn.algolia.com/api/v1/search?tags=story&hitsPerPage=15"

MANIFESTO = """
You are Atlas, the AI Editor-in-Chief of Founder OS, a daily newsletter for
aspiring founders (audience: MBA students, e.g. Entrepreneurship Club, IIM
Udaipur).

MISSION: Help readers understand what matters, not everything that happened.
Every edition answers: "If an aspiring founder had only five minutes today,
what should they learn?"

EDITORIAL PRINCIPLES:
- Teach before reporting. Never publish news without explaining why it matters.
- Curate, don't aggregate. Choose only stories that genuinely deserve attention.
- Connect the dots. Identify the larger trend connecting today's events.
- Think like a founder. Help readers make better business decisions.
- Respect the reader's time. Readable in five minutes. Every sentence earns its place.
- Quality over quantity. Four exceptional sections beat twenty average ones.
- Build long-term thinking. Prioritize timeless principles over hype.

WORKFLOW:
1. From the raw stories provided, remove duplicates, ignore clickbait, ignore
   stories without educational value.
2. Identify today's single dominant theme that connects several stories
   (e.g. "Distribution Beats Technology", "Vertical AI Is Replacing
   Horizontal Software", "Capital Is Becoming More Selective").
3. Curate exactly four sections:
   - startup_brief: 3 stories. Each needs a title, a 1-2 sentence summary
     explaining why it matters (not just what happened), a source url, and
     the company's website domain (for logo lookup, e.g. "openai.com").
   - startup_breakdown: ONE company that best represents today's theme.
     Include: company name, domain, what it does, why it matters, and one
     memorable one-sentence lesson for founders.
   - trend_to_watch: 2-3 short paragraphs explaining the broader shift, no
     jargon, focused on strategic implications for founders.
   - editors_note: 2-3 short paragraphs, one thoughtful reflection that ties
     the whole edition into one coherent story with one memorable idea.

STYLE: Clear, thoughtful, analytical, conversational, concise, confident
without exaggeration. No buzzwords, no unnecessary adjectives, no
motivational cliches. Short paragraphs. Write as if speaking to intelligent
MBA students. Never fabricate facts — only use what's in the provided
stories.

OUTPUT FORMAT: Respond with ONLY valid JSON, no markdown fences, no preamble,
matching exactly this schema:

{
  "date": "YYYY-MM-DD",
  "theme": "string",
  "brief": [
    {"title": "string", "summary": "string", "url": "string", "domain": "string"},
    {"title": "string", "summary": "string", "url": "string", "domain": "string"},
    {"title": "string", "summary": "string", "url": "string", "domain": "string"}
  ],
  "breakdown": {
    "company": "string",
    "domain": "string",
    "category": "string (e.g. 'Agentic AI · Enterprise Support · Bengaluru')",
    "what": "string",
    "why": "string",
    "lesson": "string"
  },
  "trend": {"paragraphs": ["string", "string"]},
  "editors_note": {"paragraphs": ["string", "string", "string"]}
}
"""

# ---------------------------------------------------------------------------
# 2. COLLECT NEWS
# ---------------------------------------------------------------------------

def collect_stories(limit_per_source=8):
    """Pull recent items from RSS feeds + Hacker News. Returns a flat list."""
    stories = []

    for name, url in RSS_SOURCES.items():
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:limit_per_source]:
                stories.append({
                    "source": name,
                    "title": entry.get("title", ""),
                    "summary": re.sub("<[^<]+?>", "", entry.get("summary", ""))[:400],
                    "url": entry.get("link", ""),
                })
        except Exception as e:
            print(f"[warn] failed to fetch {name}: {e}")

    try:
        hn = requests.get(HN_API, timeout=10).json()
        for hit in hn.get("hits", []):
            if hit.get("title"):
                stories.append({
                    "source": "Hacker News",
                    "title": hit["title"],
                    "summary": "",
                    "url": hit.get("url") or f"https://news.ycombinator.com/item?id={hit['objectID']}",
                })
    except Exception as e:
        print(f"[warn] failed to fetch Hacker News: {e}")

    return stories


# ---------------------------------------------------------------------------
# 3. CURATE WITH GEMINI (free tier)
# ---------------------------------------------------------------------------

def curate_edition(stories):
    today = datetime.date.today().isoformat()

    raw_dump = "\n".join(
        f"- [{s['source']}] {s['title']} — {s['summary']} ({s['url']})"
        for s in stories if s["title"]
    )

    user_prompt = f"""Today's date: {today}

RAW STORIES COLLECTED TODAY:
{raw_dump}

Curate today's Founder OS edition following the manifesto exactly. Output
only the JSON object."""

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{MODEL}:generateContent?key={GEMINI_AP
