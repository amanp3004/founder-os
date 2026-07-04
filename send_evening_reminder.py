"""
send_evening_reminder.py — sends a second, differently-angled push
notification later in the day, teasing a story from today's edition that
readers may have missed that morning. Run by a separate scheduled workflow.

Requires GEMINI_API_KEY and FIREBASE_SERVICE_ACCOUNT_JSON.
"""

import os
import re
import json
import requests

from push_utils import send_push

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
MODEL = os.environ.get("FOUNDEROS_MODEL", "gemini-2.5-flash")
SITE_URL = os.environ.get("SITE_URL", "https://amanp3004.github.io/catalyst/")

if not GEMINI_API_KEY:
    raise SystemExit("GEMINI_API_KEY environment variable is not set.")


def load_todays_edition():
    with open("data/latest.json") as f:
        return json.load(f)


def write_teaser(edition):
    """Ask Gemini to write a short, scroll-stopping push notification —
    Zomato-style playful urgency, not a dry headline restatement."""

    context = json.dumps({
        "theme": edition.get("theme"),
        "brief": [{"title": b["title"], "summary": b["summary"]} for b in edition.get("brief", [])],
        "breakdown_company": edition.get("breakdown", {}).get("company"),
        "breakdown_lesson": edition.get("breakdown", {}).get("lesson"),
    }, indent=2)

    prompt = f"""You write push notifications for Catalyst, a daily startup
newsletter for founders. Your job right now: write ONE evening reminder
notification that nudges someone who hasn't opened today's edition yet to
open it.

Style: playful, punchy, a little cheeky — like Zomato/Swiggy notification
copy, but for founders/business news instead of food. Create curiosity,
don't give away the full story. Use at most one emoji. No corporate tone,
no "Don't miss out on our newsletter" cliches.

Today's edition content (for context, do not just restate it):
{context}

Respond with ONLY valid JSON, no markdown fences:
{{"title": "string, max 40 characters, hooky", "body": "string, max 90 characters, creates curiosity"}}
"""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={GEMINI_API_KEY}"
    response = requests.post(
        url,
        json={
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.9,
                "maxOutputTokens": 300,
                "responseMimeType": "application/json",
            },
        },
        timeout=30,
    )
    response.raise_for_status()
    text = response.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    text = re.sub(r"^```(json)?|```$", "", text, flags=re.MULTILINE).strip()
    return json.loads(text)


if __name__ == "__main__":
    edition = load_todays_edition()
    print("Writing evening teaser with Gemini...")
    teaser = write_teaser(edition)
    print(f"Teaser: {teaser}")
    send_push(title=teaser["title"], body=teaser["body"], click_url=SITE_URL)
    print("Done.")
