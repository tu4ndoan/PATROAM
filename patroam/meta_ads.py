"""Direct Meta Ads stats — deterministic, no OAuth, no model tool-calling.

Calls Meta's Graph Marketing API straight from a `skills` command, so PATROAM can
read out your ad numbers reliably even on a small local model. Needs a Meta access
token with `ads_read` and your ad-account id (see config.META_*).
"""

import json
import urllib.error
import urllib.parse
import urllib.request

from . import config

BASE = "https://graph.facebook.com"
_FIELDS = "spend,impressions,clicks,ctr,cpc,reach"


def available():
    return bool(config.META_ACCESS_TOKEN and config.META_AD_ACCOUNT_ID)


def _get(path, params):
    params = dict(params)
    params["access_token"] = config.META_ACCESS_TOKEN
    url = f"{BASE}/{config.META_API_VERSION}/{path}?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            return json.loads(r.read()), None
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read())["error"]["message"]
        except Exception:
            msg = str(e)
        return None, msg
    except Exception as e:
        return None, str(e)


def _int(v):
    try:
        return int(float(v))
    except Exception:
        return 0


def _money(v, cur):
    try:
        return f"{float(v):,.0f} {cur}".strip()
    except Exception:
        return f"{v} {cur}".strip()


def _currency(acct):
    data, _ = _get(f"act_{acct}", {"fields": "currency"})
    return (data or {}).get("currency", "") if data else ""


def latest_ad_summary(acct, cur):
    data, err = _get(f"act_{acct}/ads", {"fields": "name,created_time", "limit": "50"})
    if err:
        return f"I couldn't reach Meta: {err}"
    ads = (data or {}).get("data", [])
    if not ads:
        return "I don't see any ads in that account."
    ads.sort(key=lambda a: a.get("created_time", ""), reverse=True)
    ad = ads[0]
    name = ad.get("name", "your latest ad")
    ins, err = _get(f"{ad['id']}/insights", {"fields": _FIELDS, "date_preset": "maximum"})
    if err:
        return f"I found '{name}', but couldn't get its stats: {err}"
    rows = (ins or {}).get("data", [])
    if not rows:
        return f"Your latest ad, '{name}', hasn't delivered any results yet."
    s = rows[0]
    out = (f"Your latest ad, '{name}': spent {_money(s.get('spend', 0), cur)}, "
           f"{_int(s.get('impressions')):,} impressions, {_int(s.get('clicks')):,} clicks, "
           f"a {float(s.get('ctr', 0) or 0):.2f}% click-through rate")
    out += f", and {_money(s.get('cpc'), cur)} per click." if s.get("cpc") else "."
    return out


def account_summary(acct, cur):
    ins, err = _get(f"act_{acct}/insights", {"fields": _FIELDS, "date_preset": "last_30d"})
    if err:
        return f"I couldn't reach Meta: {err}"
    rows = (ins or {}).get("data", [])
    if not rows:
        return "There's been no ad activity in the last 30 days."
    s = rows[0]
    return (f"In the last 30 days you've spent {_money(s.get('spend', 0), cur)} across your "
            f"ads, reaching {_int(s.get('reach')):,} people with {_int(s.get('impressions')):,} "
            f"impressions and {_int(s.get('clicks')):,} clicks — a "
            f"{float(s.get('ctr', 0) or 0):.2f}% click-through rate.")


def summary(text):
    """Spoken ad-stats reply for a request like 'how are my ads doing'."""
    if not available():
        return ("I'd need access to your Meta ads first, Sir — set a Meta access token "
                "(META_ACCESS_TOKEN) and your ad account id (META_AD_ACCOUNT_ID).")
    acct = config.META_AD_ACCOUNT_ID
    cur = _currency(acct)
    t = (text or "").lower()
    if "latest" in t or "last ad" in t or "this ad" in t or "the ad" in t:
        return latest_ad_summary(acct, cur)
    return account_summary(acct, cur)
