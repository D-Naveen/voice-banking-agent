"""Deterministic payee matching against the customer's saved contacts.

Exact / first-name matches resolve. A near-miss ("Raamesh") is only a SUGGESTION
that the customer must pick; several candidates mean ambiguity -> ask. Never guess.
"""
from __future__ import annotations

import time
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from sqlalchemy.orm import Session

from app.models import Payee

FUZZY_MIN = 0.80
NEW_PAYEE_SECONDS = 24 * 3600


@dataclass
class Resolution:
    kind: str                      # exact | fuzzy | ambiguous | none
    payee: Payee | None = None
    candidates: list[Payee] = field(default_factory=list)


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").casefold().strip()
    return " ".join("".join(c if (c.isalnum() or unicodedata.category(c).startswith("M")) else " " for c in s).split())


def _forms(p: Payee) -> list[str]:
    forms = [_norm(p.name)] + [_norm(a) for a in (p.aliases or "").split(",") if a.strip()]
    return [f for f in forms if f]


def is_new_payee(p: Payee, now: int | None = None) -> bool:
    now = now or int(time.time())
    return bool(p.created_at_epoch) and (now - p.created_at_epoch) < NEW_PAYEE_SECONDS


def resolve_payee(db: Session, owner_id: int, text: str) -> Resolution:
    query = _norm(text)
    payees = db.query(Payee).filter_by(owner_id=owner_id).all()
    if not query or not payees:
        return Resolution("none")

    # 1. Whole-name / alias match.
    full = [p for p in payees if query in _forms(p)]
    if len(full) == 1:
        return Resolution("exact", full[0])
    if len(full) > 1:
        return Resolution("ambiguous", candidates=full)

    # 2. Every query word is a word of the contact's name ("Ramesh" -> "Ramesh Kumar").
    q_words = query.split()
    by_word = [p for p in payees
               if any(all(w in f.split() for w in q_words) for f in _forms(p))]
    if len(by_word) == 1:
        return Resolution("exact", by_word[0])
    if len(by_word) > 1:
        return Resolution("ambiguous", candidates=by_word)

    # 3. Fuzzy near-misses are suggestions only.
    scored = []
    for p in payees:
        best = 0.0
        for f in _forms(p):
            for target in [f] + f.split():
                for qw in [query] + q_words:
                    best = max(best, SequenceMatcher(None, qw, target).ratio())
        if best >= FUZZY_MIN:
            scored.append((best, p))
    scored.sort(key=lambda x: -x[0])
    if not scored:
        return Resolution("none")
    if len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.08:
        return Resolution("fuzzy", scored[0][1], [scored[0][1]])
    return Resolution("ambiguous", candidates=[p for _, p in scored])
