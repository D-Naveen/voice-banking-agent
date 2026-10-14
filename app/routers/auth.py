"""Login and logout routes for demo customers and agents."""
import os
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.services.auth import (COOKIE_NAME, drop_session, get_current_user, get_user_account,
                               login_locked, login_user)
from app.services.audit_service import log_event

router = APIRouter(prefix="/auth", tags=["Authentication"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=100)
    voice_consent: bool = False


def user_response(db: Session, user: User):
    account = get_user_account(db, user.id)
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "account_id": account.id if account else None,
    }


@router.post("/login")
def login(request: LoginRequest, response: Response, db: Session = Depends(get_db)):
    if login_locked(request.username):
        log_event(db, "login_locked", details={"username": request.username.strip()[:50]})
        raise HTTPException(status_code=429, detail="Too many failed attempts. Try again in a few minutes.")
    result = login_user(db, request.username, request.password)
    if result is None:
        log_event(db, "login_failed", details={"username": request.username.strip()})
        raise HTTPException(status_code=401, detail="Incorrect username or password.")

    user, token = result
    if user.role == "customer" and not request.voice_consent:
        drop_session(db, token)
        raise HTTPException(status_code=422, detail="Please accept the voice-processing notice to continue.")
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=os.getenv("COOKIE_SECURE", "false").lower() == "true",
        samesite="lax",
        max_age=8 * 60 * 60,
    )
    log_event(db, "login_succeeded", actor_user_id=user.id, details={"role": user.role})
    if user.role == "customer":
        log_event(db, "voice_consent_given", actor_user_id=user.id, details={"notice_version": "1"})
    return user_response(db, user)


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    uid = drop_session(db, request.cookies.get(COOKIE_NAME))
    if uid:
        log_event(db, "logout", actor_user_id=uid)
    response.delete_cookie(COOKIE_NAME, httponly=True, samesite="lax")
    return {"status": "logged_out"}


@router.get("/me")
def who_am_i(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return user_response(db, user)
