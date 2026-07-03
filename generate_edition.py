"""
Catalyst — Daily Edition Generator
Pulls startup news, curates it with Gemini (free tier) according to the
Atlas Editorial Manifesto, and writes the result as JSON for the website
to render.

Run manually:
    GEMINI_API_KEY=AIza... PEXELS_API_KEY=... python generate_edition.py

Get a free Gemini key (no credit card) at https://aistudio.google.com/apikey
Get a free Pexels key (no credit card) at https://www.pexels.com/api/

In production this is run automatically every morning by the GitHub Actions
workflow in .github/workflows/daily-edition.yml
"""

import os
import re
import json
import time
import datetime
from zoneinfo import ZoneInfo
import feedparser
import requests

IST = ZoneInfo("Asia/Kolkata")


def today_ist():
    """Today's date in IST, as 'YYYY-MM-DD'.

    GitHub Actions runners default to UTC, so 'today' by server clock can
    roll over up to 5.5 hours before it actually does in India. Anchoring
    explicitly to IST is what makes the 'one edition per day, content
    locked until 12am IST' behavior correct regardless of when/how often
    the workflow runs.
    """
    return datetime.datetime.now(IST).date().isoformat()

# ---------------------------------------------------------------------------
# 1. CONFIG
# ---------------------------------------------------------------------------

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
MODEL = os.environ.get("FOUNDEROS_MODEL", "gemini-2.5-flash")
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY")

if not GEMINI_API_KEY:
    raise SystemExit(
        "GEMINI_API_KEY environment variable is not set.\n"
        "Get a free key (no credit card needed) at https://aistudio.google.com/apikey"
    )

if not PEXELS_API_KEY:
    raise SystemExit(
        "PEXELS_API_KEY environment variable is not set.\n"
        "Get a free key (no credit card needed) at https://www.pexels.com/api/"
    )

RSS_SOURCES = {
    "TechCrunch": "https://techcrunch.com/feed/",
    "YourStory": "https://yourstory.com/feed",
    "Inc42": "https://inc42.com/feed/",
    "Entrackr": "https://entrackr.com/feed",
}

HN_API = "https://hn.algolia.com/api/v1/search?tags=story&hitsPerPage=15"

MANIFESTO = """
You are Atlas, the AI Editor-in-Chief of Catalyst, a daily newsletter for
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
2. Identify today's single dominant theme that connects several stories.
   Make it punchy and memorable — like a magazine cover line, not a
   textbook chapter title. Favor short, confident, slightly provocative
   phrasing over descriptive phrasing.
   Good: "Vertical AI Is Replacing Horizontal Software", "Distribution
   Beats Technology", "The Great Unbundling of Venture Capital"
   Weak/too plain: "AI Trends in Startups", "Changes in the Funding Market"
3. Curate exactly five sections:
   - startup_brief: 3 stories. Each needs a catchy, magazine-style title —
     not a dry restatement of the headline. Titles should hook attention
     while staying factually accurate (no clickbait exaggeration, no
     fabricated claims). Think Economist/Fast Company headline energy, not
     press-release energy.
     Example — dry: "Company X raises $10M in funding round"
     Example — catchy: "Company X just proved bigger isn't better"
     Each item also needs a 1-2 sentence summary explaining why it matters
     (not just what happened), a source url, and the company's website
     domain (for logo lookup, e.g. "openai.com").
   - startup_breakdown: ONE company that best represents today's theme.
     Include: company name, domain, what it does, why it matters, and one
     memorable one-sentence lesson for founders.
   - trend_to_watch: 2-3 short paragraphs explaining the broader shift, no
     jargon, focused on strategic implications for founders.
   - builder_lexicon: exactly ONE entrepreneurial/startup concept that best
     complements today's theme, chosen AFTER the theme is set (e.g. a
     fundraising-themed edition → "SAFE Note", a growth-themed edition →
     "North Star Metric"). Draw from standard startup/VC/product vocabulary
     (MVP, Pivot, Product-Market Fit, CAC, LTV, Burn Rate, Runway, Churn,
     ARR, MRR, TAM, SAM, SOM, GTM, Flywheel, Network Effects, North Star
     Metric, Moat, Freemium, Blitzscaling, ESOP, SAFE Note, Convertible
     Note, Cap Table, Seed Round, Series A, Unicorn, Decacorn, or an
     equally standard term) — never invent a term. Explain it in a way
     that sharpens business thinking, not just defines it, but keep it
     tight. Total reading time ~15-20 seconds: definition is EXACTLY 1-2
     sentences (roughly 25-40 words, one short paragraph — never multiple
     paragraphs, this is a quick-hit definition, not an essay), why it
     matters (1-2 sentences), one real-world example sentence naming a
     known company.
   - editors_note: 2-3 short paragraphs, one thoughtful reflection that ties
     the whole edition into one coherent story with one memorable idea.

STYLE: Clear, thoughtful, analytical, conversational, concise, confident
without exaggeration. No buzzwords, no unnecessary adjectives, no
motivational cliches. Short paragraphs. Write as if speaking to intelligent
MBA students. Never fabricate facts — only use what's in the provided
stories. For company domains, use the real, official website domain you
are confident about (e.g. "openai.com", not "open-ai.com" or a guess) —
if genuinely unsure of the exact domain, use your best confident guess at
the root domain rather than a subpage or made-up variant.

For every image_query and theme_image_query, write a plain, literal,
photographable scene (e.g. "team meeting office", "server room data
center", "city skyline finance") — these are used as stock-photo search
terms, not headlines, so keep them concrete and generic rather than
abstract or metaphorical.

OUTPUT FORMAT: Respond with ONLY valid JSON, no markdown fences, no preamble,
matching exactly this schema:

{
  "date": "YYYY-MM-DD",
  "theme": "string",
  "theme_image_query": "2-4 word literal, photographable stock-photo search phrase for the theme (e.g. 'server room data center', 'city skyline finance')",
  "brief": [
    {"title": "string", "summary": "string", "url": "string", "domain": "string", "image_query": "2-4 word literal, photographable stock-photo search phrase (e.g. 'startup office team', 'robot factory automation')"},
    {"title": "string", "summary": "string", "url": "string", "domain": "string", "image_query": "string"},
    {"title": "string", "summary": "string", "url": "string", "domain": "string", "image_query": "string"}
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
  "builder_lexicon": {
    "term": "string",
    "definition": "string (1-2 sentences only, ~25-40 words)",
    "why_it_matters": "string (1-2 sentences)",
    "real_world_example": "string (1 sentence)",
    "reading_time": "string (e.g. '20 sec read')"
  },
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

def curate_edition(stories, today):
    raw_dump = "\n".join(
        f"- [{s['source']}] {s['title']} — {s['summary']} ({s['url']})"
        for s in stories if s["title"]
    )

    user_prompt = f"""Today's date: {today}

RAW STORIES COLLECTED TODAY:
{raw_dump}

Curate today's Catalyst edition following the manifesto exactly. Output
only the JSON object."""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={GEMINI_API_KEY}"

    response = requests.post(
        url,
        json={
            "system_instruction": {"parts": [{"text": MANIFESTO}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "temperature": 0.7,
                "maxOutputTokens": 8192,
                "responseMimeType": "application/json",
            },
        },
        timeout=60,
    )
    response.raise_for_status()
    data = response.json()

    text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
    text = re.sub(r"^```(json)?|```$", "", text, flags=re.MULTILINE).strip()

    try:
        edition = json.loads(text)
    except json.JSONDecodeError as e:
        print("---- RAW MODEL OUTPUT (failed to parse as JSON) ----")
        print(text)
        print("---- END RAW OUTPUT ----")
        raise SystemExit(f"Gemini did not return valid JSON: {e}")

    edition["date"] = today
    return edition


# ---------------------------------------------------------------------------
# 3b. FETCH IMAGES (Pexels — free tier, requires API key)
# ---------------------------------------------------------------------------

def search_pexels(query):
    """Return a photo URL from Pexels for a search query, or None.

    Pexels' free tier allows 200 requests/hour, so a small pacing delay in
    enrich_with_images is plenty; no aggressive backoff needed like with
    Openverse. If a query comes back empty we retry once with a broader
    (shorter) query before giving up.
    """
    if not query:
        return None

    def _try(q):
        resp = requests.get(
            "https://api.pexels.com/v1/search",
            params={"query": q, "per_page": 3, "orientation": "landscape"},
            headers={"Authorization": PEXELS_API_KEY},
            timeout=15,
        )
        if resp.status_code == 429:
            # rate limited — back off once and retry the same request
            time.sleep(5)
            resp = requests.get(
                resp.url,
                headers={"Authorization": PEXELS_API_KEY},
                timeout=15,
            )
        resp.raise_for_status()
        photos = resp.json().get("photos", [])
        if photos:
            src = photos[0].get("src", {})
            # "large" is a good balance of quality vs. payload size for a
            # card image; fall back to whatever sizes are actually present.
            return src.get("large") or src.get("original") or src.get("medium")
        return None

    try:
        url = _try(query)
        if url:
            return url
        # broaden: drop to the first 2 words and retry once
        broad = " ".join(query.split()[:2])
        if broad and broad != query:
            time.sleep(0.5)
            return _try(broad)
    except Exception as e:
        print(f"[warn] Pexels search failed for '{query}': {e}")
    return None


def enrich_with_images(edition):
    edition["theme_image"] = search_pexels(edition.get("theme_image_query"))
    for item in edition.get("brief", []):
        time.sleep(0.3)  # gentle pacing; well within Pexels' 200 req/hour limit
        item["image"] = search_pexels(item.get("image_query"))
    return edition


# ---------------------------------------------------------------------------
# 4. WRITE OUTPUT
# ---------------------------------------------------------------------------

def save_edition(edition):
    os.makedirs("data", exist_ok=True)
    date_path = f"data/{edition['date']}.json"

    with open(date_path, "w") as f:
        json.dump(edition, f, indent=2)

    with open("data/latest.json", "w") as f:
        json.dump(edition, f, indent=2)

    # maintain an index of all editions for an archive page
    index_path = "data/index.json"
    archive = []
    if os.path.exists(index_path):
        with open(index_path) as f:
            archive = json.load(f)
    if edition["date"] not in archive:
        archive.append(edition["date"])
    archive = sorted(set(archive))
    with open(index_path, "w") as f:
        json.dump(archive, f, indent=2)

    print(f"Saved edition for {edition['date']} -> {date_path}")


if __name__ == "__main__":
    today = today_ist()
    existing_path = f"data/{today}.json"

    if os.path.exists(existing_path):
        print(
            f"An edition for {today} (IST) already exists at {existing_path}.\n"
            "Content is locked for the day once generated — skipping regeneration.\n"
            "A fresh edition will be generated the next time this runs after "
            "12:00 AM IST."
        )
        raise SystemExit(0)

    print("Collecting stories...")
    stories = collect_stories()
    print(f"Collected {len(stories)} raw stories. Curating with Gemini...")
    edition = curate_edition(stories, today)
    print("Fetching relevant images from Pexels...")
    edition = enrich_with_images(edition)
    save_edition(edition)
    print("Done.")
