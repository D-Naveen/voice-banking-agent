"""Small helper functions for saving safe audit events."""
import json
from sqlalchemy.orm import Session

from app.models import AuditLog


def log_event(
    db: Session,
    event_type: str,
    actor_user_id: int | None = None,
    payment_intent_id: int | None = None,
    transaction_id: int | None = None,
    details: dict | None = None,
):
    """Save an event. Never pass PINs, passwords, or raw audio here."""
    try:
        event = AuditLog(
            event_type=event_type,
            actor_user_id=actor_user_id,
            payment_intent_id=payment_intent_id,
            transaction_id=transaction_id,
            details=json.dumps(details or {}),
        )
        db.add(event)
        db.commit()
        db.refresh(event)
        return event
    except Exception:
        # Logging should not accidentally leave the database session broken.
        db.rollback()
        return None
