"""Helpers for creating human-review cases."""
import json
from uuid import uuid4
from sqlalchemy.orm import Session

from app.models import HandoffCase
from app.services.audit_service import log_event


def create_handoff_case(
    db: Session,
    reason: str,
    transcript: str = "",
    actor_user_id: int | None = None,
    payment_intent_id: int | None = None,
    uncertain_field: str | None = None,
    extracted_fields: dict | None = None,
):
    """Create a case for an agent; this does not execute any payment."""
    case = HandoffCase(
        case_ref=f"CASE-{uuid4().hex[:12].upper()}",
        actor_user_id=actor_user_id,
        payment_intent_id=payment_intent_id,
        transcript=transcript[:2000],
        reason=reason[:250],
        uncertain_field=uncertain_field,
        extracted_fields=json.dumps(extracted_fields or {}),
        status="open",
    )
    db.add(case)
    db.commit()
    db.refresh(case)

    log_event(
        db,
        "human_handoff_created",
        actor_user_id=actor_user_id,
        payment_intent_id=payment_intent_id,
        details={"case_ref": case.case_ref, "reason": case.reason},
    )
    return case
