"""Latest news via NewsAPI — deterministic, no model tool-calling.

Powers the "what's up" command: fetches top headlines and reads them out. Needs a
free NewsAPI key (https://newsapi.org) in config.NEWSAPI_KEY.
"""

import json
import urllib.error
import urllib.parse
import urllib.request

from . import config

_CATEGORIES = {"business", "entertainment", "general", "health",
               "science", "sports", "technology"}


def available():
    return bool(config.NEWSAPI_KEY)


def _category(text):
    t = (text or "").lower()
    for c in _CATEGORIES:
        if c in t:
            return c
    if "tech" in t:
        return "technology"
    if "sport" in t:
        return "sports"
    return None


def headlines(text="", n=5):
    if not available():
        return "I'd need a news key first, Sir — set NEWSAPI_KEY (free at newsapi.org)."
    params = {"apiKey": config.NEWSAPI_KEY, "pageSize": str(n),
              "country": config.NEWS_COUNTRY}
    cat = _category(text)
    if cat:
        params["category"] = cat
    url = "https://newsapi.org/v2/top-headlines?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("message", str(e))
        except Exception:
            msg = str(e)
        return f"I couldn't reach the news: {msg}"
    except Exception as e:
        return f"I couldn't reach the news: {e}"

    articles = data.get("articles", [])
    # Strip the trailing " - Source" most NewsAPI titles carry.
    titles = []
    for a in articles[:n]:
        title = (a.get("title") or "").split(" - ")[0].strip()
        if title:
            titles.append(title)
    if not titles:
        return "I couldn't find any headlines right now."
    lead = f"Here are the latest {cat} headlines: " if cat else "Here's what's happening: "
    return lead + " ".join(f"{i + 1}. {t}." for i, t in enumerate(titles))
