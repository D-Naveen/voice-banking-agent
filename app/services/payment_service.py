
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import (
    BankAccount,
    Payee,
    Transaction,
    LedgerEntry,
)


PER_PAYMENT_LIMIT = Decimal("5000.00")   # default; the live value comes from settings
DAILY_LIMIT = Decimal("10000.00")        # default; the live value comes from settings


def spent_last_24h(db: Session, account_id: int) -> Decimal:
    """Completed outgoing payments in the last rolling 24 hours."""
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=24)
    total = (
        db.query(func.coalesce(func.sum(Transaction.amount), 0))
        .filter(
            Transaction.sender_account_id == account_id,
            Transaction.status == "completed",
            Transaction.created_at >= since,
        )
        .scalar()
    )
    return Decimal(str(total))


def execute_mock_payment(
    db: Session,
    sender_account_id: int,
    payee_id: int,
    amount: Decimal,
    per_payment_limit: Decimal = PER_PAYMENT_LIMIT,
    daily_limit: Decimal = DAILY_LIMIT,
) -> Transaction:
    """Execute a mock transfer with matching debit and credit entries."""

    # Validate amount before touching the database.
    try:
        amount = Decimal(str(amount))
        if not amount.is_finite():
            raise ValueError("Amount must be finite.")
        if amount <= 0:
            raise ValueError("Amount must be positive.")
        if amount != amount.quantize(Decimal("0.01")):
            raise ValueError("Amount cannot have fractions of a paisa.")
    except (InvalidOperation, TypeError):
        raise ValueError("Invalid payment amount.")

    if amount > Decimal(str(per_payment_limit)):
        raise ValueError("Payment exceeds the per-payment limit.")

    try:
        # Start a database transaction.
        sender = (
            db.query(BankAccount)
            .filter_by(id=sender_account_id)
            .with_for_update()
            .first()
        )

        payee = (
            db.query(Payee)
            .filter_by(id=payee_id)
            .first()
        )

        if sender is None:
            raise ValueError("Sender account not found.")

        if payee is None:
            raise ValueError("Payee not found.")

        recipient = (
            db.query(BankAccount)
            .filter_by(id=payee.account_id)
            .first()
        )

        if recipient is None:
            raise ValueError("Payee has no linked bank account.")

        if sender.owner_id != payee.owner_id:
            raise ValueError("Payee does not belong to this customer.")

        if sender.id == recipient.id:
            raise ValueError("Cannot transfer to the same account.")

        if sender.balance < amount:
            raise ValueError("Insufficient balance.")

        if spent_last_24h(db, sender.id) + amount > Decimal(str(daily_limit)):
            raise ValueError("Payment would exceed the daily limit.")

        # Create transaction as pending until both entries exist.
        transaction = Transaction(
            reference=f"MOCK-{uuid4().hex.upper()}",
            sender_account_id=sender.id,
            payee_id=payee.id,
            amount=amount,
            status="pending",
        )
        db.add(transaction)
        db.flush()

        # Update both account balances.
        sender.balance -= amount
        recipient.balance += amount

        # Record matching debit and credit entries.
        db.add_all(
            [
                LedgerEntry(
                    transaction_id=transaction.id,
                    account_id=sender.id,
                    entry_type="debit",
                    amount=amount,
                ),
                LedgerEntry(
                    transaction_id=transaction.id,
                    account_id=recipient.id,
                    entry_type="credit",
                    amount=amount,
                ),
            ]
        )

        # Mark completed only after both entries have been inserted.
        transaction.status = "completed"

        db.commit()
        db.refresh(transaction)

        return transaction

    except Exception:
        db.rollback()
        raise
