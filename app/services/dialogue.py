"""Dialogue state machine for a voice payment.

States:  capture -> (clarify)* -> read_back -> liveness -> (step_up) -> ready -> receipt
         any state -> handoff | cancelled | expired

Hard rules enforced here (not in the browser, not by any language model):
  * Only this code moves money, and only from `handle_execute`.
  * Confirmation is a separate step from understanding. Silence, background speech,
    or the agent's own TTS echo never counts as confirmation.
  * Customer text is data: it can only fill {payee, amount}. Instruction-like text is
    logged and ignored.
  * Every uncertain situation ends in clarify or handoff - never a silent guess.
"""
from __future__ import annotations

import json
import time
from decimal import Decimal
from difflib import SequenceMatcher
from uuid import uuid4

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models import (BankAccount, DialogueSession, HandoffCase, LivenessChallenge, Payee,
                        PaymentIntent, User, UserSecurity)
from app.services import amount_parser as ap
from app.services import intent as it
from app.services.audit_service import log_event
from app.services.handoff_service import create_handoff_case
from app.services.liveness import create_challenge, verify_challenge
from app.services.payee_resolver import is_new_payee, resolve_payee
from app.services.payment_service import execute_mock_payment, spent_last_24h
from app.services.security import verify_pin
from app.services.settings_service import get_setting
from app.services.spoof_detection import check_audio

TERMINAL = {"receipt", "handoff", "cancelled", "expired"}
SESSION_IDLE_SECONDS = 600
INTENT_SECONDS = 300
MIN_VOICE_CONFIRM_DELAY = 1.5   # seconds after the read-back before a spoken "confirm" counts


class DialogueError(Exception):
    def __init__(self, status: int, detail: str):
        self.status, self.detail = status, detail
        super().__init__(detail)


# ---------------------------------------------------------------- helpers
def format_inr(n: int) -> str:
    s = str(int(n))
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ",".join(groups + [tail])


def mask_account(number: str) -> str:
    return "XXXXXX" + number[-4:]


def _j(s: str | None, default):
    try:
        return json.loads(s) if s else default
    except (ValueError, TypeError):
        return default


def _now() -> int:
    return int(time.time())


def _get_session(db: Session, user: User, session_id: str) -> DialogueSession:
    sess = db.query(DialogueSession).filter_by(session_id=session_id).first()
    if sess is None or sess.user_id != user.id:
        raise DialogueError(404, "Conversation not found.")
    if sess.state not in TERMINAL and _now() - sess.updated_epoch > SESSION_IDLE_SECONDS:
        _expire(db, sess, "idle_timeout")
    return sess


def _intent(db: Session, sess: DialogueSession) -> PaymentIntent | None:
    if not sess.intent_id:
        return None
    return db.query(PaymentIntent).filter_by(intent_id=sess.intent_id).first()


def _audit(db: Session, sess: DialogueSession, event: str, **details):
    intent = _intent(db, sess)
    log_event(db, event, actor_user_id=sess.user_id,
              payment_intent_id=intent.id if intent else None,
              details={"session": sess.session_id, **details})


def _add_history(sess: DialogueSession, role: str, text: str):
    hist = _j(sess.history, [])
    hist.append({"t": _now(), "role": role, "text": (text or "")[:300]})
    sess.history = json.dumps(hist[-30:])


def _save(db: Session, sess: DialogueSession, state: str | None = None):
    if state:
        sess.state = state
    sess.updated_epoch = _now()
    db.commit()


def _say(db: Session, sess: DialogueSession, message: str, speak: str | None = None,
         extra: dict | None = None) -> dict:
    """Build the response and remember what the agent said (for echo detection)."""
    speak = message if speak is None else speak
    sess.last_prompt, sess.last_prompt_epoch = speak, _now()
    _add_history(sess, "agent", message)
    _save(db, sess)
    return view(db, sess, message, speak, extra)


def view(db: Session, sess: DialogueSession, message: str = "", speak: str = "",
         extra: dict | None = None) -> dict:
    fields = _j(sess.fields, {})
    intent = _intent(db, sess)
    data: dict = {"attempts": sess.attempts}
    if fields.get("candidates"):
        payees = db.query(Payee).filter(Payee.id.in_(fields["candidates"])).all()
        data["candidates"] = [{"id": p.id, "name": p.name} for p in payees]
    if intent is not None and sess.state in ("read_back", "liveness", "step_up", "ready"):
        payee = db.query(Payee).filter_by(id=intent.payee_id).first()
        amount = int(intent.amount)
        data.update({"payee_name": payee.name, "account_masked": mask_account(payee.account_number),
                     "amount": amount, "amount_display": format_inr(amount),
                     "amount_words": ap.amount_to_words(amount),
                     "new_payee": is_new_payee(payee),
                     "expires_in_seconds": max(0, intent.expires_at_epoch - _now())})
    if sess.state in ("step_up", "ready") and intent is not None:
        data["pin_reasons"] = pin_reasons(db, sess, intent)
        data["pin_required"] = bool(data["pin_reasons"])
    if sess.state == "liveness":
        ch = (db.query(LivenessChallenge)
              .filter_by(session_id=sess.session_id, used=0)
              .order_by(LivenessChallenge.id.desc()).first())
        if ch is not None and _now() < ch.expires_at_epoch:
            data["challenge"] = {"id": ch.challenge_id, "digits": " ".join(ch.digits),
                                 "expires_in_seconds": ch.expires_at_epoch - _now()}
    if sess.case_ref:
        data["case_ref"] = sess.case_ref
    data.update(extra or {})
    return {"session_id": sess.session_id, "state": sess.state, "message": message,
            "speak": speak, "data": data}


def _expire(db: Session, sess: DialogueSession, reason: str):
    intent = _intent(db, sess)
    if intent is not None and intent.status == "pending":
        intent.status = "expired"
    sess.state, sess.liveness_ok = "expired", 0
    _audit(db, sess, "session_expired", reason=reason)
    _save(db, sess)


def _cancel(db: Session, sess: DialogueSession, why: str, message: str) -> dict:
    intent = _intent(db, sess)
    if intent is not None and intent.status == "pending":
        intent.status = "cancelled"
    sess.state, sess.liveness_ok = "cancelled", 0
    _audit(db, sess, "payment_cancelled", reason=why)
    return _say(db, sess, message)


def _handoff(db: Session, sess: DialogueSession, reason: str, uncertain_field: str,
             extra_fields: dict | None = None, message: str | None = None) -> dict:
    """Route to a human with the full context. Never moves money."""
    fields = _j(sess.fields, {})
    intent = _intent(db, sess)
    hist = _j(sess.history, [])
    customer_lines = [h["text"] for h in hist if h["role"] == "customer"]
    extracted = {
        "payee_id": fields.get("payee_id"), "payee_name": fields.get("payee_name"),
        "amount": fields.get("amount"), "amount_text": fields.get("amount_text"),
        "cross_check": fields.get("cross_check"), "asr_confidence": fields.get("asr_confidence"),
        "alternatives": fields.get("alternatives"), "input_source": fields.get("source"),
        "state_when_escalated": sess.state, "attempts": sess.attempts,
        "recent_turns": hist[-8:],
    }
    extracted.update(extra_fields or {})
    case = create_handoff_case(
        db, reason=reason, transcript="\n".join(customer_lines[-6:]),
        actor_user_id=sess.user_id, payment_intent_id=intent.id if intent else None,
        uncertain_field=uncertain_field, extracted_fields=extracted)
    if intent is not None and intent.status == "pending":
        intent.status = "held"
    sess.case_ref, sess.state, sess.liveness_ok = case.case_ref, "handoff", 0
    msg = message or "I'm passing this to a person who can help. Nothing has been sent."
    return _say(db, sess, f"{msg} Reference: {case.case_ref}.", speak=msg)


def pin_reasons(db: Session, sess: DialogueSession, intent: PaymentIntent) -> list[str]:
    """Why a PIN is required. Recomputed on the server at execute time."""
    reasons = []
    payee = db.query(Payee).filter_by(id=intent.payee_id).first()
    if int(intent.amount) >= get_setting(db, "pin_amount_threshold"):
        reasons.append("amount_over_threshold")
    if payee is not None and is_new_payee(payee):
        reasons.append("new_payee")
    if intent.spoof_status == "step_up":
        reasons.append("audio_check_uncertain")
    elif intent.spoof_status != "pass":
        reasons.append("audio_not_verified")
    if sess.liveness_mode == "typed":
        reasons.append("typed_liveness")
    if intent.input_source == "voice" and intent.cross_check != "agree":
        reasons.append("single_source_voice")
    return reasons


# ---------------------------------------------------------------- start / state
def start_session(db: Session, user: User, account: BankAccount) -> DialogueSession:
    sess = DialogueSession(session_id=uuid4().hex, user_id=user.id, account_id=account.id,
                           state="capture", fields="{}", history="[]",
                           created_epoch=_now(), updated_epoch=_now())
    db.add(sess)
    db.commit()
    _audit(db, sess, "session_started")
    return sess


def get_state(db: Session, user: User, session_id: str) -> dict:
    sess = _get_session(db, user, session_id)
    extra = {}
    if sess.case_ref:
        case = db.query(HandoffCase).filter_by(case_ref=sess.case_ref).first()
        if case:
            extra["case"] = {"status": case.status, "resolution": case.resolution,
                             "message": case.agent_note if case.resolution == "ask_again" else None}
    return view(db, sess, sess.last_prompt, "", extra)


# ---------------------------------------------------------------- interpretation
def _interpret(db: Session, user: User, text: str) -> dict:
    """One independent interpretation of a transcript."""
    info = it.extract_intent(text)
    res = {"intent": info, "payee": None, "amount": None, "amount_error": None}
    if info["payee_text"]:
        res["payee"] = resolve_payee(db, user.id, info["payee_text"])
    if info["amount_text"]:
        try:
            res["amount"] = ap.parse_amount(info["amount_text"])
        except ValueError as exc:
            res["amount_error"] = str(exc)
    return res


def _disagreement(primary: dict, alt: dict) -> str | None:
    if primary["amount"] and alt["amount"] and primary["amount"] != alt["amount"]:
        return "amount"
    pp, ap_ = primary["payee"], alt["payee"]
    if pp and ap_ and pp.kind == "exact" and ap_.kind == "exact" and pp.payee.id != ap_.payee.id:
        return "payee"
    return None


def _cross_check(db, user, primary, alternatives: list[str], source: str):
    """Compare the deterministic parse with the speech engine's alternative transcripts."""
    if source != "voice":
        return "n/a", None, []
    alts_info, agreed = [], False
    for alt_text in alternatives:
        alt = _interpret(db, user, alt_text)
        alts_info.append({"text": alt_text[:200], "amount": alt["amount"],
                          "payee": alt["payee"].payee.name if alt["payee"] and alt["payee"].payee else None})
        field = _disagreement(primary, alt)
        if field:
            return "disagree", field, alts_info
        if alt["amount"] and alt["amount"] == primary["amount"]:
            agreed = True
    return ("agree" if agreed else "unavailable"), None, alts_info


# ---------------------------------------------------------------- turn
def _clarify(db, sess, message, field: str, reason: str, count: bool = True, extra=None) -> dict:
    """Ask again. Failed clarifications are counted; too many -> human."""
    if count:
        sess.attempts += 1
    _audit(db, sess, "clarify", field=field, reason=reason, attempts=sess.attempts)
    if count and sess.attempts >= get_setting(db, "max_clarify_attempts"):
        return _handoff(db, sess, f"Could not confirm the {field} after {sess.attempts} attempts ({reason}).",
                        field, message="I'm having trouble understanding. I'll pass this to a person.")
    sess.state = "clarify"
    return _say(db, sess, message, extra=extra)


def handle_turn(db: Session, user: User, session_id: str | None, account: BankAccount,
                text: str, alternatives: list[str] | None = None, confidence: float | None = None,
                source: str = "text", choice_payee_id: int | None = None) -> dict:
    if session_id:
        sess = _get_session(db, user, session_id)
    else:
        sess = start_session(db, user, account)
    text = (text or "").strip()[:300]
    source = "voice" if source == "voice" else "text"
    alternatives = [a.strip()[:300] for a in (alternatives or []) if a and a.strip() and a.strip() != text][:3]
    if confidence is not None and not (0 < confidence <= 1):
        confidence = None   # 0 means "engine gave no confidence"

    if sess.state in TERMINAL:
        raise DialogueError(409, "This conversation has ended. Start a new payment.")

    if source == "voice" and text and _is_echo(sess, text):
        # The microphone picked up the agent's own voice: never treat that as the customer.
        _audit(db, sess, "echo_ignored", state=sess.state)
        _save(db, sess)
        return view(db, sess, sess.last_prompt, "", {"echo_ignored": True})

    _add_history(sess, "customer", text if text else "(silence)")
    _audit(db, sess, "turn_received", source=source, confidence=confidence,
           chars=len(text), state=sess.state)

    # ---- states in which speech must never advance the payment ---------------------
    if sess.state in ("liveness", "step_up", "ready"):
        info = it.extract_intent(text)
        if info["action"] == "cancel":
            return _cancel(db, sess, "customer_cancel", "Cancelled. Nothing was sent.")
        return _say(db, sess, "Please finish the current step on screen, or say cancel.")

    if sess.state == "read_back":
        return _turn_in_read_back(db, user, sess, account, text, alternatives, confidence, source)

    # ---- capture / clarify ----------------------------------------------------------
    return _turn_capture(db, user, sess, account, text, alternatives, confidence, source, choice_payee_id)


def _turn_in_read_back(db, user, sess, account, text, alternatives, confidence, source):
    intent = _intent(db, sess)
    info = it.extract_intent(text)

    if info["action"] == "silence":
        sess.attempts += 1
        _audit(db, sess, "silence_in_read_back", attempts=sess.attempts)
        if sess.attempts >= get_setting(db, "max_clarify_attempts"):
            return _cancel(db, sess, "no_confirmation", "I didn't hear a confirmation, so I cancelled. Nothing was sent.")
        return _say(db, sess, "I didn't hear anything. Silence is not a confirmation. Tap Confirm or Cancel.")
    if info["action"] == "cancel":
        return _cancel(db, sess, "customer_cancel", "Cancelled. Nothing was sent.")
    if info["injection"]:
        _audit(db, sess, "prompt_injection_ignored")
        return _say(db, sess, "I can only confirm or cancel the payment shown. Tap Confirm or Cancel.")

    norm = " ".join(ap.normalize_text(text).split())
    if norm in it.CONFIRM_PHRASES:
        ok, why = _voice_confirm_allowed(db, sess, text, confidence, source)
        if ok:
            _audit(db, sess, "voice_confirm_accepted")
            return _start_liveness(db, sess)
        _audit(db, sess, "voice_confirm_rejected", reason=why)
        return _say(db, sess, "I couldn't treat that as a confirmation. Please tap the Confirm button.")

    # A new, complete payment request replaces the pending one (a correction).
    if info["action"] == "pay" and info["amount_text"] and info["payee_text"]:
        if intent is not None and intent.status == "pending":
            intent.status = "cancelled"
        sess.intent_id, sess.state, sess.attempts = None, "capture", 0
        sess.fields = "{}"
        _audit(db, sess, "request_replaced")
        return _turn_capture(db, user, sess, account, text, alternatives, confidence, source, None)

    sess.attempts += 1
    if sess.attempts >= get_setting(db, "max_clarify_attempts") + 1:
        return _cancel(db, sess, "no_confirmation", "I couldn't get a clear confirmation, so I cancelled. Nothing was sent.")
    return _say(db, sess, "To continue, tap Confirm (or say 'confirm'). To stop, tap Cancel.")


def _is_echo(sess: DialogueSession, text: str) -> bool:
    """True when the recognised text is (mostly) the agent's own last prompt coming back in."""
    prompt = (sess.last_prompt or "").casefold()
    heard = text.casefold()
    if not prompt or len(heard.split()) < 4:
        return False
    if SequenceMatcher(None, heard, prompt).ratio() > 0.6:
        return True
    words = [w.strip(".,:!?'\"") for w in heard.split()]
    prompt_words = {w.strip(".,:!?'\"") for w in prompt.split()}
    return sum(w in prompt_words for w in words) / len(words) >= 0.8


def _voice_confirm_allowed(db, sess, text, confidence, source) -> tuple[bool, str]:
    if source != "voice":
        return False, "not_voice"
    if confidence is None or confidence < get_setting(db, "asr_clarify_below"):
        return False, "low_or_unknown_confidence"
    if time.time() - sess.last_prompt_epoch < MIN_VOICE_CONFIRM_DELAY:
        return False, "too_soon_after_prompt"
    # Echo: the agent hearing its own read-back is never confirmation.
    if sess.last_prompt and SequenceMatcher(None, text.casefold(), sess.last_prompt.casefold()).ratio() > 0.6:
        return False, "echo_of_agent_voice"
    return True, "ok"


def _turn_capture(db, user, sess, account, text, alternatives, confidence, source, choice_payee_id):
    fields = _j(sess.fields, {})
    fields.update({"source": source, "asr_confidence": confidence})

    # Choosing one of the offered contacts (button tap).
    if choice_payee_id is not None:
        if choice_payee_id not in (fields.get("candidates") or []):
            raise DialogueError(422, "That contact was not one of the options offered.")
        payee = db.query(Payee).filter_by(id=choice_payee_id, owner_id=user.id).first()
        if payee is None:
            raise DialogueError(404, "Contact not found.")
        fields.update({"payee_id": payee.id, "payee_name": payee.name, "candidates": []})
        sess.fields = json.dumps(fields)
        _audit(db, sess, "payee_chosen", payee=payee.name)
        return _advance(db, user, sess, account, fields)

    if not text:
        return _clarify(db, sess, "I didn't hear anything. Please tap the microphone and speak, or type your request.",
                        "request", "silence")

    # Confidence router (editable thresholds) -------------------------------------
    if source == "voice" and confidence is not None:
        if confidence < get_setting(db, "asr_handoff_below"):
            sess.fields = json.dumps(fields)
            _audit(db, sess, "confidence_handoff", confidence=confidence)
            return _handoff(db, sess, f"Speech confidence {confidence:.2f} is below the hand-off threshold.",
                            "transcript", message="I couldn't hear you clearly. I'll pass this to a person.")
        if confidence < get_setting(db, "asr_clarify_below"):
            sess.fields = json.dumps(fields)
            return _clarify(db, sess, "I'm not sure I heard that correctly. Please say it again or type it.",
                            "transcript", f"low_confidence_{confidence:.2f}")

    primary = _interpret(db, user, text)
    info = primary["intent"]

    if info["injection"]:
        _audit(db, sess, "prompt_injection_ignored", snippet=text[:80])
        sess.fields = json.dumps(fields)
        return _clarify(db, sess, "That isn't a payment request, so I ignored it. "
                        "Try: 'Ramesh ko paanch sau rupaye bhej do'.", "request", "instruction_like_text")
    if info["action"] == "cancel":
        return _cancel(db, sess, "customer_cancel", "Cancelled. Nothing was sent.")

    # "Yes / haan" to accept a single suggested contact.
    if (fields.get("candidates") and len(fields["candidates"]) == 1
            and ap.normalize_text(text) in {"yes", "haan", "ha", "han", "ji haan", "हाँ", "हां"}):
        return handle_turn(db, user, sess.session_id, account, "", choice_payee_id=fields["candidates"][0])

    # Cross-check with the second method ------------------------------------------
    cc, cc_field, alts = _cross_check(db, user, primary, alternatives, source)
    fields["cross_check"], fields["alternatives"] = cc, alts
    fields["transcript"] = text
    if cc == "disagree":
        sess.fields = json.dumps(fields)
        _audit(db, sess, "cross_check_failed", field=cc_field)
        return _clarify(db, sess, f"I heard two different versions of the {cc_field}, so I won't guess. "
                        "Please say it again slowly, or type it.", cc_field, "cross_check_disagree")

    # Fill slots ---------------------------------------------------------------------
    if info["error"] == "multiple_amounts":
        sess.fields = json.dumps(fields)
        return _clarify(db, sess, "I heard more than one amount. Please ask for one payment at a time.",
                        "amount", "multiple_amounts")

    if info["amount_text"]:
        if primary["amount"] is None:
            sess.fields = json.dumps(fields)
            return _clarify(db, sess, "I couldn't understand the amount. Please say it again, for example "
                            "'paanch sau' or 'dedh hazaar', or type digits.", "amount",
                            primary["amount_error"] or "amount_not_understood",
                            extra={"hint": "amount"})
        fields.update({"amount": primary["amount"], "amount_text": info["amount_text"]})

    res = primary["payee"]
    if info["payee_text"]:
        if res is None or res.kind == "none":
            sess.fields = json.dumps(fields)
            names = ", ".join(p.name for p in db.query(Payee).filter_by(owner_id=user.id).all())
            return _clarify(db, sess, f"I couldn't find '{info['payee_text']}' in your contacts. "
                            f"Your saved contacts are: {names}.", "payee", "payee_not_found")
        if res.kind == "exact":
            fields.update({"payee_id": res.payee.id, "payee_name": res.payee.name, "candidates": []})
        else:   # fuzzy or ambiguous: the customer must choose
            fields.update({"payee_id": None, "payee_name": None,
                           "candidates": [p.id for p in res.candidates]})
    sess.fields = json.dumps(fields)
    return _advance(db, user, sess, account, fields)


def _advance(db, user, sess, account, fields) -> dict:
    """Move forward once slots are filled, or ask for what is still missing."""
    if fields.get("candidates"):
        names = [p.name for p in db.query(Payee).filter(Payee.id.in_(fields["candidates"])).all()]
        msg = (f"Did you mean {names[0]}? Tap the name to choose." if len(names) == 1
               else f"Which contact do you mean: {' or '.join(names)}? Tap a name.")
        return _clarify(db, sess, msg, "payee", "ambiguous_payee", count=False)
    if not fields.get("payee_id"):
        return _clarify(db, sess, "Who would you like to pay?", "payee", "missing_payee", count=False)
    if not fields.get("amount"):
        return _clarify(db, sess, f"How much would you like to send to {fields['payee_name']}?",
                        "amount", "missing_amount", count=False)

    amount = Decimal(int(fields["amount"]))
    payee = db.query(Payee).filter_by(id=fields["payee_id"], owner_id=user.id).first()
    if payee is None:
        raise DialogueError(404, "Contact not found.")
    sender = db.query(BankAccount).filter_by(id=sess.account_id).first()
    per_payment = get_setting(db, "per_payment_limit")
    if amount > per_payment:
        fields.pop("amount", None)
        sess.fields = json.dumps(fields)
        return _clarify(db, sess, f"₹{format_inr(int(amount))} is above the ₹{format_inr(per_payment)} per-payment limit. "
                        "Please ask for a smaller amount.", "amount", "per_payment_limit", count=False)
    if spent_last_24h(db, sender.id) + amount > get_setting(db, "daily_limit"):
        return _cancel(db, sess, "daily_limit", "That would go over your daily limit, so I can't prepare it.")
    if sender.balance < amount:
        return _cancel(db, sess, "insufficient_balance", "Your balance is too low for that payment.")

    intent = PaymentIntent(intent_id=uuid4().hex, sender_account_id=sender.id, payee_id=payee.id,
                           amount=amount, status="pending", spoof_status="not_checked",
                           cross_check=fields.get("cross_check", "n/a"),
                           input_source=fields.get("source", "text"),
                           expires_at_epoch=_now() + INTENT_SECONDS)
    db.add(intent)
    db.commit()
    sess.intent_id, sess.state, sess.attempts = intent.intent_id, "read_back", 0
    _audit(db, sess, "read_back_issued", amount=str(amount), payee=payee.name,
           cross_check=intent.cross_check)
    words = ap.amount_to_words(int(amount))
    speak = (f"Please check. Amount: {int(amount)} rupees. In words: {words} rupees. "
             f"Payee: {payee.name}. Tap Confirm to continue, or Cancel.")
    return _say(db, sess, f"Please check: ₹{format_inr(int(amount))} ({words} rupees) to {payee.name}.", speak=speak)


# ---------------------------------------------------------------- confirm
def _check_intent_alive(db, sess) -> PaymentIntent:
    intent = _intent(db, sess)
    if intent is None or intent.status != "pending":
        raise DialogueError(409, "There is no pending payment in this conversation.")
    if _now() >= intent.expires_at_epoch:
        _expire(db, sess, "intent_expired")
        raise DialogueError(410, "This payment request expired. Please start again.")
    return intent


def _start_liveness(db, sess) -> dict:
    ttl = get_setting(db, "liveness_ttl_seconds")
    ch = create_challenge(db, sess.session_id, ttl)
    sess.state, sess.liveness_ok = "liveness", 0
    _audit(db, sess, "liveness_challenge_created", ttl=ttl)
    # The digits are shown, never spoken: a TTS echo or old recording can't answer them.
    return _say(db, sess, f"Confirmed. Now prove it's you: say the digits {ch['spaced']}.",
                speak="Confirmed. Now say the digits shown on screen.",
                extra={"challenge": {"id": ch["challenge_id"], "digits": ch["spaced"],
                                     "expires_in_seconds": ttl}})


def handle_confirm(db: Session, user: User, session_id: str, decision: str) -> dict:
    sess = _get_session(db, user, session_id)
    if sess.state != "read_back":
        raise DialogueError(409, f"Cannot confirm while the conversation is in '{sess.state}'.")
    _check_intent_alive(db, sess)
    if decision == "cancel":
        return _cancel(db, sess, "customer_cancel", "Cancelled. Nothing was sent.")
    _audit(db, sess, "button_confirm")
    return _start_liveness(db, sess)


# ---------------------------------------------------------------- liveness + spoof
async def handle_liveness(db: Session, user: User, session_id: str, challenge_id: str,
                          transcript: str, audio: bytes | None, filename: str, typed: bool) -> dict:
    sess = _get_session(db, user, session_id)
    if sess.state != "liveness":
        raise DialogueError(409, f"Liveness is not expected in state '{sess.state}'.")
    intent = _check_intent_alive(db, sess)
    _add_history(sess, "customer", f"(liveness answer) {transcript[:40]}")

    try:
        verify_challenge(db, challenge_id, sess.session_id, transcript)
    except ValueError as exc:
        sess.liveness_failures += 1
        _audit(db, sess, "liveness_failed", reason=str(exc), failures=sess.liveness_failures)
        if sess.liveness_failures >= get_setting(db, "max_liveness_failures"):
            return _handoff(db, sess, "Repeated liveness challenge failures (possible replay or spoof).",
                            "voice_liveness", {"liveness_failures": sess.liveness_failures},
                            message="The voice check failed too many times. I'll pass this to a person.")
        extra = {"liveness_error": str(exc)}
        if "expired" in str(exc) or "already used" in str(exc):
            ttl = get_setting(db, "liveness_ttl_seconds")
            ch = create_challenge(db, sess.session_id, ttl)
            extra["challenge"] = {"id": ch["challenge_id"], "digits": ch["spaced"], "expires_in_seconds": ttl}
        return _say(db, sess, f"{exc} Try again.", speak="That didn't match. Please try again.", extra=extra)

    _audit(db, sess, "liveness_passed", mode="typed" if typed else "voice")
    sess.liveness_mode = "typed" if typed else "voice"

    # Anti-spoof layer: our code maps the detector score to allow / step-up / block.
    if typed:
        spoof = {"status": "step_up", "score": None, "provider": "none",
                 "message": "Digits were typed, so there is no voice to analyse; PIN step-up is required."}
    else:
        spoof = await check_audio(audio, filename or "challenge.webm",
                                  get_setting(db, "spoof_pass_below"), get_setting(db, "spoof_block_at"))
    intent.spoof_status = spoof["status"]
    intent.spoof_score = spoof["score"]
    _audit(db, sess, "audio_spoof_check", status=spoof["status"], score=spoof["score"],
           provider=spoof["provider"], error=spoof.get("error"))

    if spoof["status"] == "blocked":
        return _handoff(db, sess, "Anti-spoof detector flagged possible synthetic or replayed audio.",
                        "voice_authenticity", {"spoof_score": spoof["score"], "provider": spoof["provider"]},
                        message="The voice check raised a risk, so I've stopped this payment and sent it to a person.")

    sess.liveness_ok = 1
    sess.state = "step_up" if pin_reasons(db, sess, intent) else "ready"
    needs_pin = sess.state == "step_up"
    msg = ("Voice check done. For your safety, enter your PIN to finish." if needs_pin
           else "Voice check done. Tap Send payment to finish.")
    return _say(db, sess, msg, speak=msg, extra={"spoof": {"status": spoof["status"], "message": spoof["message"]}})


# ---------------------------------------------------------------- PIN + execute
def handle_execute(db: Session, user: User, session_id: str, pin: str | None) -> dict:
    sess = _get_session(db, user, session_id)
    if sess.state not in ("step_up", "ready") or not sess.liveness_ok:
        raise DialogueError(409, "Complete the confirmation and voice check first.")
    intent = _check_intent_alive(db, sess)
    if intent.spoof_status == "blocked":
        raise DialogueError(403, "This payment is blocked pending human review.")

    reasons = pin_reasons(db, sess, intent)
    if reasons:
        security = db.query(UserSecurity).filter_by(user_id=user.id).first()
        if security is None:
            raise DialogueError(403, "PIN verification is not configured.")
        now = _now()
        if now < security.locked_until_epoch:
            _audit(db, sess, "pin_locked")
            raise DialogueError(429, "PIN entry is temporarily locked. Please try again later.")
        if not pin:
            raise DialogueError(401, "Please enter your PIN.")
        if not verify_pin(pin, security.pin_hash):
            security.failed_attempts += 1
            locked = security.failed_attempts >= 3
            if locked:
                security.failed_attempts, security.locked_until_epoch = 0, now + 300
            db.commit()
            _audit(db, sess, "pin_lockout" if locked else "pin_failed")
            if locked:
                _handoff(db, sess, "PIN locked after 3 incorrect attempts during a payment.", "pin")
                raise DialogueError(429, "Too many incorrect PIN attempts. This payment was sent to a person.")
            raise DialogueError(401, "Incorrect PIN.")
        security.failed_attempts, security.locked_until_epoch = 0, 0
        db.commit()
        _audit(db, sess, "pin_passed", reasons=reasons)

    # Atomically claim the intent so a double-tap or replay can never pay twice.
    claimed = db.execute(update(PaymentIntent)
                         .where(PaymentIntent.id == intent.id, PaymentIntent.status == "pending")
                         .values(status="processing")).rowcount
    db.commit()
    if claimed != 1:
        raise DialogueError(409, "This payment was already processed.")
    sess.liveness_ok = 0   # single use

    try:
        txn = execute_mock_payment(db, intent.sender_account_id, intent.payee_id, intent.amount,
                                   per_payment_limit=Decimal(get_setting(db, "per_payment_limit")),
                                   daily_limit=Decimal(get_setting(db, "daily_limit")))
    except ValueError as exc:
        intent.status = "failed"
        sess.state = "cancelled"
        db.commit()
        _audit(db, sess, "payment_failed", reason=str(exc))
        return _say(db, sess, f"The payment could not be completed: {exc}", extra={"failed": True})

    intent.status = "completed"
    sess.state = "receipt"
    db.commit()
    payee = db.query(Payee).filter_by(id=intent.payee_id).first()
    sender = db.query(BankAccount).filter_by(id=intent.sender_account_id).first()
    log_event(db, "payment_completed", actor_user_id=user.id, payment_intent_id=intent.id,
              transaction_id=txn.id, details={"session": sess.session_id, "amount": str(intent.amount),
                                              "pin_reasons": reasons})
    words = ap.amount_to_words(int(intent.amount))
    receipt = {"reference": txn.reference, "amount": int(intent.amount),
               "amount_display": format_inr(int(intent.amount)), "payee_name": payee.name,
               "balance_after": format_inr(int(sender.balance))}
    return _say(db, sess, f"Done. ₹{format_inr(int(intent.amount))} sent to {payee.name}.",
                speak=f"Done. {words} rupees sent to {payee.name}.", extra={"receipt": receipt})


# ---------------------------------------------------------------- human on request
def handle_handoff(db: Session, user: User, session_id: str | None, account: BankAccount,
                   reason: str, text: str) -> dict:
    sess = _get_session(db, user, session_id) if session_id else start_session(db, user, account)
    if sess.state == "handoff" and sess.case_ref:
        return view(db, sess, f"You're already in the queue. Reference: {sess.case_ref}.")
    if text:
        _add_history(sess, "customer", text)
    return _handoff(db, sess, reason or "Customer asked to talk to a person.", "customer_request")
