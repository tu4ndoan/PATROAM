"""Gold spot price in USD and VND — for the "gold price" / "what's up" command.

Uses goldapi.io if config.GOLD_API_KEY is set (most accurate), else a free,
keyless source. USD→VND comes from a free FX endpoint. All network calls are
defensive — any failure yields a graceful message, never a crash.
"""

import json
import urllib.request

from . import config


def _get(url, headers=None, timeout=12):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "PATROAM/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _gold_usd_oz():
    """Gold price in USD per troy ounce, or None."""
    try:
        if config.GOLD_API_KEY:
            d = _get("https://www.goldapi.io/api/XAU/USD",
                     {"x-access-token": config.GOLD_API_KEY, "Content-Type": "application/json"})
            return d.get("price")
        d = _get("https://api.gold-api.com/price/XAU")   # free, keyless
        return d.get("price")
    except Exception:
        return None


def _usd_to_vnd():
    """USD→VND rate, or None (tries a couple of free endpoints)."""
    for url in ("https://open.er-api.com/v6/latest/USD",
                "https://api.exchangerate.host/latest?base=USD&symbols=VND"):
        try:
            rates = _get(url).get("rates") or {}
            if rates.get("VND"):
                return float(rates["VND"])
        except Exception:
            continue
    return None


def price(text=""):
    """Spoken gold-price summary in USD and (if available) VND."""
    usd = _gold_usd_oz()
    if not usd:
        return "I couldn't reach the gold price right now, Sir."
    out = f"Gold is ${usd:,.0f} per ounce"
    rate = _usd_to_vnd()
    if rate:
        out += f", about {usd * rate / 1e6:.1f} million Vietnamese dong per ounce"
    return out + "."
