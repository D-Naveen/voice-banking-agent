import json
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import User, AuditLog
from app.services.audit_service import log_event


def test_audit_event_is_saved_without_secrets():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    try:
        user = User(username="audit_user", password_hash="test", role="customer")
        session.add(user)
        session.commit()

        event = log_event(
            session,
            "payment_prepared",
            actor_user_id=user.id,
            details={"amount": "500", "status": "pending"},
        )

        saved = session.query(AuditLog).filter_by(id=event.id).first()
        assert saved.event_type == "payment_prepared"
        assert json.loads(saved.details) == {"amount": "500", "status": "pending"}
        assert "pin" not in saved.details.lower()
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
