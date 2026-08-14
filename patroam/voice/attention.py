"""Attention gate — "was that meant for me?"

In always-on mode PATROAM has no wake word: it hears everything in the room and
must decide, per utterance, whether it is being ADDRESSED and given something to
act on. Most of what a microphone picks up is not for the assistant — thinking
aloud, talking to someone else, a video playing, a rhetorical question.

Two stages, cheapest first:

  1. A local heuristic throws out the obvious non-commands instantly (too short,
     pure filler, a known stop phrase). No model, no latency.
  2. Whatever survives goes to the model, which answers ADDRESSED or IGNORE.

The gate is deliberately biased towards IGNORE: interrupting a conversation you
weren't part of is far worse than missing one command the user can repeat.
"""

import re

from .. import config

# Utterances shorter than this (in words) are almost never a real command.
_MIN_WORDS = 2

# Pure filler / backchannel — never a command on its own.
_FILLER = {
    "um", "uh", "er", "hmm", "hm", "ah", "oh", "eh", "mm", "mhm", "yeah", "yep",
    "yes", "no", "nope", "ok", "okay", "right", "sure", "well", "so", "like",
    "à", "ừ", "ờ", "ừm", "vâng", "dạ", "ok", "được", "rồi", "thôi", "ê", "ơ",
}

# Talking ABOUT the assistant, or to someone else — a strong ignore signal.
_THIRD_PARTY = re.compile(
    r"\b(he|she|they|him|her|them)\s+(said|says|told|thinks|wants)\b"
    r"|\b(anh|em|ông|bà|chị|nó|họ)\s+(nói|bảo|kể)\b", re.I)

_QUESTION_MARKS = ("?", "？")


def _words(text):
    return re.findall(r"[^\W_]+", (text or "").lower(), re.UNICODE)


def obviously_not_a_command(text):
    """Instant local verdict: True when this clearly needs no response."""
    t = (text or "").strip()
    if not t:
        return True
    w = _words(t)
    if len(w) < _MIN_WORDS:
        return True
    if all(x in _FILLER for x in w):
        return True
    if _THIRD_PARTY.search(t):
        return True
    return False


_PROMPT = (
    "You are the attention gate of a voice assistant named PATROAM that is "
    "always listening in the user's room. Decide whether the utterance below is "
    "ADDRESSED TO THE ASSISTANT and asks for something it should act on.\n\n"
    "Answer ADDRESSED only when the user is clearly talking TO the assistant and "
    "wants an action or an answer — a command, a request, or a question aimed at "
    "it.\n"
    "Answer IGNORE for everything else: thinking out loud, muttering, talking to "
    "another person, a phone call, a rhetorical question, reading or singing "
    "aloud, background TV or music, incomplete fragments, or general chatter "
    "that merely mentions the assistant.\n"
    "When uncertain, answer IGNORE — interrupting uninvited is worse than "
    "missing one request.\n\n"
    "Examples:\n"
    '"what\'s on my calendar tomorrow" -> ADDRESSED\n'
    '"mở spotify đi" -> ADDRESSED\n'
    '"đặt lịch họp thứ 3 lúc 10 giờ" -> ADDRESSED\n'
    '"remind me to call the dentist" -> ADDRESSED\n'
    '"ừ để anh xem đã" -> IGNORE\n'
    '"why is this always so slow" -> IGNORE\n'
    '"anh nói với nó là mai gặp nhé" -> IGNORE\n'
    '"I was telling him about the new project" -> IGNORE\n'
    '"hmm what should I have for lunch" -> IGNORE\n'
    '"ok cool" -> IGNORE\n\n'
    "Reply with exactly one word: ADDRESSED or IGNORE.\n"
    "Utterance: "
)


def is_for_me(text, complete=None):
    """True if `text` should be handled as a command in always-on mode.

    `complete` is an optional (prompt, timeout=…)->str for testing; the active
    model is used otherwise. With no model available the gate returns False —
    without a judge it cannot tell a command from a conversation, and guessing
    would make PATROAM interrupt at random."""
    if obviously_not_a_command(text):
        return False
    fn = complete
    if fn is None:
        from .. import llm
        if not llm.available():
            return False
        fn = llm.complete
    try:
        ans = fn(_PROMPT + '"' + (text or "").strip() + '"',
                 timeout=config.ATTENTION_TIMEOUT)
    except Exception:
        return False
    if not ans:
        return False
    verdict = ans.strip().upper()
    # Require the positive word explicitly — anything unparseable means ignore.
    return "ADDRESSED" in verdict and "IGNORE" not in verdict
