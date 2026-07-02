"""
Catalyst — Daily Edition Generator
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
MODEL = os.environ.get("FOUNDEROS_MODEL", "gemini-3-flash")
# Images via Openverse (openverse.org) — free, no signup, no API key needed.

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
3. Curate exactly four sections:
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

OUTPUT FORMAT: Respond with ONLY valid JSON, no markdown fences, no preamble,
matching exactly this schema:

{
  "date": "YYYY-MM-DD",
  "theme": "string",
  "theme_image_query": "2-4 word visual search phrase for a stock photo that captures the theme (e.g. 'server room data center', 'city skyline finance')",
  "brief": [
    {"title": "string", "summary": "string", "url": "string", "domain": "string", "image_query": "2-4 word visual search phrase for a relevant stock photo (e.g. 'startup office team', 'robot factory automation')"},
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
# 3b. FETCH IMAGES (Openverse — free, no API key required)
# ---------------------------------------------------------------------------

def search_openverse(query):
    """Return an openly-licensed photo URL for a search query, or None."""
    if not query:
        return None
    try:
        resp = requests.get(
            "https://api.openverse.org/v1/images/",
            params={
                "q": query,
                "page_size": 3,
                "mature": "false",
                "license_type": "commercial,modification",  # broad, reusable
            },
            headers={"User-Agent": "CatalystNewsletterBot/1.0"},
            timeout=15,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        for item in results:
            url = item.get("url")
            if url and url.lower().split("?")[0].endswith((".jpg", ".jpeg", ".png", ".webp")):
                return url
        if results:
            return results[0].get("url")
    except Exception as e:
        print(f"[warn] Openverse search failed for '{query}': {e}")
    return None


def enrich_with_images(edition):
    edition["theme_image"] = search_openverse(edition.get("theme_image_query"))
    for item in edition.get("brief", []):
        item["image"] = search_openverse(item.get("image_query"))
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
    print("Collecting stories...")
    stories = collect_stories()
    print(f"Collected {len(stories)} raw stories. Curating with Gemini...")
    edition = curate_edition(stories)
    print("Fetching relevant images...")
    edition = enrich_with_images(edition)
    save_edition(edition)
    print("Done.")
