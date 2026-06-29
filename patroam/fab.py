"""Read your Fab sales table and summarize it.

Two paths:
  * report_live() — drives a real browser (Playwright) to the Fab sales page and
    scrapes the rendered table. Uses a persistent profile in ~/.patroam/fab_profile,
    so you log into Fab there ONCE; afterwards it just reads the live table. This
    is what gets past Fab's login + Cloudflare to the actual <tr> rows.
  * report() — fallback: reads the most recent Fab CSV in your Downloads folder.
"""

import csv
import glob
import os
import re

from . import config

_PROFILE = os.path.join(os.path.expanduser("~"), ".patroam", "fab_profile")


def _num(s):
    s = re.sub(r"[^\d.\-]", "", str(s or ""))
    try:
        return float(s) if s else 0.0
    except ValueError:
        return 0.0


def _summarize(data, label):
    """data = [(day, title, units, sales)]. Returns {say, show}."""
    data = [d for d in data if d[1] and d[1].lower() != "total"]
    if not data:
        return {"say": "There are no Fab sales rows to report, Sir.", "show": "No rows."}
    total_units = sum(d[2] for d in data)
    total_sales = sum(d[3] for d in data)
    agg = {}
    for _d, t, u, s in data:
        a = agg.setdefault(t, [0.0, 0.0])
        a[0] += u
        a[1] += s
    top = max(agg.items(), key=lambda kv: kv[1][1])[0]
    say = (f"Your Fab sales: {int(total_units)} units for ${total_sales:,.2f} net "
           f"across {len(data)} sales. Top seller: {top}.")
    lines = [f"📊 Fab sales — {label}", ""]
    for d, t, u, s in data:
        lines.append(f"• {d}  {t} — {int(u)}× = ${s:,.2f}")
    lines.append("\nBy product:")
    for t, (u, s) in sorted(agg.items(), key=lambda kv: -kv[1][1]):
        lines.append(f"  {t}: {int(u)} units, ${s:,.2f}")
    lines.append(f"\nTotal: {int(total_units)} units, ${total_sales:,.2f}")
    return {"say": say, "show": "\n".join(lines)}


# ── live browser scrape ──────────────────────────────────────────────────────────
def _rows_to_data(headers, rows):
    h = [x.lower() for x in (headers or [])]

    def idx(*names):
        for n in names:
            for i, x in enumerate(h):
                if n in x:
                    return i
        return None

    i_day, i_title = idx("day", "date"), idx("listing", "title", "product")
    i_units, i_sales = idx("net unit", "unit", "qty", "quantity"), idx("net sale", "sales", "amount", "revenue")
    out = []
    for r in rows:
        if not r:
            continue
        get = lambda i: r[i].strip() if (i is not None and i < len(r)) else ""
        out.append((get(i_day), get(i_title), _num(get(i_units)), _num(get(i_sales))))
    return out


def scrape_live(timeout_ms=120000):
    """Open the Fab sales page in a real browser and scrape the table. Returns
    {headers, rows} on success, {"login": True} if it needs a login, or None if
    Playwright/the browser is unavailable."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    os.makedirs(_PROFILE, exist_ok=True)

    def go(install_retry=True):
        with sync_playwright() as p:
            try:
                ctx = p.chromium.launch_persistent_context(_PROFILE, headless=False)
            except Exception as e:
                if install_retry and ("install" in str(e).lower() or "executable" in str(e).lower()):
                    import subprocess
                    import sys
                    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"])
                    return go(False)
                raise
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                page.goto(config.FAB_SALES_URL, wait_until="domcontentloaded", timeout=60000)
                try:
                    page.wait_for_selector("tr.fabkit-Table-row", timeout=timeout_ms)
                except Exception:
                    return {"login": True}      # table never appeared → log in
                rows = page.eval_on_selector_all(
                    "tbody.fabkit-Table-body tr.fabkit-Table-row",
                    "rs => rs.map(r => { let c = Array.from(r.querySelectorAll('td,th'));"
                    " if(!c.length) c = Array.from(r.children);"
                    " return c.map(x => x.innerText.trim()); })")
                headers = page.evaluate(
                    "() => { const h = document.querySelector('thead'); if(!h) return [];"
                    " let c = Array.from(h.querySelectorAll('th,td'));"
                    " if(!c.length){ const row = h.querySelector('tr') || h; c = Array.from(row.children); }"
                    " return c.map(x => x.innerText.trim()); }")
                return {"headers": headers, "rows": rows}
            finally:
                ctx.close()

    try:
        return go()
    except Exception as e:
        return {"error": str(e)}


def report_live():
    """Scrape the live Fab table and summarize it. None if unavailable (→ fall
    back to the CSV reader)."""
    res = scrape_live()
    if not res or res.get("error"):
        return None
    if res.get("login"):
        return {"say": "I opened Fab in a window, Sir — please log in, then ask me "
                       "about your Fab sales again and I'll read the live numbers.",
                "show": "Log into Fab in the window I opened, then retry."}
    return _summarize(_rows_to_data(res.get("headers", []), res.get("rows", [])), "live page")


# ── CSV: download a fresh report via Brave, then read it ──────────────────────────
def _downloads():
    return os.path.join(os.path.expanduser("~"), "Downloads")


def _all_csvs():
    return glob.glob(os.path.join(_downloads(), "*.csv"))


def latest_csv():
    found = set()
    for pat in ("*fab*sales*.csv", "*fab*.csv", "*sales*report*.csv"):
        found.update(glob.glob(os.path.join(_downloads(), pat)))
    return max(found, key=os.path.getmtime) if found else None


def _read_csv(path):
    """Parse a Fab sales CSV → {say, show}."""
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            cols = {c.lower().strip(): c for c in (reader.fieldnames or [])}

            def col(*names):
                for n in names:
                    for lc, orig in cols.items():
                        if n in lc:
                            return orig
                return None

            c_day, c_title = col("day", "date"), col("listing", "title", "product")
            c_units, c_sales = col("net unit", "unit", "quantity"), col("net sale", "sales", "amount")
            data = [(str(row.get(c_day, "")).strip(), (row.get(c_title) or "").strip(),
                     _num(row.get(c_units)), _num(row.get(c_sales)))
                    for row in reader]
    except Exception as e:
        return {"say": f"I couldn't read your Fab report, Sir: {e}", "show": str(e)}
    return _summarize(data, os.path.basename(path))


def report():
    """Summarize the most recent Fab CSV already in Downloads (no download)."""
    path = latest_csv()
    return _read_csv(path) if path else None


def _report_url():
    from datetime import date, timedelta
    end = date.today().isoformat()
    start = (date.today() - timedelta(days=config.FAB_REPORT_DAYS)).isoformat()
    return f"{config.FAB_DOWNLOAD_URL}?end_date={end}&group_by=day&start_date={start}"


def _brave_cookie_header():
    """Read fab.com cookies straight from Brave (session + Cloudflare clearance),
    so PATROAM can fetch the CSV itself with no window and no button. Returns a
    'Cookie:' header string, or '' if cookies aren't available."""
    try:
        import browser_cookie3
    except ImportError:
        return ""
    for loader in ("brave", "chrome", "edge"):
        try:
            cj = getattr(browser_cookie3, loader)(domain_name="fab.com")
            pairs = [f"{c.name}={c.value}" for c in cj]
            if pairs:
                return "; ".join(pairs)
        except Exception:
            continue
    return ""


def download_silently(url=None):
    """Fetch the Fab CSV directly using your Brave cookies — no browser window, no
    click. Saves it to Downloads and returns the path, or None if it didn't get a
    CSV (e.g. cookies missing or Cloudflare still challenged the request)."""
    import urllib.request
    from datetime import date
    cookie = _brave_cookie_header()
    if not cookie:
        return None
    url = url or _report_url()
    ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
    req = urllib.request.Request(url, headers={
        "Cookie": cookie, "User-Agent": ua,
        "Accept": "text/csv,application/octet-stream,*/*",
        "Referer": config.FAB_SALES_URL})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            ctype = (r.headers.get("content-type") or "").lower()
            data = r.read()
    except Exception:
        return None
    head = data[:300].lstrip().lower()
    if not data or head.startswith(b"<") or "html" in ctype:
        return None                              # got a Cloudflare/login page, not a CSV
    path = os.path.join(_downloads(), f"fab_sales_{date.today().isoformat()}.csv")
    try:
        with open(path, "wb") as f:
            f.write(data)
    except Exception:
        return None
    return path


def download_and_read(wait=20):
    """Get a fresh Fab report and read it. First tries a silent direct download
    (Brave cookies, no window). If that's blocked, opens the download endpoint in
    Brave and waits for the file to land in Downloads."""
    # 1) Silent download — no window, no button.
    path = download_silently()
    if path:
        return _read_csv(path)

    # 2) Fallback — let Brave do it (may need a click), then pick up the file.
    import time
    from . import skills
    t0 = time.time()
    skills.open_url_in_brave(_report_url())
    deadline = time.time() + wait
    while time.time() < deadline:
        time.sleep(0.7)
        fresh = [f for f in _all_csvs() if os.path.getmtime(f) >= t0 - 2]
        if fresh:
            named = [f for f in fresh if "fab" in os.path.basename(f).lower()
                     or "sales" in os.path.basename(f).lower()]
            path = max(named or fresh, key=os.path.getmtime)
            break
    if not path:
        path = latest_csv()                      # fall back to whatever's there
    return _read_csv(path) if path else None
