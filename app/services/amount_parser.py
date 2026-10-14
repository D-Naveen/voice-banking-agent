"""Deterministic English / Hinglish / Devanagari amount parser.

Design rules (safety first):
  * Unknown words -> ValueError (never silently skipped inside an amount).
  * Ambiguous constructions ("paanch sau paanch sau", "five five") -> ValueError.
  * Command words such as "bhej do" are removed BEFORE parsing so the verb "do"
    is never mistaken for the number 2.
  * Fractions (dedh, dhai, sawa, sade, paune) are computed exactly with Fraction
    and must resolve to a whole rupee amount.
"""
from __future__ import annotations

import re
import unicodedata
from fractions import Fraction

MAX_AMOUNT = 10_000_000

_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50,
         "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}

# Hindi 1-99 (common romanised spellings).
_HINDI = {
    "ek": 1, "do": 2, "teen": 3, "tin": 3, "char": 4, "chaar": 4,
    "paanch": 5, "panch": 5, "paach": 5, "chhe": 6, "chhah": 6, "che": 6, "chah": 6,
    "saat": 7, "sat": 7, "aath": 8, "ath": 8, "nau": 9, "das": 10, "dus": 10,
    "gyarah": 11, "barah": 12, "terah": 13, "chaudah": 14, "pandrah": 15,
    "solah": 16, "satrah": 17, "atharah": 18, "unnis": 19, "unees": 19,
    "bees": 20, "ikkis": 21, "baees": 22, "baais": 22, "teis": 23, "tais": 23,
    "chaubis": 24, "pachchis": 25, "pachis": 25, "pachees": 25, "chhabbis": 26,
    "sattais": 27, "atthais": 28, "unatis": 29, "untees": 29, "tees": 30,
    "ikatis": 31, "battis": 32, "taintis": 33, "chauntis": 34, "paintis": 35,
    "chhattis": 36, "saintis": 37, "adtis": 38, "untalis": 39, "chalis": 40,
    "chaalis": 40, "iktalis": 41, "bayalis": 42, "taitalis": 43, "chauwalis": 44,
    "paintalis": 45, "chiyalis": 46, "saintalis": 47, "adtalis": 48,
    "unchas": 49, "pachas": 50, "pachaas": 50, "ikyavan": 51, "bavan": 52,
    "trepan": 53, "chauwan": 54, "pachpan": 55, "chhappan": 56, "sattavan": 57,
    "atthavan": 58, "unsath": 59, "saath": 60, "iksath": 61,
    "baasath": 62, "tirsath": 63, "chausath": 64, "painsath": 65,
    "chhiyasath": 66, "sadsath": 67, "adsath": 68, "unhattar": 69,
    "sattar": 70, "ikhattar": 71, "bahattar": 72, "tihattar": 73,
    "chauhattar": 74, "pachhattar": 75, "chhihattar": 76, "satattar": 77,
    "athattar": 78, "unyasi": 79, "assi": 80, "ikyasi": 81, "bayasi": 82,
    "tirasi": 83, "chaurasi": 84, "pachasi": 85, "chhiyasi": 86, "sattasi": 87,
    "atthasi": 88, "nawasi": 89, "nabbe": 90, "ikyanve": 91, "banve": 92,
    "tiranve": 93, "chauranve": 94, "pachanve": 95, "chhiyanve": 96,
    "sattanve": 97, "atthanve": 98, "ninyanve": 99,
}

# Devanagari tokens mapped to the romanised words above.
_DEVANAGARI = {
    "एक": "ek", "दो": "do", "तीन": "teen", "चार": "char", "पांच": "paanch",
    "पाँच": "paanch", "छह": "chhe", "छः": "chhe", "छे": "chhe", "सात": "saat",
    "आठ": "aath", "नौ": "nau", "दस": "das", "ग्यारह": "gyarah", "बारह": "barah",
    "तेरह": "terah", "चौदह": "chaudah", "पंद्रह": "pandrah", "सोलह": "solah",
    "सत्रह": "satrah", "अठारह": "atharah", "उन्नीस": "unnis", "बीस": "bees",
    "इक्कीस": "ikkis", "बाईस": "baees", "तेईस": "teis", "चौबीस": "chaubis",
    "पच्चीस": "pachchis", "छब्बीस": "chhabbis", "सत्ताईस": "sattais",
    "अट्ठाईस": "atthais", "उनतीस": "unatis", "तीस": "tees", "चालीस": "chalis",
    "पचास": "pachas", "साठ": "saath", "सत्तर": "sattar", "अस्सी": "assi",
    "नब्बे": "nabbe",
    "सौ": "sau", "हज़ार": "hazaar", "हजार": "hazaar", "लाख": "lakh", "करोड़": "crore",
    "डेढ़": "dedh", "डेढ": "dedh", "ढाई": "dhai", "सवा": "sawa", "साढ़े": "sade",
    "साढे": "sade", "पौने": "paune",
    "रुपये": "rupaye", "रुपए": "rupaye", "रूपये": "rupaye", "रुपया": "rupaye",
    "रुपय": "rupaye", "को": "ko", "भेज": "bhej", "भेजो": "bhej", "दीजिए": "dijiye",
    "दीजिये": "dijiye", "करो": "karo", "कर": "kar", "दे": "de", "दें": "de",
    "पे": "pay",
}

_SCALES = {"hundred": 100, "sau": 100, "thousand": 1000, "hazaar": 1000,
           "hazar": 1000, "hajar": 1000, "lakh": 100_000, "lac": 100_000,
           "lakhs": 100_000, "crore": 10_000_000, "crores": 10_000_000}
_FRACTION_PREFIX = {"dedh", "dhai", "adhai", "sawa", "sava", "sade", "saadhe", "paune"}

# Words that carry no amount information.
FILLER = {
    "rupaye", "rupaiye", "rupya", "rupay", "rupees", "rupee", "rs", "inr", "only",
    "and", "of", "the", "please", "kindly", "ko", "ne", "to", "for", "pay",
    "send", "transfer", "bhej", "bhejo", "bhejna", "bhijwa", "dijiye", "dijie",
    "dena", "kar", "karo", "karna", "mujhe", "main", "mera", "apna", "se", "ka",
    "ki", "hai", "chahiye", "wala", "wali",
}

# "bhej do", "kar do", "de do" ... : the "do" here is a verb, not the number 2.
_VERB_DO = re.compile(
    r"\b(bhej|bhejo|bhijwa|kar|karo|de|dedo|send|transfer|pay)\s+(do|dijiye|dijie|dena)\b")


def normalize_text(text: str) -> str:
    """Lower-case, convert Devanagari digits, drop currency symbols and commas."""
    text = unicodedata.normalize("NFKC", text or "").lower().strip()
    # Devanagari digits -> ASCII
    text = "".join(str(unicodedata.digit(c)) if c.isdigit() else c for c in text)
    # 1,500 / 1,50,000 -> 1500 / 150000  (comma INSIDE a number, no space)
    text = re.sub(r"(?<=\d),(?=\d)", "", text)
    text = text.replace("₹", " ").replace("/-", " ")
    text = re.sub(r"\brs\.", " rs ", text)
    # Drop punctuation/symbols by Unicode category so Devanagari vowel marks survive.
    text = "".join(" " if (unicodedata.category(c)[0] in "PS" and c != ".") else c for c in text)
    text = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", text)
    text = _VERB_DO.sub(r"\1", text)
    tokens = [_DEVANAGARI.get(t, t) for t in text.split()]
    text = " ".join(tokens)
    text = _VERB_DO.sub(r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


def strip_filler(tokens: list[str]) -> list[str]:
    return [t for t in tokens if t not in FILLER]


def _small_value(tok: str):
    if tok in _UNITS:
        return _UNITS[tok], "unit"
    if tok in _TENS:
        return _TENS[tok], "tens"
    if tok in _HINDI:
        return _HINDI[tok], "hindi"
    return None, None


def _is_number(tok: str) -> bool:
    return bool(re.fullmatch(r"\d+(\.\d+)?", tok))


def parse_amount(text: str) -> int:
    """Parse an English/Hinglish amount phrase into whole rupees.

    Raises ValueError when empty, unsupported, ambiguous, zero or too large.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Amount phrase cannot be empty.")

    tokens = strip_filler(normalize_text(text).split())
    if not tokens:
        raise ValueError("Amount phrase cannot be empty.")

    total = Fraction(0)
    last_scale = None          # strict descending order for thousand/lakh/crore
    small = Fraction(0)        # 0..99 (or digit token) part of the current group
    small_kind = None
    hundreds = Fraction(0)
    hundreds_set = False
    frac = None                # pending dedh / dhai / sawa / sade / paune
    standalone_digits = False
    consumed_any = False

    def group_value() -> Fraction:
        return hundreds + small

    for tok in tokens:
        if tok in _FRACTION_PREFIX:
            if frac is not None or small_kind == "digit_big":
                raise ValueError("Ambiguous amount phrase.")
            frac = {"adhai": "dhai", "sava": "sawa", "saadhe": "sade"}.get(tok, tok)
            if frac in ("dedh", "dhai") and (small or hundreds):
                raise ValueError("Ambiguous amount phrase.")
            continue

        val, kind = _small_value(tok)
        if val is not None:
            if frac in ("dedh", "dhai"):
                raise ValueError("Ambiguous amount phrase.")
            if small:
                # Only English "twenty five" style combinations are allowed.
                if not (small_kind == "tens" and kind == "unit" and 0 < val < 10):
                    raise ValueError("Ambiguous amount phrase (repeated number words).")
                small += val
                small_kind = "combined"
            else:
                small, small_kind = Fraction(val), kind
            consumed_any = True
            continue

        if _is_number(tok):
            n = Fraction(tok)
            if frac in ("dedh", "dhai"):
                raise ValueError("Ambiguous amount phrase.")
            if small or hundreds_set and small:
                raise ValueError("Ambiguous amount phrase (repeated numbers).")
            if n >= 1000:
                if total or hundreds_set or last_scale:
                    raise ValueError("Ambiguous amount phrase.")
                standalone_digits = True
                small_kind = "digit_big"
            else:
                small_kind = "digit"
            small = n
            consumed_any = True
            continue

        if tok in _SCALES:
            scale = _SCALES[tok]
            if small_kind == "digit_big":
                raise ValueError("Ambiguous amount phrase.")
            if frac:
                base = {"dedh": Fraction(3, 2), "dhai": Fraction(5, 2)}.get(frac)
                if base is None:
                    n = small if small else Fraction(1)
                    if frac == "sawa":
                        base = n + Fraction(1, 4)
                    elif frac == "sade":
                        if not small:
                            raise ValueError("'sade' needs a number.")
                        base = n + Fraction(1, 2)
                    else:  # paune
                        if not small:
                            raise ValueError("'paune' needs a number.")
                        base = n - Fraction(1, 4)
                if scale == 100:
                    if hundreds_set:
                        raise ValueError("Ambiguous amount phrase.")
                    hundreds, hundreds_set, small, small_kind = base * 100, True, Fraction(0), None
                else:
                    if last_scale is not None and scale >= last_scale:
                        raise ValueError("Amount words are out of order.")
                    total += (hundreds + base) * scale
                    last_scale, hundreds, hundreds_set, small, small_kind = scale, Fraction(0), False, Fraction(0), None
                frac = None
                consumed_any = True
                continue
            if scale == 100:
                if hundreds_set:
                    raise ValueError("Ambiguous amount phrase (two 'hundred' parts).")
                if small >= 100:
                    raise ValueError("Ambiguous amount phrase.")
                hundreds = (small if small else Fraction(1)) * 100
                hundreds_set, small, small_kind = True, Fraction(0), None
            else:
                if last_scale is not None and scale >= last_scale:
                    raise ValueError("Amount words are out of order.")
                g = group_value()
                total += (g if g else Fraction(1)) * scale
                last_scale, hundreds, hundreds_set, small, small_kind = scale, Fraction(0), False, Fraction(0), None
            consumed_any = True
            continue

        raise ValueError(f"Unsupported amount word: {tok}")

    if frac is not None:
        raise ValueError("Fractional word without a number scale.")
    if not consumed_any:
        raise ValueError("Amount phrase cannot be empty.")

    rest = group_value()
    if last_scale is not None and rest >= last_scale:
        raise ValueError("Amount words are out of order.")
    amount = total + rest

    if amount.denominator != 1:
        raise ValueError("Amount is not a whole number of rupees.")
    amount = int(amount)
    if amount <= 0:
        raise ValueError("Amount must be greater than zero.")
    if amount > MAX_AMOUNT:
        raise ValueError("Amount exceeds the parser's supported limit.")
    return amount


def parse_amount_checked(text: str, alternative_text: str | None = None) -> int:
    """Parse the main amount and compare it with a second interpretation."""
    first = parse_amount(text)
    if alternative_text and alternative_text.strip():
        second = parse_amount(alternative_text)
        if first != second:
            raise ValueError("The two speech interpretations disagree on the amount.")
    return first


# ---- amount in words (for the read-back) -------------------------------------
_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
         "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
         "seventeen", "eighteen", "nineteen"]
_TENW = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def _below_100(n: int) -> str:
    if n < 20:
        return _ONES[n]
    t, u = divmod(n, 10)
    return _TENW[t] + (f"-{_ONES[u]}" if u else "")


def _below_1000(n: int) -> str:
    h, r = divmod(n, 100)
    parts = []
    if h:
        parts.append(f"{_ONES[h]} hundred")
    if r:
        parts.append(_below_100(r))
    return " ".join(parts)


def amount_to_words(n: int) -> str:
    """Indian-system words, e.g. 225000 -> 'two lakh twenty-five thousand'."""
    if n == 0:
        return "zero"
    parts = []
    for scale, name in ((10_000_000, "crore"), (100_000, "lakh"), (1000, "thousand")):
        q, n = divmod(n, scale)
        if q:
            parts.append(f"{_below_100(q) if q < 100 else _below_1000(q)} {name}")
    if n:
        parts.append(_below_1000(n))
    return " ".join(parts)
