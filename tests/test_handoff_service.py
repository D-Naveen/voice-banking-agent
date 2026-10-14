import json
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import User, HandoffCase, AuditLog
from app.services.handoff_service import create_handoff_case


def test_handoff_case_is_saved_for_agent_review():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    try:
        user = User(username="handoff_user", password_hash="test", role="customer")
        session.add(user)
        session.commit()

        case = create_handoff_case(
            db=session,
            reason="Amount was unclear",
            transcript="Pay Ramesh five hundred",
            actor_user_id=user.id,
            uncertain_field="amount",
            extracted_fields={"payee": "Ramesh"},
        )

        saved = session.query(HandoffCase).filter_by(id=case.id).first()
        assert saved.status == "open"
        assert saved.reason == "Amount was unclear"
        assert json.loads(saved.extracted_fields) == {"payee": "Ramesh"}
        assert session.query(AuditLog).filter_by(event_type="human_handoff_created").count() == 1
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
