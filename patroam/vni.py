"""Convert VNI-Win legacy Vietnamese text → Unicode.

Many older Vietnamese PDFs embed "VNI-Helve" fonts whose text layer is NOT
Unicode: each accented letter is stored as a base vowel + a separate "mark"
glyph (e.g. `oâ`=ô, `eä`=ệ, `aá`=ấ, `uù`=ú, `ñ`=đ). Every PDF text extractor
therefore returns garbage. This module reverses that mapping deterministically.

Reconstructed from a real VNI PDF; covers the full common tone/diacritic set.
"""

import unicodedata

# Combining tone marks (added then NFC-composed into a single code point).
_TONE = {"sac": "́", "huyen": "̀", "hoi": "̉",
         "nga": "̃", "nang": "̣"}

# Base vowel + diacritic modifier → modified vowel.
_CIRC = {"a": "â", "e": "ê", "o": "ô", "A": "Â", "E": "Ê", "O": "Ô"}
_BREVE = {"a": "ă", "A": "Ă"}

# Standalone VNI glyphs that map straight to one Unicode letter.
_SOLO = {
    "ñ": "đ", "Ñ": "Đ",
    "ô": "ơ", "Ô": "Ơ", "ö": "ư", "Ö": "Ư",   # horn vowels (standalone in VNI)
    "ì": "ì", "Ì": "Ì", "í": "í", "Í": "Í",     # i + huyền / sắc
    "ò": "ị", "Ò": "Ị", "ó": "ỉ", "Ó": "Ỉ",     # i + nặng / hỏi
    "î": "ĩ", "Î": "Ĩ",                          # i + ngã
}

# Marks that combine with the PRECEDING base vowel: char → (modifier, tone).
_MOD = {
    "â": ("circ", None), "Â": ("circ", None),
    "á": ("circ", "sac"), "Á": ("circ", "sac"),
    "à": ("circ", "huyen"), "À": ("circ", "huyen"),
    "å": ("circ", "hoi"), "Å": ("circ", "hoi"),
    "ã": ("circ", "nga"), "Ã": ("circ", "nga"),
    "ä": ("circ", "nang"), "Ä": ("circ", "nang"),
    "ê": ("breve", None), "Ê": ("breve", None),
    "é": ("breve", "sac"), "É": ("breve", "sac"),
    "è": ("breve", "huyen"), "È": ("breve", "huyen"),
    "ẻ": ("breve", "hoi"),
    "ẽ": ("breve", "nga"),
    "ë": ("breve", "nang"), "Ë": ("breve", "nang"),
}

# Plain tone marks that combine with the preceding vowel (incl. ơ/ư/â/ê/ô/ă).
_PLAIN_TONE = {
    "ù": "sac", "Ù": "sac", "ø": "huyen", "Ø": "huyen", "û": "hoi", "Û": "hoi",
    "õ": "nga", "Õ": "nga", "ï": "nang", "Ï": "nang",
}

# Characters that signal VNI-Win text (rare in normal Unicode).
_SIGNATURE = "ñÑöÖøØûÛäÄãÃåÅäÄ"


def _toned(vowel, tone):
    if not tone or not vowel:
        return vowel
    return unicodedata.normalize("NFC", vowel + _TONE[tone])


def from_vni(text):
    """Decode a VNI-Win string to proper Unicode Vietnamese."""
    out = []
    for ch in text:
        if ch in _SOLO:
            out.append(_SOLO[ch])
        elif ch in _MOD:
            kind, tone = _MOD[ch]
            base = out.pop() if out else ""
            tbl = _CIRC if kind == "circ" else _BREVE
            out.append(_toned(tbl.get(base, base), tone))
        elif ch in _PLAIN_TONE:
            base = out.pop() if out else ""
            out.append(_toned(base, _PLAIN_TONE[ch]))
        else:
            out.append(ch)
    return unicodedata.normalize("NFC", "".join(out))


def looks_like_vni(text):
    """Heuristic: is this extracted text VNI-encoded (so it needs decoding)?"""
    if not text:
        return False
    sig = sum(text.count(c) for c in set(_SIGNATURE))
    return sig >= max(6, len(text) // 500)
