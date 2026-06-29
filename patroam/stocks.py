"""Vietnamese stock prices via SSI FastConnect Data (official API).

Uses the FastConnect Data REST API directly (stdlib urllib — no heavy SDK), the
same lightweight style as gold.py/news.py. You authenticate once with your
FastConnect ConsumerID/Secret to get a bearer token (cached), then read daily
stock prices and index values.

Credentials (register at https://iboard.ssi.com.vn → FastConnect Data) go in
~/.patroam/secrets.json:
  "SSI_CONSUMER_ID": "...",
  "SSI_CONSUMER_SECRET": "..."
"""

import datetime
import json
import time
import urllib.request

from . import config

_token = {"value": None, "exp": 0.0}


def available():
    return bool(config.SSI_CONSUMER_ID and config.SSI_CONSUMER_SECRET)


def _base():
    return config.SSI_DATA_URL.rstrip("/")


def _num(x):
    try:
        return float(str(x).replace(",", "").strip() or 0)
    except (ValueError, TypeError):
        return 0.0


def _scale(raw):
    """SSI may return equity prices in thousands of VND (e.g. 60.5 = 60,500 đ).
    VN-listed shares trade well above 1,000 đ, so a value under 1,000 means it's
    quoted in thousands — scale it up. Returns the multiplier (1 or 1000)."""
    return 1000 if 0 < raw < 1000 else 1


# ── auth ──────────────────────────────────────────────────────────────────────────
def _get_token():
    if _token["value"] and _token["exp"] > time.time():
        return _token["value"]
    body = json.dumps({"consumerID": config.SSI_CONSUMER_ID,
                       "consumerSecret": config.SSI_CONSUMER_SECRET}).encode()
    req = urllib.request.Request(
        _base() + "/api/v2/Market/AccessToken", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.loads(r.read())
    tok = (d.get("data") or {}).get("accessToken")
    if tok:
        _token["value"] = tok
        _token["exp"] = time.time() + 7 * 3600   # tokens last ~8h; refresh early
    return tok


def _api(path, params):
    tok = _get_token()
    if not tok:
        return None
    import urllib.parse
    url = _base() + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + tok})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


# ── helpers ─────────────────────────────────────────────────────────────────────
def _dates(days_back=12):
    today = datetime.date.today()
    fmt = "%d/%m/%Y"
    return (today - datetime.timedelta(days=days_back)).strftime(fmt), today.strftime(fmt)


def _parse_date(s):
    for f in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(str(s), f)
        except (ValueError, TypeError):
            continue
    return datetime.datetime.min


def _latest(rows, key="TradingDate"):
    return max(rows, key=lambda r: _parse_date(r.get(key, "")))


# ── quotes ─────────────────────────────────────────────────────────────────────
def quote(symbol):
    """Latest daily price for a ticker (e.g. VNM). Returns {say, show}."""
    if not available():
        return None
    sym = (symbol or "").upper().strip()
    frm, to = _dates()
    try:
        d = _api("/api/v2/Market/DailyStockPrice",
                 {"Symbol": sym, "FromDate": frm, "ToDate": to,
                  "PageIndex": 1, "PageSize": 100})
    except Exception as e:
        return {"say": f"I couldn't reach SSI for {sym}, Sir: {e}", "show": str(e)}
    rows = (d or {}).get("data") or []
    if not rows:
        return {"say": f"I found no SSI price for {sym}, Sir.",
                "show": f"No SSI data for {sym}. Check the ticker."}
    row = _latest(rows)
    raw_close = _num(row.get("ClosePrice")) or _num(row.get("RefPrice"))
    sc = _scale(raw_close)
    close = raw_close * sc
    chg = _num(row.get("PriceChange")) * sc
    pct = _num(row.get("PerPriceChange"))
    vol = _num(row.get("TotalMatchVol"))
    o = _num(row.get("OpenPrice")) * sc
    hi = _num(row.get("HighestPrice")) * sc
    lo = _num(row.get("LowestPrice")) * sc
    date = row.get("TradingDate", "")
    arrow = "▲" if chg > 0 else ("▼" if chg < 0 else "■")
    word = "up" if chg > 0 else ("down" if chg < 0 else "flat")
    say = (f"{sym} is at {close:,.0f} đồng, {word} {abs(pct):.2f} percent."
           if close else f"{sym}: no price available, Sir.")
    show = (f"📈 {sym} — {date}\n"
            f"Price: {close:,.0f} đ   {arrow} {pct:+.2f}% ({chg:+,.0f} đ)\n"
            f"Open {o:,.0f} · High {hi:,.0f} · Low {lo:,.0f}\n"
            f"Volume: {vol:,.0f}")
    return {"say": say, "show": show}


def index(code="VNINDEX"):
    """Latest value for an index (VNINDEX / HNXINDEX / VN30 …). Returns {say, show}."""
    if not available():
        return None
    code = (code or "VNINDEX").upper().strip()
    frm, to = _dates()
    try:
        d = _api("/api/v2/Market/DailyIndex",
                 {"IndexId": code, "FromDate": frm, "ToDate": to,
                  "PageIndex": 1, "PageSize": 100, "OrderBy": "TradingDate",
                  "Order": "desc"})
    except Exception as e:
        return {"say": f"I couldn't reach SSI for {code}, Sir: {e}", "show": str(e)}
    rows = (d or {}).get("data") or []
    if not rows:
        return {"say": f"I found no SSI data for {code}, Sir.",
                "show": f"No SSI index data for {code}."}
    row = _latest(rows)
    val = _num(row.get("IndexValue"))
    chg = _num(row.get("Change"))
    pct = _num(row.get("RatioChange"))
    vol = _num(row.get("TotalMatchVol"))
    date = row.get("TradingDate", "")
    label = "VN-Index" if code == "VNINDEX" else code
    word = "up" if chg > 0 else ("down" if chg < 0 else "flat")
    arrow = "▲" if chg > 0 else ("▼" if chg < 0 else "■")
    say = f"{label} is at {val:,.2f}, {word} {abs(pct):.2f} percent."
    show = (f"📊 {label} — {date}\n"
            f"{val:,.2f}   {arrow} {pct:+.2f}% ({chg:+,.2f})\n"
            f"Volume: {vol:,.0f}")
    return {"say": say, "show": show}
