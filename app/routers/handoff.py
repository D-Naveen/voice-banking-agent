"""Customer escalation endpoints and the simple agent review page."""
import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import HandoffCase, User, AuditLog, SettingChange
from app.services.auth import get_current_user, get_current_agent
from app.services.audit_service import log_event
from app.services import settings_service as ss

router = APIRouter(tags=["Human handoff"])
FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"


class SettingUpdate(BaseModel):
    key: str = Field(max_length=60)
    value: float
    note: str = Field(min_length=3, max_length=250)


class AgentActionRequest(BaseModel):
    action: str = Field(pattern="^(approve|reject|ask_again)$")
    note: str = Field(min_length=3, max_length=1000)




@router.get("/agent/cases")
def list_agent_cases(
    status: str = "open",
    db: Session = Depends(get_db),
    current_agent: User = Depends(get_current_agent),
):
    query = db.query(HandoffCase)
    if status != "all":
        query = query.filter_by(status=status)

    cases = query.order_by(HandoffCase.created_at.desc()).limit(100).all()
    users = {u.id: u.username for u in db.query(User).all()}
    result = []
    for case in cases:
        result.append({
            "customer": users.get(case.actor_user_id),
            "id": case.id,
            "case_ref": case.case_ref,
            "actor_user_id": case.actor_user_id,
            "payment_intent_id": case.payment_intent_id,
            "transcript": case.transcript,
            "reason": case.reason,
            "uncertain_field": case.uncertain_field,
            "extracted_fields": json.loads(case.extracted_fields or "{}"),
            "status": case.status,
            "agent_note": case.agent_note,
            "resolution": case.resolution,
            "created_at": case.created_at.isoformat() if case.created_at else None,
        })
    return {"cases": result}


@router.post("/agent/cases/{case_id}/action")
def update_agent_case(
    case_id: int,
    request: AgentActionRequest,
    db: Session = Depends(get_db),
    current_agent: User = Depends(get_current_agent),
):
    case = db.query(HandoffCase).filter_by(id=case_id).first()
    if case is None:
        raise HTTPException(status_code=404, detail="Handoff case not found.")
    if case.status == "resolved":
        raise HTTPException(status_code=409, detail="This case is already resolved.")

    case.handled_by_user_id = current_agent.id
    case.agent_note = request.note.strip()
    case.resolution = request.action

    if request.action == "ask_again":
        case.status = "waiting_customer"
    else:
        case.status = "resolved"

    db.commit()
    db.refresh(case)
    log_event(
        db,
        "agent_case_updated",
        actor_user_id=case.handled_by_user_id,
        payment_intent_id=case.payment_intent_id,
        details={"case_ref": case.case_ref, "action": request.action},
    )
    return {
        "status": case.status,
        "case_ref": case.case_ref,
        "resolution": case.resolution,
        "message": "Case updated. This action does not transfer money.",
    }


@router.get("/agent/audit-logs")
def list_audit_logs(
    limit: int = 50,
    db: Session = Depends(get_db),
    current_agent: User = Depends(get_current_agent),
):
    """Show recent audit events to the signed-in demo agent."""
    limit = max(1, min(limit, 100))
    events = db.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit).all()
    result = []
    for event in events:
        result.append({
            "id": event.id,
            "event_type": event.event_type,
            "actor_user_id": event.actor_user_id,
            "payment_intent_id": event.payment_intent_id,
            "transaction_id": event.transaction_id,
            "details": json.loads(event.details or "{}"),
            "created_at": event.created_at.isoformat() if event.created_at else None,
        })
    return {"events": result}


@router.get("/agent/settings")
def get_settings(db: Session = Depends(get_db), current_agent: User = Depends(get_current_agent)):
    changes = db.query(SettingChange).order_by(SettingChange.id.desc()).limit(15).all()
    return {"settings": ss.all_settings(db),
            "changes": [{"key": c.key, "old": c.old_value, "new": c.new_value, "note": c.note,
                         "at": c.created_at.isoformat() if c.created_at else None} for c in changes]}


@router.post("/agent/settings")
def change_setting(request: SettingUpdate, db: Session = Depends(get_db),
                   current_agent: User = Depends(get_current_agent)):
    try:
        return ss.update_setting(db, request.key, request.value, current_agent.id, request.note)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
