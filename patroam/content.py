"""Content pipeline — turn ONE edited video into platform-tailored posts and
publish it to TikTok, Instagram, YouTube, Threads and X.

Flow ("post my reel", "publish my video", "đăng reel mới"):
  1. Locate the video — an explicit path, or the newest clip in CONTENT_DIR.
  2. Read the brief — dictated in the command, or a sidecar `<video>.txt`/`.md`.
  3. Generate a hook + caption + hashtags TAILORED to each platform via the LLM
     (TikTok short & punchy, Threads conversational, YouTube SEO-titled, ...).
  4. Publish to each platform whose credentials are configured; for the rest,
     fall back to ASSISTED mode — open the upload page in Brave and copy that
     platform's caption to the clipboard so you paste it. So the pipeline works
     today, and every platform turns fully automatic once its token is set.
  5. Log the whole package (video + per-platform captions + results) so
     "what did I post" can recall it.

Publishers are pluggable: `_PUBLISHERS[platform]` is a callable
`(video_path, post, public_url) -> {ok, url|error}`; each first checks whether
its credentials are present and returns `{"assisted": True}` if not.
"""

import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime

from . import config, llm

IS_WIN = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"

# Human-facing platform names.
NAMES = {"tiktok": "TikTok", "instagram": "Instagram", "youtube": "YouTube Shorts",
         "threads": "Threads", "x": "X"}


# ── video + brief discovery ────────────────────────────────────────────────────────
def _newest_video():
    """The most recently modified video in CONTENT_DIR, or None."""
    try:
        files = [os.path.join(config.CONTENT_DIR, f) for f in os.listdir(config.CONTENT_DIR)]
    except Exception:
        return None
    vids = [f for f in files if os.path.isfile(f)
            and f.lower().endswith(config.CONTENT_VIDEO_EXTS)]
    return max(vids, key=os.path.getmtime) if vids else None


def _find_video(hint=""):
    """Resolve the video to post: an explicit path in the command, else the newest
    clip in CONTENT_DIR."""
    hint = (hint or "").strip().strip('"')
    if hint and os.path.isfile(hint) and hint.lower().endswith(config.CONTENT_VIDEO_EXTS):
        return hint
    # A bare filename → look for it inside CONTENT_DIR.
    if hint:
        cand = os.path.join(config.CONTENT_DIR, os.path.basename(hint))
        if os.path.isfile(cand):
            return cand
    return _newest_video()


def _sidecar_brief(video_path):
    """Read a `<video>.txt` / `.md` brief sitting next to the video, if any."""
    base = os.path.splitext(video_path)[0]
    for ext in (".txt", ".md", ".brief"):
        p = base + ext
        if os.path.isfile(p):
            try:
                with open(p, encoding="utf-8") as f:
                    return f.read().strip()
            except Exception:
                pass
    return ""


# ── caption / hook generation ──────────────────────────────────────────────────────
_GEN_PROMPT = """You are a short-form social copywriter for the creator described below. \
From ONE video and its brief, write posts TAILORED to each platform. Return ONLY a JSON \
object — no prose, no markdown fences.

Creator / niche:
{niche}

Video brief (what this specific video is about):
{brief}

Language: write ALL copy in {language} (keep hashtags as single tokens; proper nouns \
and platform names stay as-is).

Rules per platform — respect each platform's native style:
- tiktok: hook (first line, <=12 words, makes them stop scrolling) + a SHORT caption \
(1-2 lines) + 3-5 hashtags (mix niche + money tags).
- instagram: hook + a 3-5 line caption (hook -> 2-3 value lines -> a call to action) + \
5-9 hashtags.
- youtube: an SEO title (<=70 chars, contains the main keyword) + a 2-3 line description \
+ 3-5 hashtags including #Shorts.
- threads: hook + a conversational, confessional caption that reads like a personal story \
and ENDS WITH AN OPEN QUESTION. 0-1 hashtag only.
- x: one sharp line (the hook) + a punchy 1-2 line caption + 1-2 hashtags.

Every hook must be honest and concrete (show a real result, avoid empty "passive income" \
hype). Use a call to action that grows a following (follow / comment a keyword).

JSON shape (fill every field):
{{"tiktok":{{"hook":"","caption":"","hashtags":[]}},
"instagram":{{"hook":"","caption":"","hashtags":[]}},
"youtube":{{"title":"","caption":"","hashtags":[]}},
"threads":{{"hook":"","caption":"","hashtags":[]}},
"x":{{"hook":"","caption":"","hashtags":[]}}}}"""


def _clean_tag(t):
    t = str(t or "").strip().lstrip("#")
    t = "".join(ch for ch in t if ch.isalnum())
    return ("#" + t) if t else ""


def _assemble_text(platform, post):
    """Compose the final string to post from a platform's caption + hashtags."""
    cap = (post.get("caption") or "").strip()
    tags = [_clean_tag(t) for t in (post.get("hashtags") or [])]
    tags = [t for t in tags if t]
    if platform == "youtube":                       # description; title is separate
        body = cap
    elif platform == "threads":                     # keep it clean, 1 tag max
        body = cap + ((" " + tags[0]) if tags else "")
        return body.strip()
    else:
        body = cap
    if tags:
        body = (body + "\n\n" + " ".join(tags)).strip()
    return body.strip()


def _fallback_posts(brief):
    """Deterministic posts when the LLM is unavailable — plain but usable."""
    brief = (brief or "my latest video").strip().rstrip(".")
    hook = f"Here's {brief}"
    base_tags = ["gamedev", "blender", "unrealengine", "3dart", "gameassets",
                 "indiedev", "passiveincome"]
    out = {}
    for p in ("tiktok", "instagram", "youtube", "threads", "x"):
        n = {"threads": 1, "x": 2, "tiktok": 4}.get(p, 6)
        out[p] = {"hook": hook, "caption": f"{hook}. Follow to see how I do it.",
                  "hashtags": base_tags[:n]}
        if p == "youtube":
            out[p]["title"] = brief[:70]
    return out


def generate_posts(brief):
    """Return {platform: {hook, caption, hashtags, title?, text}} for every platform."""
    raw = None
    if llm.available():
        prompt = _GEN_PROMPT.format(
            niche=config.CONTENT_NICHE, brief=(brief or "(no brief given — infer from the niche)"),
            language=config.RESPONSE_LANGUAGE or "English")
        raw = llm.complete(prompt, timeout=60)
    data = None
    if raw:
        try:
            i, j = raw.find("{"), raw.rfind("}")
            data = json.loads(raw[i:j + 1]) if i >= 0 and j > i else None
        except Exception:
            data = None
    if not isinstance(data, dict):
        data = _fallback_posts(brief)
    # Normalise + assemble the final post text for each platform.
    posts = {}
    fb = _fallback_posts(brief)
    for p in ("tiktok", "instagram", "youtube", "threads", "x"):
        post = data.get(p) if isinstance(data.get(p), dict) else fb[p]
        post.setdefault("hook", fb[p]["hook"])
        post.setdefault("caption", post.get("hook", ""))
        post.setdefault("hashtags", [])
        if p == "youtube" and not post.get("title"):
            post["title"] = (post.get("hook") or fb[p].get("title", ""))[:70]
        post["text"] = _assemble_text(p, post)
        posts[p] = post
    return posts


# ── clipboard + browser helpers (for assisted mode) ────────────────────────────────
def _to_clipboard(text):
    """Copy `text` to the OS clipboard. Best-effort; returns True on success."""
    try:
        if IS_WIN:
            p = subprocess.Popen("clip", stdin=subprocess.PIPE, shell=True)
            p.communicate(input=text.encode("utf-16le"))
            return p.returncode == 0
        if IS_MAC:
            p = subprocess.Popen("pbcopy", stdin=subprocess.PIPE)
            p.communicate(input=text.encode("utf-8"))
            return p.returncode == 0
        p = subprocess.Popen(["xclip", "-selection", "clipboard"], stdin=subprocess.PIPE)
        p.communicate(input=text.encode("utf-8"))
        return p.returncode == 0
    except Exception:
        return False


def _public_url(video_path):
    """Map the local video to a public URL if CONTENT_PUBLIC_BASE is configured
    (needed by Instagram/Threads, which pull the video from a url). Else ''."""
    base = (config.CONTENT_PUBLIC_BASE or "").rstrip("/")
    if not base:
        return ""
    try:
        rel = os.path.relpath(video_path, config.CONTENT_DIR).replace(os.sep, "/")
    except Exception:
        rel = os.path.basename(video_path)
    if rel.startswith(".."):
        rel = os.path.basename(video_path)
    return base + "/" + urllib.parse.quote(rel)


# ── HTTP helpers ────────────────────────────────────────────────────────────────────
def _req(url, method="GET", headers=None, data=None, timeout=120):
    """Minimal JSON HTTP. `data` bytes are sent as-is; returns parsed JSON or {'_raw'}."""
    r = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        body = resp.read()
        loc = resp.headers.get("Location")
    try:
        out = json.loads(body.decode("utf-8")) if body else {}
    except Exception:
        out = {"_raw": body[:400].decode("utf-8", "replace")}
    if loc:
        out["_location"] = loc
    return out


def _post_form(url, fields, timeout=120):
    """POST application/x-www-form-urlencoded, JSON response."""
    data = urllib.parse.urlencode(fields).encode("utf-8")
    return _req(url, "POST", {"Content-Type": "application/x-www-form-urlencoded"}, data, timeout)


# ── publishers (each: (video, post, public_url) -> {ok|assisted|error}) ─────────────
def _pub_youtube(video, post, public_url):
    """YouTube Shorts via Data API v3 resumable upload of the LOCAL file."""
    if not (config.YOUTUBE_CLIENT_ID and config.YOUTUBE_CLIENT_SECRET
            and config.YOUTUBE_REFRESH_TOKEN):
        return {"assisted": True}
    try:
        tok = _post_form("https://oauth2.googleapis.com/token", {
            "client_id": config.YOUTUBE_CLIENT_ID,
            "client_secret": config.YOUTUBE_CLIENT_SECRET,
            "refresh_token": config.YOUTUBE_REFRESH_TOKEN,
            "grant_type": "refresh_token"})
        access = tok.get("access_token")
        if not access:
            return {"error": "YouTube token refresh failed"}
        title = post.get("title") or post.get("hook") or "New Short"
        meta = json.dumps({
            "snippet": {"title": title[:100], "description": post.get("text", ""),
                        "tags": [t.lstrip("#") for t in post.get("hashtags", [])][:15],
                        "categoryId": "20"},                      # 20 = Gaming
            "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False}}).encode()
        size = os.path.getsize(video)
        init = _req("https://www.googleapis.com/upload/youtube/v3/videos"
                    "?uploadType=resumable&part=snippet,status", "POST",
                    {"Authorization": "Bearer " + access, "Content-Type": "application/json",
                     "X-Upload-Content-Type": "video/*", "X-Upload-Content-Length": str(size)},
                    meta)
        up = init.get("_location")
        if not up:
            return {"error": "YouTube: no upload URL"}
        with open(video, "rb") as f:
            res = _req(up, "PUT", {"Content-Type": "video/*", "Content-Length": str(size)},
                       f.read(), timeout=600)
        vid = res.get("id")
        return {"ok": True, "url": f"https://youtube.com/shorts/{vid}"} if vid \
            else {"error": "YouTube upload returned no id"}
    except Exception as e:
        return {"error": f"YouTube: {e}"}


def _pub_tiktok(video, post, public_url):
    """TikTok via Content Posting API (FILE_UPLOAD of the local file)."""
    if not config.TIKTOK_ACCESS_TOKEN:
        return {"assisted": True}
    try:
        size = os.path.getsize(video)
        hdr = {"Authorization": "Bearer " + config.TIKTOK_ACCESS_TOKEN,
               "Content-Type": "application/json; charset=UTF-8"}
        body = json.dumps({
            "post_info": {"title": post.get("text", "")[:2200],
                          "privacy_level": "SELF_ONLY", "disable_comment": False},
            "source_info": {"source": "FILE_UPLOAD", "video_size": size,
                            "chunk_size": size, "total_chunk_count": 1}}).encode()
        init = _req("https://open.tiktokapis.com/v2/post/publish/video/init/",
                    "POST", hdr, body)
        up = (init.get("data") or {}).get("upload_url")
        if not up:
            return {"error": f"TikTok init failed: {init.get('error') or init}"}
        with open(video, "rb") as f:
            _req(up, "PUT", {"Content-Type": "video/mp4",
                             "Content-Range": f"bytes 0-{size - 1}/{size}",
                             "Content-Length": str(size)}, f.read(), timeout=600)
        pid = (init.get("data") or {}).get("publish_id")
        # Privacy is SELF_ONLY until your TikTok app is audited for direct public posts.
        return {"ok": True, "url": "https://www.tiktok.com/", "note": "posted (private until app audited)",
                "publish_id": pid}
    except Exception as e:
        return {"error": f"TikTok: {e}"}


def _pub_instagram(video, post, public_url):
    """Instagram Reels via Graph API. Needs a PUBLIC video url (Graph pulls it)."""
    if not (config.IG_ACCESS_TOKEN and config.IG_USER_ID):
        return {"assisted": True}
    if not public_url:
        return {"assisted": True, "note": "set PATROAM_CONTENT_PUBLIC_BASE for auto Reels"}
    try:
        base = "https://graph.facebook.com/v21.0/"
        cont = _post_form(f"{base}{config.IG_USER_ID}/media", {
            "media_type": "REELS", "video_url": public_url,
            "caption": post.get("text", ""), "access_token": config.IG_ACCESS_TOKEN})
        cid = cont.get("id")
        if not cid:
            return {"error": f"IG container failed: {cont.get('error') or cont}"}
        for _ in range(30):                                   # wait for processing
            time.sleep(5)
            st = _req(f"{base}{cid}?fields=status_code&access_token={config.IG_ACCESS_TOKEN}")
            if st.get("status_code") == "FINISHED":
                break
            if st.get("status_code") == "ERROR":
                return {"error": "IG processing error"}
        pub = _post_form(f"{base}{config.IG_USER_ID}/media_publish",
                         {"creation_id": cid, "access_token": config.IG_ACCESS_TOKEN})
        return {"ok": True, "url": f"https://www.instagram.com/reel/{pub.get('id', '')}"} \
            if pub.get("id") else {"error": f"IG publish failed: {pub}"}
    except Exception as e:
        return {"error": f"Instagram: {e}"}


def _pub_threads(video, post, public_url):
    """Threads via Threads Graph API. Needs a PUBLIC video url."""
    if not (config.THREADS_ACCESS_TOKEN and config.THREADS_USER_ID):
        return {"assisted": True}
    if not public_url:
        return {"assisted": True, "note": "set PATROAM_CONTENT_PUBLIC_BASE for auto Threads"}
    try:
        base = "https://graph.threads.net/v1.0/"
        cont = _post_form(f"{base}{config.THREADS_USER_ID}/threads", {
            "media_type": "VIDEO", "video_url": public_url,
            "text": post.get("text", ""), "access_token": config.THREADS_ACCESS_TOKEN})
        cid = cont.get("id")
        if not cid:
            return {"error": f"Threads container failed: {cont.get('error') or cont}"}
        for _ in range(30):
            time.sleep(5)
            st = _req(f"{base}{cid}?fields=status&access_token={config.THREADS_ACCESS_TOKEN}")
            if st.get("status") == "FINISHED":
                break
            if st.get("status") == "ERROR":
                return {"error": "Threads processing error"}
        pub = _post_form(f"{base}{config.THREADS_USER_ID}/threads_publish",
                         {"creation_id": cid, "access_token": config.THREADS_ACCESS_TOKEN})
        return {"ok": True, "url": f"https://www.threads.net/@you/post/{pub.get('id', '')}"} \
            if pub.get("id") else {"error": f"Threads publish failed: {pub}"}
    except Exception as e:
        return {"error": f"Threads: {e}"}


def _pub_x(video, post, public_url):
    """X (Twitter) via API v2 + v1.1 chunked media upload of the LOCAL file.
    Uses OAuth 1.0a — needs `requests_oauthlib` and a posting-enabled tier."""
    if not (config.X_API_KEY and config.X_API_SECRET
            and config.X_ACCESS_TOKEN and config.X_ACCESS_SECRET):
        return {"assisted": True}
    try:
        from requests_oauthlib import OAuth1Session
    except ImportError:
        return {"assisted": True, "note": "pip install requests_oauthlib for auto X posts"}
    try:
        x = OAuth1Session(config.X_API_KEY, config.X_API_SECRET,
                          config.X_ACCESS_TOKEN, config.X_ACCESS_SECRET)
        up = "https://upload.twitter.com/1.1/media/upload.json"
        size = os.path.getsize(video)
        init = x.post(up, data={"command": "INIT", "media_type": "video/mp4",
                                "total_bytes": size, "media_category": "tweet_video"}).json()
        mid = init.get("media_id_string")
        if not mid:
            return {"error": f"X INIT failed: {init}"}
        with open(video, "rb") as f:
            idx, chunk = 0, f.read(4 * 1024 * 1024)
            while chunk:
                x.post(up, data={"command": "APPEND", "media_id": mid, "segment_index": idx},
                       files={"media": chunk})
                idx += 1
                chunk = f.read(4 * 1024 * 1024)
        fin = x.post(up, data={"command": "FINALIZE", "media_id": mid}).json()
        info = fin.get("processing_info", {})
        while info.get("state") in ("pending", "in_progress"):
            time.sleep(info.get("check_after_secs", 5))
            info = x.get(up, params={"command": "STATUS", "media_id": mid}) \
                .json().get("processing_info", {})
        if info.get("state") == "failed":
            return {"error": "X media processing failed"}
        tw = x.post("https://api.twitter.com/2/tweets",
                    json={"text": post.get("text", "")[:280], "media": {"media_ids": [mid]}}).json()
        tid = (tw.get("data") or {}).get("id")
        return {"ok": True, "url": f"https://x.com/i/status/{tid}"} if tid \
            else {"error": f"X tweet failed: {tw}"}
    except Exception as e:
        return {"error": f"X: {e}"}


_PUBLISHERS = {"youtube": _pub_youtube, "tiktok": _pub_tiktok, "instagram": _pub_instagram,
               "threads": _pub_threads, "x": _pub_x}


def _assisted(platform, post):
    """Open the platform's upload page in Brave and copy its caption to the clipboard."""
    from . import skills
    url = config.CONTENT_UPLOAD_URLS.get(platform, "")
    if url:
        skills.open_url_in_brave(url)
    copied = _to_clipboard(post.get("text", ""))
    return copied


# ── content log ─────────────────────────────────────────────────────────────────────
def _log(entry):
    try:
        os.makedirs(config.CONTENT_DIR, exist_ok=True)
        try:
            with open(config.CONTENT_LOG, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = []
        data.insert(0, entry)
        with open(config.CONTENT_LOG, "w", encoding="utf-8") as f:
            json.dump(data[:200], f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ── orchestrator ────────────────────────────────────────────────────────────────────
def publish(brief="", video_hint="", platforms=None):
    """Generate tailored posts and publish/queue them across the platforms.
    Returns {say, show} for the orb + chat."""
    video = _find_video(video_hint)
    if not video:
        return {"say": "I couldn't find a video to post, Sir. Drop your edited clip in the "
                       "content folder and try again.",
                "show": f"No video found. Put your clip in:\n{config.CONTENT_DIR}"}
    brief = (brief or "").strip() or _sidecar_brief(video)
    posts = generate_posts(brief)
    public_url = _public_url(video)
    targets = [p for p in (platforms or config.CONTENT_PLATFORMS) if p in _PUBLISHERS]

    posted, assisted, failed = [], [], []
    results = {}
    for p in targets:
        post = posts.get(p, {})
        try:
            res = _PUBLISHERS[p](video, post, public_url)
        except Exception as e:
            res = {"error": str(e)}
        if res.get("ok"):
            posted.append(p)
        elif res.get("assisted"):
            _assisted(p, post)
            assisted.append(p)
        else:
            failed.append(p)
        results[p] = res

    _log({"ts": datetime.now().isoformat(), "video": os.path.basename(video),
          "brief": brief, "posts": posts, "results": results})

    # Spoken summary.
    say_bits = []
    if posted:
        say_bits.append(f"Posted to {', '.join(NAMES[p] for p in posted)}")
    if assisted:
        say_bits.append(f"opened {', '.join(NAMES[p] for p in assisted)} with the caption "
                        "copied for you to paste")
    if failed:
        say_bits.append(f"{', '.join(NAMES[p] for p in failed)} failed")
    say = ("; ".join(say_bits) + ".") if say_bits else "Nothing to post, Sir."

    # Chat detail: the captions + per-platform outcome.
    L = [f"🎬 Content pipeline — {os.path.basename(video)}"]
    if brief:
        L.append(f"Brief: {brief}")
    L.append("")
    icon = {"posted": "✅", "assisted": "📋", "failed": "⚠️"}
    for p in targets:
        r = results[p]
        state = "posted" if r.get("ok") else ("assisted" if r.get("assisted") else "failed")
        head = f"{icon[state]} {NAMES[p]}"
        if r.get("ok") and r.get("url"):
            head += f" → {r['url']}"
        elif state == "assisted":
            head += " → upload page opened, caption copied to clipboard"
        elif r.get("error"):
            head += f" → {r['error']}"
        if r.get("note"):
            head += f"  ({r['note']})"
        L.append(head)
        post = posts.get(p, {})
        if post.get("title"):
            L.append(f"    Title: {post['title']}")
        L.append("    " + (post.get("text", "").replace("\n", "\n    ")))
        L.append("")
    return {"say": say, "show": "\n".join(L).rstrip()}


def last_posts(n=5):
    """Recall the most recent content packages ('what did I post')."""
    try:
        with open(config.CONTENT_LOG, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    if not data:
        return None
    L = ["🗂️ Recent posts", ""]
    for e in data[:n]:
        when = (e.get("ts") or "")[:16].replace("T", " ")
        res = e.get("results", {})
        ok = [NAMES.get(p, p) for p, r in res.items() if r.get("ok")]
        L.append(f"• {when} — {e.get('video', '?')}"
                 + (f"  ✅ {', '.join(ok)}" if ok else ""))
        if e.get("brief"):
            L.append(f"    {e['brief'][:80]}")
    say = f"Your last post was {data[0].get('video', 'a video')}."
    return {"say": say, "show": "\n".join(L)}
