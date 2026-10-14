"""Editable thresholds. Every change is stored in setting_changes and audit-logged."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Setting, SettingChange
from app.services.audit_service import log_event

# key -> (default, min, max, type, description)
DEFAULTS: dict[str, tuple] = {
    "asr_clarify_below": (0.80, 0.0, 1.0, float, "Speech confidence below this: ask the customer to repeat."),
    "asr_handoff_below": (0.45, 0.0, 1.0, float, "Speech confidence below this: hand off to a human."),
    "max_clarify_attempts": (2, 1, 5, int, "Failed clarifications before an automatic handoff."),
    "pin_amount_threshold": (1000, 1, 100000, int, "Rupees at or above which a PIN is required."),
    "per_payment_limit": (5000, 1, 100000, int, "Hard cap per payment (rupees)."),
    "daily_limit": (10000, 1, 1000000, int, "Hard cap per rolling 24 hours (rupees)."),
    "spoof_pass_below": (0.30, 0.0, 1.0, float, "Spoof score at or below this: allow."),
    "spoof_block_at": (0.70, 0.0, 1.0, float, "Spoof score at or above this: block and hand off."),
    "liveness_ttl_seconds": (60, 15, 300, int, "Lifetime of a liveness challenge."),
    "max_liveness_failures": (3, 1, 10, int, "Wrong liveness answers before handoff."),
}


def _cast(key: str, raw):
    typ = DEFAULTS[key][3]
    return typ(raw)


def get_setting(db: Session, key: str):
    row = db.query(Setting).filter_by(key=key).first()
    if row is None:
        return DEFAULTS[key][0]
    try:
        return _cast(key, row.value)
    except (ValueError, TypeError):
        return DEFAULTS[key][0]


def all_settings(db: Session) -> list[dict]:
    out = []
    for key, (default, lo, hi, typ, desc) in DEFAULTS.items():
        out.append({"key": key, "value": get_setting(db, key), "default": default,
                    "min": lo, "max": hi, "description": desc})
    return out


def update_setting(db: Session, key: str, raw_value, user_id: int, note: str = "") -> dict:
    if key not in DEFAULTS:
        raise ValueError("Unknown setting.")
    _, lo, hi, typ, _desc = DEFAULTS[key]
    try:
        value = typ(raw_value)
    except (ValueError, TypeError):
        raise ValueError("Value must be a number.")
    if not (lo <= value <= hi):
        raise ValueError(f"Value must be between {lo} and {hi}.")

    # Cross-field sanity so the router can never become contradictory.
    cur = {k: get_setting(db, k) for k in DEFAULTS}
    cur[key] = value
    if cur["asr_handoff_below"] > cur["asr_clarify_below"]:
        raise ValueError("asr_handoff_below cannot exceed asr_clarify_below.")
    if cur["spoof_pass_below"] >= cur["spoof_block_at"]:
        raise ValueError("spoof_pass_below must be lower than spoof_block_at.")
    if cur["per_payment_limit"] > cur["daily_limit"]:
        raise ValueError("per_payment_limit cannot exceed daily_limit.")

    row = db.query(Setting).filter_by(key=key).first()
    old = row.value if row else str(DEFAULTS[key][0])
    if row is None:
        row = Setting(key=key, value=str(value), updated_by=user_id)
        db.add(row)
    else:
        row.value, row.updated_by = str(value), user_id
    db.add(SettingChange(key=key, old_value=old, new_value=str(value),
                         changed_by=user_id, note=(note or "")[:250]))
    db.commit()
    log_event(db, "setting_changed", actor_user_id=user_id,
              details={"key": key, "old": old, "new": str(value)})
    return {"key": key, "old": old, "new": value}
