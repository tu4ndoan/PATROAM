"""Wake-word spotting on a transcript.

"patroam" is a coined word, so no pretrained wake-word model recognizes it and
training a custom one needs an account + dataset. For v1 we instead spot the
word inside continuous speech-to-text output, tolerating the ways STT mishears
it (e.g. "patron", "pat rome") via fuzzy matching.

This lives behind `find_command` so the listener doesn't care *how* detection
works — a Porcupine/openWakeWord backend can replace this later untouched.
"""

import difflib
import unicodedata
import re

from .. import config


def _normalize(text: str) -> str:
    """Fold to lowercase ASCII for matching.

    Accents are TRANSLITERATED, not deleted: stripping them turned "mở" into the
    single letter "m", which then glued onto the wake word and got swallowed as
    part of it ("patroam mở spotify" → "spotify")."""
    t = unicodedata.normalize("NFD", (text or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))   # é→e, ở→o
    t = t.replace("đ", "d").replace("Đ", "d")                   # NFD leaves đ intact
    return re.sub(r"[^a-z0-9 ]", "", t).strip()


# Words as the user actually said them (accents intact) — used to return the
# command verbatim. Matching still happens on the ASCII-folded form.
_WORD_RE = re.compile(r"\S+")


def find_command(text: str):
    """Scan `text` for the wake word.

    Returns:
        None                 -> no wake word present (ignore the utterance)
        ""                   -> wake word present but nothing followed it
        "<command>"          -> wake word present, followed by this command

    The command keeps its original spelling: matching folds to ASCII (so English
    mishearings of "patroam" still match), but folding the RESULT stripped every
    Vietnamese accent — "lịch hôm nay" came back as "lch hm nay".
    """
    if not (text or "").strip():
        return None
    raw = _WORD_RE.findall(text.strip())
    tokens = [_normalize(w) for w in raw]
    # Drop words that normalise to nothing (pure punctuation), keeping the two
    # lists aligned.
    pairs = [(o, n) for o, n in zip(raw, tokens) if n]
    if not pairs:
        return None
    raw = [o for o, _ in pairs]
    tokens = [n for _, n in pairs]
    n = len(tokens)
    # Each wake phrase as a list of tokens; try longer phrases first so
    # "hey patroam" wins over bare "patroam".
    phrases = [p.split() for p in (_normalize(x) for x in config.WAKE_PHRASES) if p]
    phrases.sort(key=len, reverse=True)

    for i in range(n):
        for ptoks in phrases:
            L = len(ptoks)
            if L == 0 or i + L > n:
                continue
            heard = "".join(tokens[i:i + L])
            want = "".join(ptoks)
            # Multi-word phrases need a stricter score: a loose match there eats a
            # real word of the command ("patroam mở spotify" → "spotify").
            need = config.WAKE_WORD_FUZZ if L == 1 else min(0.88, config.WAKE_WORD_FUZZ + 0.12)
            if heard == want or difflib.SequenceMatcher(None, heard, want).ratio() >= need:
                return " ".join(raw[i + L:])          # original spelling

    return None


def is_stop_phrase(text: str) -> bool:
    """True if `text` is a request to end the conversation session."""
    norm = _normalize(text)
    if not norm:
        return False
    return any(_normalize(p) in norm for p in config.STOP_PHRASES)
