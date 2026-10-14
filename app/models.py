

from sqlalchemy import (
    Column,
    Integer,
    String,
    ForeignKey,
    Numeric,
    CheckConstraint,
    DateTime,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), nullable=False, default="customer")

    accounts = relationship(
        "BankAccount",
        back_populates="owner",
        cascade="all, delete-orphan",
    )

    payees = relationship(
        "Payee",
        back_populates="owner",
        cascade="all, delete-orphan",
    )


class BankAccount(Base):
    __tablename__ = "bank_accounts"

    id = Column(Integer, primary_key=True, index=True)
    account_number = Column(String(20), unique=True, nullable=False)

    owner_id = Column(
        Integer,
        ForeignKey("users.id"),
        nullable=False,
    )

    balance = Column(Numeric(12, 2), nullable=False, default=0)
    daily_limit = Column(Numeric(12, 2), nullable=False, default=5000)

    __table_args__ = (
        CheckConstraint("balance >= 0", name="check_balance_nonnegative"),
        CheckConstraint(
            "daily_limit >= 0",
            name="check_daily_limit_nonnegative",
        ),
    )

    owner = relationship("User", back_populates="accounts")


class Payee(Base):
    __tablename__ = "payees"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    account_number = Column(String(20), nullable=False)

    owner_id = Column(
        Integer,
        ForeignKey("users.id"),
        nullable=False,
    )

    owner = relationship("User", back_populates="payees")

    
    account_id = Column(
        Integer,
        ForeignKey("bank_accounts.id"),
        nullable=True,
    )

    account = relationship("BankAccount")

    # Extra spoken forms (e.g. Devanagari spelling) used by the fuzzy matcher.
    aliases = Column(Text, nullable=False, default="")
    # Payees added in the last 24h are "new": a PIN is always required.
    created_at_epoch = Column(Integer, nullable=False, default=0)





class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    reference = Column(String(40), unique=True, nullable=False)

    sender_account_id = Column(
        Integer,
        ForeignKey("bank_accounts.id"),
        nullable=False,
    )

    payee_id = Column(
        Integer,
        ForeignKey("payees.id"),
        nullable=False,
    )

    amount = Column(Numeric(12, 2), nullable=False)
    status = Column(String(20), nullable=False, default="pending")
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint("amount > 0", name="check_transaction_amount_positive"),
    )


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"

    id = Column(Integer, primary_key=True, index=True)

    transaction_id = Column(
        Integer,
        ForeignKey("transactions.id"),
        nullable=False,
    )

    account_id = Column(
        Integer,
        ForeignKey("bank_accounts.id"),
        nullable=False,
    )

    entry_type = Column(String(10), nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint("amount > 0", name="check_ledger_amount_positive"),
        CheckConstraint(
            "entry_type IN ('debit', 'credit')",
            name="check_ledger_entry_type",
        ),
        UniqueConstraint(
            "transaction_id",
            "account_id",
            name="unique_transaction_account_entry",
        ),
    )



class PaymentIntent(Base):
    __tablename__ = "payment_intents"

    id = Column(Integer, primary_key=True, index=True)
    intent_id = Column(String(40), unique=True, nullable=False, index=True)
    sender_account_id = Column(
        Integer,
        ForeignKey("bank_accounts.id"),
        nullable=False,
    )
    payee_id = Column(
        Integer,
        ForeignKey("payees.id"),
        nullable=False,
    )
    amount = Column(Numeric(12, 2), nullable=False)
    status = Column(String(20), nullable=False, default="pending")
    spoof_status = Column(String(20), nullable=False, default="not_checked")
    spoof_score = Column(Numeric(5, 4), nullable=True)
    cross_check = Column(String(20), nullable=False, default="n/a")
    input_source = Column(String(10), nullable=False, default="text")
    expires_at_epoch = Column(Integer, nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint("amount > 0", name="check_intent_amount_positive"),
    )



class UserSecurity(Base):
    __tablename__ = "user_security"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer,
        ForeignKey("users.id"),
        unique=True,
        nullable=False,
    )
    pin_hash = Column(String(255), nullable=False)
    failed_attempts = Column(Integer, nullable=False, default=0)
    locked_until_epoch = Column(Integer, nullable=False, default=0)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    event_type = Column(String(100), nullable=False, index=True)
    actor_user_id = Column(
        Integer,
        ForeignKey("users.id"),
        nullable=True
    )
    payment_intent_id = Column(
        Integer,
        ForeignKey("payment_intents.id"),
        nullable=True
    )
    transaction_id = Column(
        Integer,
        ForeignKey("transactions.id"),
        nullable=True
    )
    details = Column(Text, nullable=True)
    created_at = Column(
        DateTime,
        server_default=func.now(),
        nullable=False
    )


class HandoffCase(Base):
    """A case that needs review by a human agent."""

    __tablename__ = "handoff_cases"

    id = Column(Integer, primary_key=True, index=True)
    case_ref = Column(String(40), unique=True, nullable=False, index=True)
    actor_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    payment_intent_id = Column(
        Integer, ForeignKey("payment_intents.id"), nullable=True
    )
    transcript = Column(Text, nullable=False, default="")
    reason = Column(String(250), nullable=False)
    uncertain_field = Column(String(100), nullable=True)
    extracted_fields = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default="open")
    agent_note = Column(Text, nullable=True)
    resolution = Column(String(30), nullable=True)
    handled_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), nullable=False)



class Setting(Base):
    """Editable thresholds (confidence router, limits, spoof score bands)."""

    __tablename__ = "settings"

    key = Column(String(60), primary_key=True)
    value = Column(String(40), nullable=False)
    updated_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), nullable=False)


class SettingChange(Base):
    """Append-only history of threshold edits."""

    __tablename__ = "setting_changes"

    id = Column(Integer, primary_key=True)
    key = Column(String(60), nullable=False)
    old_value = Column(String(40), nullable=True)
    new_value = Column(String(40), nullable=False)
    changed_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    note = Column(String(250), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)


class DialogueSession(Base):
    """One conversation turn-state per customer attempt (the state machine)."""

    __tablename__ = "dialogue_sessions"

    id = Column(Integer, primary_key=True)
    session_id = Column(String(40), unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    account_id = Column(Integer, ForeignKey("bank_accounts.id"), nullable=False)
    state = Column(String(20), nullable=False, default="capture")
    intent_id = Column(String(40), nullable=True)
    fields = Column(Text, nullable=False, default="{}")      # JSON slots
    history = Column(Text, nullable=False, default="[]")     # JSON list of turns
    attempts = Column(Integer, nullable=False, default=0)    # clarify / silence count
    liveness_failures = Column(Integer, nullable=False, default=0)
    liveness_ok = Column(Integer, nullable=False, default=0)
    liveness_mode = Column(String(10), nullable=True)        # voice | typed
    case_ref = Column(String(40), nullable=True)
    last_prompt = Column(Text, nullable=False, default="")
    last_prompt_epoch = Column(Integer, nullable=False, default=0)
    created_epoch = Column(Integer, nullable=False, default=0)
    updated_epoch = Column(Integer, nullable=False, default=0)


class LivenessChallenge(Base):
    """Random single-use, time-limited digit challenge."""

    __tablename__ = "liveness_challenges"

    id = Column(Integer, primary_key=True)
    challenge_id = Column(String(60), unique=True, nullable=False, index=True)
    session_id = Column(String(40), nullable=False, index=True)
    digits = Column(String(10), nullable=False)
    expires_at_epoch = Column(Integer, nullable=False)
    used = Column(Integer, nullable=False, default=0)


class AuthSession(Base):
    """Login sessions survive restarts; only a SHA-256 of the cookie is stored."""

    __tablename__ = "auth_sessions"

    id = Column(Integer, primary_key=True)
    token_hash = Column(String(64), unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    expires_at_epoch = Column(Integer, nullable=False)
