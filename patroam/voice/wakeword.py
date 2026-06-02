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
    variants = {v.replace(" ", "") for v in config.WAKE_WORD_VARIANTS}
    n = len(tokens)

    for start in range(n):
        acc = ""
        # The wake word may be split across up to 3 STT tokens ("pat rome a").
        for end in range(start, min(start + 3, n)):
            acc += tokens[end]
            ratio = difflib.SequenceMatcher(None, acc, config.WAKE_WORD).ratio()
            if acc in variants or ratio >= config.WAKE_WORD_FUZZ:
                return " ".join(tokens[end + 1:])

    return None


def is_stop_phrase(text: str) -> bool:
    """True if `text` is a request to end the conversation session."""
    norm = _normalize(text)
    if not norm:
        return False
    return any(_normalize(p) in norm for p in config.STOP_PHRASES)
