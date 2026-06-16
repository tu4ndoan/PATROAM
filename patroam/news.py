"""Latest news from YOUR trusted RSS/Atom feeds (config.NEWS_FEEDS).

PATROAM fetches the feeds, ranks headlines by the topics you care about
(config.NEWS_INTERESTS) plus anything you mention in the request, then returns:
  - "say"  : the headline titles to read aloud (no links spoken), and
  - "show" : titles + clickable source links for the chat panel.

Falls back to NewsAPI top-headlines if you have no feeds configured.
"""

import html
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

from . import config

_WORD = re.compile(r"[a-z0-9]+")
_TAG = re.compile(r"<[^>]+>")


def available():
    return bool(config.NEWS_FEEDS) or bool(config.NEWSAPI_KEY)


# ── fetch & parse feeds (RSS + Atom, stdlib only) ─────────────────────────────────
def _fetch(url, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": "PATROAM/1.0 (+news)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _clean(s):
    return _TAG.sub("", html.unescape(s or "")).strip()


def _strip_ns(tag):
    return tag.rsplit("}", 1)[-1].lower()


def _parse(raw, source):
    """Return [{title, link, source}] from an RSS or Atom document."""
    items = []
    try:
        root = ET.fromstring(raw)
    except Exception:
        return items
    for el in root.iter():
        if _strip_ns(el.tag) not in ("item", "entry"):
            continue
        title, link = "", ""
        for ch in el:
            t = _strip_ns(ch.tag)
            if t == "title" and not title:
                title = _clean(ch.text or "".join(ch.itertext()))
            elif t == "link" and not link:
                # RSS: text holds the URL. Atom: <link href="..."/>.
                link = (ch.text or "").strip() or ch.get("href", "").strip()
        if title:
            items.append({"title": title, "link": link, "source": source})
    return items


def _source_name(url):
    m = re.search(r"https?://(?:www\.|feeds\.|rss\.)?([^/]+)", url or "")
    return m.group(1) if m else url


def _fetch_all(per_feed=8):
    items = []
    for url in config.NEWS_FEEDS:
        try:
            items.extend(_parse(_fetch(url), _source_name(url))[:per_feed])
        except Exception:
            continue
    return items


# ── ranking ────────────────────────────────────────────────────────────────────
def _rank(items, query):
    interests = [k.lower() for k in (config.NEWS_INTERESTS or [])]
    qwords = set(_WORD.findall((query or "").lower())) - {
        "what", "whats", "the", "news", "latest", "any", "about", "tell", "me",
        "is", "up", "new", "give", "show", "headlines", "today", "s"}
    seen, ranked = set(), []
    for it in items:
        tl = it["title"].lower()
        key = tl[:60]
        if key in seen:
            continue
        seen.add(key)
        score = sum(1 for k in interests if k in tl) * 2
        score += sum(1 for w in qwords if w in tl)
        it["_score"] = score
        ranked.append(it)
    # Interest/query matches first; otherwise keep feed (recency) order.
    ranked.sort(key=lambda it: it["_score"], reverse=True)
    return ranked


def latest(query="", n=None):
    n = n or config.NEWS_MAX
    items = _fetch_all()
    if not items:
        return _newsapi_fallback(query, n)
    top = _rank(items, query)[:n]
    if not top:
        return "I couldn't find any headlines right now."

    interests = config.NEWS_INTERESTS
    lead = ("Here's what's happening" + (" in the topics you follow" if interests else "")
            + f"{_addr()}. ")
    say = lead + " ".join(f"{i + 1}. {it['title']}." for i, it in enumerate(top))
    show = lead.strip() + "\n" + "\n".join(
        f"{i + 1}. {it['title']}"
        + (f"\n   {it['link']}" if it["link"] else "")
        + (f"   ({it['source']})" if it["source"] else "")
        for i, it in enumerate(top))
    return {"say": say, "show": show}


def _addr():
    import random
    return random.choice([", Sir", ", Master", ""])


# ── NewsAPI fallback (only if no feeds configured) ────────────────────────────────
def _newsapi_fallback(query, n):
    if not config.NEWSAPI_KEY:
        return ("Set up your trusted news feeds first, Sir — add them to "
                "config.NEWS_FEEDS or ~/.patroam/news.json.")
    import json
    import urllib.parse
    params = {"apiKey": config.NEWSAPI_KEY, "pageSize": str(n), "country": config.NEWS_COUNTRY}
    url = "https://newsapi.org/v2/top-headlines?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            data = json.loads(r.read())
    except Exception as e:
        return f"I couldn't reach the news: {e}"
    arts = data.get("articles", [])[:n]
    if not arts:
        return "I couldn't find any headlines right now."
    titles = [(a.get("title") or "").split(" - ")[0].strip() for a in arts]
    say = "Here's what's happening. " + " ".join(f"{i + 1}. {t}." for i, t in enumerate(titles) if t)
    show = "\n".join(f"{i + 1}. {t}" + (f"\n   {a.get('url', '')}" if a.get("url") else "")
                     for i, (t, a) in enumerate(zip(titles, arts)) if t)
    return {"say": say, "show": show}


# Back-compat alias.
def headlines(text="", n=5):
    return latest(text, n)
