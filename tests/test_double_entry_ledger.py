
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import User, BankAccount, Payee, Transaction, LedgerEntry
from app.services.payment_service import execute_mock_payment


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)
    session = TestSession()

    customer = User(
        username="test_customer",
        password_hash="test_hash",
        role="customer",
    )
    recipient_owner = User(
        username="test_recipient",
        password_hash="test_hash",
        role="customer",
    )

    session.add_all([customer, recipient_owner])
    session.flush()

    sender = BankAccount(
        account_number="TEST100",
        owner_id=customer.id,
        balance=Decimal("1000.00"),
        daily_limit=Decimal("5000.00"),
    )
    recipient = BankAccount(
        account_number="TEST200",
        owner_id=recipient_owner.id,
        balance=Decimal("100.00"),
        daily_limit=Decimal("5000.00"),
    )

    session.add_all([sender, recipient])
    session.flush()

    payee = Payee(
        name="Test Recipient",
        account_number=recipient.account_number,
        owner_id=customer.id,
        account_id=recipient.id,
    )
    session.add(payee)
    session.commit()

    yield session, sender.id, payee.id, recipient.id

    session.close()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def test_successful_transfer_creates_balanced_entries(db):
    session, sender_id, payee_id, recipient_id = db

    transaction = execute_mock_payment(
        session,
        sender_id,
        payee_id,
        Decimal("200.00"),
    )

    sender = session.get(BankAccount, sender_id)
    recipient = session.get(BankAccount, recipient_id)

    assert sender.balance == Decimal("800.00")
    assert recipient.balance == Decimal("300.00")
    assert transaction.status == "completed"

    entries = (
        session.query(LedgerEntry)
        .filter_by(transaction_id=transaction.id)
        .all()
    )

    assert len(entries) == 2

    debit = next(e for e in entries if e.entry_type == "debit")
    credit = next(e for e in entries if e.entry_type == "credit")

    assert debit.amount == credit.amount == Decimal("200.00")
    assert debit.account_id == sender_id
    assert credit.account_id == recipient_id


def test_insufficient_balance_changes_nothing(db):
    session, sender_id, payee_id, recipient_id = db

    with pytest.raises(ValueError, match="Insufficient balance"):
        execute_mock_payment(
            session,
            sender_id,
            payee_id,
            Decimal("2000.00"),
        )

    assert session.get(BankAccount, sender_id).balance == Decimal("1000.00")
    assert session.get(BankAccount, recipient_id).balance == Decimal("100.00")
    assert session.query(Transaction).count() == 0
    assert session.query(LedgerEntry).count() == 0


def test_excessive_amount_is_rejected(db):
    session, sender_id, payee_id, recipient_id = db

    with pytest.raises(ValueError, match="per-payment limit"):
        execute_mock_payment(
            session,
            sender_id,
            payee_id,
            Decimal("6000.00"),
        )

    assert session.get(BankAccount, sender_id).balance == Decimal("1000.00")
    assert session.query(Transaction).count() == 0
    assert session.query(LedgerEntry).count() == 0
