from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import User
from app.seed import hash_password
from app.models import AuthSession
from app.services.auth import _hash, drop_session, login_user


def test_demo_login_checks_password_hash():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    try:
        user = User(
            username="login_customer",
            password_hash=hash_password("demo-pass"),
            role="customer",
        )
        session.add(user)
        session.commit()

        result = login_user(session, "login_customer", "demo-pass")
        assert result is not None
        logged_in_user, token = result
        assert logged_in_user.id == user.id
        # Only a hash of the cookie token is stored, and it is persisted in the DB.
        assert session.query(AuthSession).filter_by(token_hash=_hash(token)).count() == 1
        assert session.query(AuthSession).filter_by(token_hash=token).count() == 0

        assert login_user(session, "login_customer", "wrong-pass") is None
        drop_session(session, token)
        assert session.query(AuthSession).count() == 0
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
