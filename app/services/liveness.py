"""Random, single-use, time-limited digit challenge ("Please say: 4 7 2").

Stored in the database so it survives restarts and works with several workers.
The digits are displayed only (never read out by TTS) so the agent's own voice
cannot answer the challenge, and an old recording carries old digits.
"""
from __future__ import annotations

import secrets
import time
import unicodedata

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models import LivenessChallenge

DIGIT_WORDS = {
    "zero": "0", "oh": "0", "shunya": "0", "one": "1", "ek": "1", "two": "2", "do": "2",
    "three": "3", "teen": "3", "four": "4", "char": "4", "chaar": "4", "five": "5",
    "paanch": "5", "panch": "5", "six": "6", "chhe": "6", "chhah": "6", "che": "6",
    "seven": "7", "saat": "7", "eight": "8", "aath": "8", "nine": "9", "nau": "9",
    "शून्य": "0", "एक": "1", "दो": "2", "तीन": "3", "चार": "4", "पांच": "5", "पाँच": "5",
    "छह": "6", "छः": "6", "सात": "7", "आठ": "8", "नौ": "9",
}
CHALLENGE_LENGTH = 3


def spoken_to_digits(transcript: str) -> str:
    """Map '4 7 2', '472', 'four seven two', 'char saat do', '४ ७ २' to '472'."""
    text = unicodedata.normalize("NFKC", transcript or "").casefold()
    text = "".join(str(unicodedata.digit(c)) if c.isdigit() else c for c in text)
    text = "".join(" " if unicodedata.category(c)[0] in "PSZC" else c for c in text)
    out = []
    for tok in text.split():
        if tok.isdigit():
            out.append(tok)
        elif tok in DIGIT_WORDS:
            out.append(DIGIT_WORDS[tok])
    return "".join(out)


def create_challenge(db: Session, session_id: str, ttl_seconds: int = 60) -> dict:
    # Only the newest challenge for a session stays valid.
    db.execute(update(LivenessChallenge)
               .where(LivenessChallenge.session_id == session_id, LivenessChallenge.used == 0)
               .values(used=1))
    digits = "".join(str(secrets.randbelow(10)) for _ in range(CHALLENGE_LENGTH))
    ch = LivenessChallenge(challenge_id=secrets.token_urlsafe(24), session_id=session_id,
                           digits=digits, expires_at_epoch=int(time.time()) + ttl_seconds, used=0)
    db.add(ch)
    db.commit()
    return {"challenge_id": ch.challenge_id, "digits": digits,
            "spaced": " ".join(digits), "expires_in_seconds": ttl_seconds}


def verify_challenge(db: Session, challenge_id: str, session_id: str, transcript: str) -> None:
    """Raise ValueError unless the transcript is the exact, fresh, unused digits.

    A wrong answer does NOT burn the challenge (so a mishearing can be retried) but
    the caller counts failures and hands off after too many. A correct answer burns it
    atomically, so the same challenge can never pass twice.
    """
    now = int(time.time())
    ch = db.query(LivenessChallenge).filter_by(challenge_id=challenge_id).first()
    if ch is None or ch.session_id != session_id:
        raise ValueError("Liveness challenge not found for this payment.")
    if ch.used:
        raise ValueError("This liveness challenge was already used. Request a new one.")
    if now >= ch.expires_at_epoch:
        db.execute(update(LivenessChallenge).where(LivenessChallenge.id == ch.id).values(used=1))
        db.commit()
        raise ValueError("Liveness challenge expired. Request a new one.")
    if spoken_to_digits(transcript) != ch.digits:
        raise ValueError("The spoken digits did not match.")
    result = db.execute(update(LivenessChallenge)
                        .where(LivenessChallenge.id == ch.id, LivenessChallenge.used == 0)
                        .values(used=1))
    db.commit()
    if result.rowcount != 1:
        raise ValueError("This liveness challenge was already used. Request a new one.")
