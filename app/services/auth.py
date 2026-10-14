"""Cookie sessions stored in the database (hashed) + simple login throttling."""
import hashlib
import secrets
import threading
import time

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AuthSession, BankAccount, User
from app.services.security import verify_password

SESSION_SECONDS = 8 * 60 * 60
COOKIE_NAME = "voicebank_session"
MAX_LOGIN_FAILURES = 5
LOGIN_LOCK_SECONDS = 300
_FAILS: dict[str, list[float]] = {}
_LOCK = threading.Lock()


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def login_locked(username: str) -> bool:
    now = time.time()
    with _LOCK:
        recent = [t for t in _FAILS.get(username.lower(), []) if now - t < LOGIN_LOCK_SECONDS]
        _FAILS[username.lower()] = recent
        return len(recent) >= MAX_LOGIN_FAILURES


def clear_login_failures() -> None:
    with _LOCK:
        _FAILS.clear()


def login_user(db: Session, username: str, password: str):
    username = username.strip()
    user = db.query(User).filter_by(username=username).first()
    if user is None or not verify_password(password, user.password_hash):
        with _LOCK:
            _FAILS.setdefault(username.lower(), []).append(time.time())
        return None
    with _LOCK:
        _FAILS.pop(username.lower(), None)
    token = secrets.token_urlsafe(32)
    db.add(AuthSession(token_hash=_hash(token), user_id=user.id,
                       expires_at_epoch=int(time.time()) + SESSION_SECONDS))
    db.commit()
    return user, token


def drop_session(db: Session, token: str | None):
    if not token:
        return None
    row = db.query(AuthSession).filter_by(token_hash=_hash(token)).first()
    if row:
        uid = row.user_id
        db.delete(row)
        db.commit()
        return uid
    return None


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(COOKIE_NAME)
    row = db.query(AuthSession).filter_by(token_hash=_hash(token)).first() if token else None
    if row is None or time.time() >= row.expires_at_epoch:
        if row:
            db.delete(row)
            db.commit()
        raise HTTPException(status_code=401, detail="Please log in to continue.")
    user = db.query(User).filter_by(id=row.user_id).first()
    if user is None:
        raise HTTPException(status_code=401, detail="User session is no longer valid.")
    return user


def get_current_customer(user: User = Depends(get_current_user)) -> User:
    if user.role != "customer":
        raise HTTPException(status_code=403, detail="Customer access is required.")
    return user


def get_current_agent(user: User = Depends(get_current_user)) -> User:
    if user.role != "agent":
        raise HTTPException(status_code=403, detail="Human-agent access is required.")
    return user


def get_user_account(db: Session, user_id: int):
    return db.query(BankAccount).filter_by(owner_id=user_id).order_by(BankAccount.id).first()
