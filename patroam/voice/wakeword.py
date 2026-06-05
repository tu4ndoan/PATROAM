"""Wake-word spotting on a transcript.

"patroam" is a coined word, so no pretrained wake-word model recognizes it and
training a custom one needs an account + dataset. For v1 we instead spot the
word inside continuous speech-to-text output, tolerating the ways STT mishears
it (e.g. "patron", "pat rome") via fuzzy matching.

This lives behind `find_command` so the listener doesn't care *how* detection
works — a Porcupine/openWakeWord backend can replace this later untouched.
"""

import difflib
import re

from .. import config


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


def find_command(text: str):
    """Scan `text` for the wake word.

    Returns:
        None                 -> no wake word present (ignore the utterance)
        ""                   -> wake word present but nothing followed it
        "<command>"          -> wake word present, followed by this command
    """
    norm = _normalize(text)
    if not norm:
        return None

    tokens = norm.split()
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
            if heard == want or difflib.SequenceMatcher(None, heard, want).ratio() >= config.WAKE_WORD_FUZZ:
                return " ".join(tokens[i + L:])

    return None


def is_stop_phrase(text: str) -> bool:
    """True if `text` is a request to end the conversation session."""
    norm = _normalize(text)
    if not norm:
        return False
    return any(_normalize(p) in norm for p in config.STOP_PHRASES)
