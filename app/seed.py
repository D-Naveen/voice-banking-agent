"""Seed / reset the synthetic demo data.

    python -m app.seed            # create anything that is missing
    python -m app.seed --reset    # wipe EVERYTHING and re-seed (balances, ledger, logs, cases)
"""
import hashlib
import os
import secrets
import sys
from decimal import Decimal

from app.database import Base, SessionLocal, engine
from app.models import BankAccount, Payee, User, UserSecurity
from app.services.security import hash_pin

# Mahesh is deliberately permanent "new payee" for the demo: a far-future creation time
# keeps (now - created) < 24h forever, so the new-payee PIN rule can always be shown.
ALWAYS_NEW = 4_102_444_800


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    iterations = int(os.getenv("PBKDF2_ITERATIONS", "600000"))
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), iterations).hex()
    return f"pbkdf2_sha256${iterations}${salt}${digest}"


DEMO_USERS = [("customer1", "demo123", "customer", "2468"),
              ("customer2", "demo123", "customer", "1357"),
              ("agent1", "agent123", "agent", "8642")]
SENDERS = [("1000000001", "customer1", "10000.00"), ("1000000002", "customer2", "8000.00")]
RECIPIENTS = ["2000000001", "2000000002", "2000000003", "2000000004", "2000000005"]
# (name, recipient account, aliases, created_at_epoch)
PAYEES = [("Ramesh Kumar", "2000000001", "रमेश,Ramesh", 0),
          ("Suresh", "2000000002", "सुरेश", 0),
          ("Priya Sharma", "2000000003", "प्रिया शर्मा", 0),
          ("Priya Nair", "2000000004", "प्रिया नायर", 0),
          ("Mahesh", "2000000005", "महेश", ALWAYS_NEW)]


def seed_demo_data(quiet: bool = False):
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        users = {}
        for username, password, role, pin in DEMO_USERS:
            user = db.query(User).filter_by(username=username).first()
            if user is None:
                user = User(username=username, password_hash=hash_password(password), role=role)
                db.add(user)
                db.flush()
            users[username] = user
            if db.query(UserSecurity).filter_by(user_id=user.id).first() is None:
                db.add(UserSecurity(user_id=user.id, pin_hash=hash_pin(pin)))

        for number, username, balance in SENDERS:
            if db.query(BankAccount).filter_by(account_number=number).first() is None:
                db.add(BankAccount(account_number=number, owner_id=users[username].id,
                                   balance=Decimal(balance), daily_limit=Decimal("10000.00")))
        db.flush()
        for number in RECIPIENTS:
            if db.query(BankAccount).filter_by(account_number=number).first() is None:
                db.add(BankAccount(account_number=number, owner_id=users["customer2"].id,
                                   balance=Decimal("1000.00"), daily_limit=Decimal("10000.00")))
        db.flush()

        for name, number, aliases, created in PAYEES:
            recipient = db.query(BankAccount).filter_by(account_number=number).first()
            payee = db.query(Payee).filter_by(owner_id=users["customer1"].id, name=name).first()
            if payee is None:
                db.add(Payee(name=name, account_number=number, owner_id=users["customer1"].id,
                             account_id=recipient.id, aliases=aliases, created_at_epoch=created))
        db.commit()
        if not quiet:
            print("Demo data initialised.")
            print("  customer1 / demo123 (PIN 2468)   customer2 / demo123 (PIN 1357)   agent1 / agent123")
            print("  customer1 contacts:", ", ".join(p[0] for p in PAYEES))
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def reset_demo_data():
    """Drop every table and seed from scratch."""
    Base.metadata.drop_all(bind=engine)
    seed_demo_data()


if __name__ == "__main__":
    if "--reset" in sys.argv:
        reset_demo_data()
        print("Database reset.")
    else:
        seed_demo_data()
