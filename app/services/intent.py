"""Turn raw transcript text into a structured form: {action, payee_text, amount_text}.

This is plain deterministic code. Text from the customer is DATA: nothing in it can
trigger an action other than filling these two fields. Strings that look like
instructions to the system are flagged and ignored.
"""
from __future__ import annotations

import re

from app.services import amount_parser as ap

INJECTION_PATTERNS = [
    r"ignore (all |your |the |previous |prior )*(instructions|rules|prompt)",
    r"disregard (all |your |the |previous )*(instructions|rules)",
    r"(system|developer) prompt", r"you are now", r"act as", r"jailbreak",
    r"override", r"bypass", r"skip (the )?(pin|verification|liveness|confirmation)",
    r"reveal .*(pin|password|instructions)", r"send (all|everything|my money)",
    r"without (asking|confirm)",
]
CANCEL_WORDS = {"cancel", "stop", "no", "nahi", "nahin", "mat", "ruko", "ruk", "abort", "रद्द", "नहीं", "रुको"}
CONFIRM_PHRASES = {"confirm", "confirmed", "yes confirm", "haan confirm", "confirm karo", "कन्फर्म", "कन्फर्म करो"}
PAYEE_STOPWORDS = {"pay", "send", "transfer", "to", "ko", "ne", "for", "please", "kindly",
                   "bhej", "bhejo", "karo", "kar", "de", "dena", "ka", "ki", "se", "mujhe",
                   "payment", "paisa", "paise", "money", "amount", "rupaye", "rupees",
                   "rupee", "rs", "inr", "bhijwa", "dijiye", "dijie", "karna"}


def looks_like_injection(text: str) -> bool:
    t = (text or "").lower()
    return any(re.search(p, t) for p in INJECTION_PATTERNS)


def _is_amount_token(tok: str) -> bool:
    if tok in ap._UNITS or tok in ap._TENS or tok in ap._HINDI or tok in ap._SCALES:
        return True
    return tok in ap._FRACTION_PREFIX or bool(re.fullmatch(r"\d+(\.\d+)?", tok))


def extract_intent(text: str) -> dict:
    """Return {action, payee_text, amount_text, injection, error}."""
    raw = (text or "").strip()
    out = {"action": "unknown", "payee_text": "", "amount_text": "", "injection": False, "error": None}
    if not raw:
        out["action"] = "silence"
        return out
    if looks_like_injection(raw):
        out["injection"] = True
        return out

    norm = ap.normalize_text(raw)
    tokens = norm.split()
    if set(tokens) & CANCEL_WORDS and len(tokens) <= 3:
        out["action"] = "cancel"
        return out

    amount_idx = [i for i, t in enumerate(tokens)
                  if _is_amount_token(t) or (t == "and" and 0 < len(tokens) - 1)]
    # 'and' only counts when it sits between amount tokens.
    amount_idx = [i for i in amount_idx if tokens[i] != "and" or
                  (i - 1 in amount_idx and i + 1 < len(tokens) and _is_amount_token(tokens[i + 1]))]
    if amount_idx:
        if amount_idx[-1] - amount_idx[0] + 1 != len(amount_idx):
            # amounts separated by other words: could be two payments -> ask, never merge
            out["error"] = "multiple_amounts"
        out["amount_text"] = " ".join(tokens[i] for i in amount_idx)
    rest = [t for i, t in enumerate(tokens) if i not in amount_idx and t not in PAYEE_STOPWORDS]
    out["payee_text"] = " ".join(rest)
    if out["amount_text"] or out["payee_text"]:
        out["action"] = "pay"
    return out
