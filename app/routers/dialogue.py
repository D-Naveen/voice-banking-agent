"""Customer-facing dialogue API. The browser only relays what was heard; every decision is server-side."""
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import BankAccount, Payee, Transaction, User
from app.services import dialogue as dlg
from app.services.auth import get_current_customer, get_user_account
from app.services.payment_service import spent_last_24h
from app.services.settings_service import get_setting
from app.services.spoof_detection import MAX_AUDIO_BYTES

router = APIRouter(tags=["Dialogue"])


class TurnRequest(BaseModel):
    session_id: str | None = Field(default=None, max_length=40)
    text: str = Field(default="", max_length=300)
    alternatives: list[str] = Field(default_factory=list, max_length=4)
    confidence: float | None = Field(default=None, ge=0, le=1)
    source: str = Field(default="text", pattern="^(voice|text)$")
    choice_payee_id: int | None = Field(default=None, gt=0)


class ConfirmRequest(BaseModel):
    session_id: str = Field(max_length=40)
    decision: str = Field(pattern="^(confirm|cancel)$")


class ExecuteRequest(BaseModel):
    session_id: str = Field(max_length=40)
    pin: str | None = Field(default=None, max_length=12)


class HandoffRequest(BaseModel):
    session_id: str | None = Field(default=None, max_length=40)
    reason: str = Field(default="", max_length=250)
    text: str = Field(default="", max_length=300)


def _account(db: Session, user: User) -> BankAccount:
    acct = get_user_account(db, user.id)
    if acct is None:
        raise HTTPException(status_code=400, detail="No bank account is linked to this login.")
    return acct


def _run(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except dlg.DialogueError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail)


@router.post("/dialogue/turn")
def turn(req: TurnRequest, db: Session = Depends(get_db), user: User = Depends(get_current_customer)):
    return _run(dlg.handle_turn, db, user, req.session_id, _account(db, user), req.text,
                req.alternatives, req.confidence, req.source, req.choice_payee_id)


@router.post("/dialogue/confirm")
def confirm(req: ConfirmRequest, db: Session = Depends(get_db), user: User = Depends(get_current_customer)):
    return _run(dlg.handle_confirm, db, user, req.session_id, req.decision)


@router.post("/dialogue/liveness")
async def liveness(session_id: str = Form(..., max_length=40), challenge_id: str = Form(..., max_length=80),
                   transcript: str = Form("", max_length=200), typed: bool = Form(False),
                   audio: UploadFile | None = File(default=None),
                   db: Session = Depends(get_db), user: User = Depends(get_current_customer)):
    data = None
    if audio is not None:
        data = await audio.read(MAX_AUDIO_BYTES + 1)
    try:
        # Audio is analysed in memory only; it is never written to disk or to logs.
        return await dlg.handle_liveness(db, user, session_id, challenge_id, transcript, data,
                                         (audio.filename if audio else "") or "challenge.webm", typed)
    except dlg.DialogueError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail)


@router.post("/dialogue/execute")
def execute(req: ExecuteRequest, db: Session = Depends(get_db), user: User = Depends(get_current_customer)):
    return _run(dlg.handle_execute, db, user, req.session_id, req.pin)


@router.post("/dialogue/handoff")
def handoff(req: HandoffRequest, db: Session = Depends(get_db), user: User = Depends(get_current_customer)):
    return _run(dlg.handle_handoff, db, user, req.session_id, _account(db, user), req.reason, req.text)


@router.get("/dialogue/{session_id}")
def state(session_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_customer)):
    return _run(dlg.get_state, db, user, session_id)


@router.get("/account")
def account(db: Session = Depends(get_db), user: User = Depends(get_current_customer)):
    acct = _account(db, user)
    txns = (db.query(Transaction, Payee).join(Payee, Payee.id == Transaction.payee_id)
            .filter(Transaction.sender_account_id == acct.id)
            .order_by(Transaction.id.desc()).limit(5).all())
    payees = db.query(Payee).filter_by(owner_id=user.id).order_by(Payee.id).all()
    return {
        "account_masked": dlg.mask_account(acct.account_number),
        "balance": int(acct.balance), "balance_display": dlg.format_inr(int(acct.balance)),
        "spent_24h": int(spent_last_24h(db, acct.id)),
        "daily_limit": get_setting(db, "daily_limit"),
        "per_payment_limit": get_setting(db, "per_payment_limit"),
        "pin_threshold": get_setting(db, "pin_amount_threshold"),
        "payees": [p.name for p in payees],
        "recent": [{"payee": p.name, "amount": dlg.format_inr(int(t.amount)), "reference": t.reference}
                   for t, p in txns],
    }
