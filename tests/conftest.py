"""Isolated test environment: a throw-away SQLite file, never the dev database."""
import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="voicebank-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/test.db"
os.environ["PBKDF2_ITERATIONS"] = "1000"
os.environ["AUTO_SEED"] = "false"
for var in ("SPOOF_DETECTOR_URL", "SPOOF_PROVIDER", "SPOOF_HF_MODEL"):
    os.environ.pop(var, None)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def seeded():
    from app.seed import reset_demo_data
    from app.services.auth import clear_login_failures
    reset_demo_data()
    clear_login_failures()


@pytest.fixture
def db(seeded):
    from app.database import SessionLocal
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(seeded):
    from app.main import app
    with TestClient(app) as c:
        yield c


def login(client, username="customer1", password="demo123", consent=True):
    r = client.post("/auth/login", json={"username": username, "password": password,
                                         "voice_consent": consent})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def cust(client):
    login(client)
    return client


@pytest.fixture
def agent_client(seeded):
    from app.main import app
    with TestClient(app) as c:
        login(c, "agent1", "agent123", consent=False)
        yield c


class Flow:
    """Tiny helper that drives the dialogue API like the browser does."""

    def __init__(self, client):
        self.c, self.sid, self.last = client, None, None

    def turn(self, text, **kw):
        body = {"text": text, "session_id": self.sid, **kw}
        r = self.c.post("/dialogue/turn", json=body)
        assert r.status_code == 200, r.text
        self.last = r.json()
        self.sid = self.last["session_id"]
        return self.last

    def confirm(self, decision="confirm"):
        r = self.c.post("/dialogue/confirm", json={"session_id": self.sid, "decision": decision})
        assert r.status_code == 200, r.text
        self.last = r.json()
        return self.last

    def live(self, digits=None, audio=b"", typed=False, expect=200):
        d = self.last["data"]["challenge"]
        files = {"audio": ("challenge.webm", audio, "audio/webm")} if audio else None
        r = self.c.post("/dialogue/liveness", data={
            "session_id": self.sid, "challenge_id": d["id"],
            "transcript": digits if digits is not None else d["digits"], "typed": str(typed).lower()},
            files=files)
        assert r.status_code == expect, r.text
        if r.status_code == 200:
            self.last = r.json()
        return r

    def execute(self, pin=None, expect=200):
        r = self.c.post("/dialogue/execute", json={"session_id": self.sid, "pin": pin})
        assert r.status_code == expect, r.text
        if r.status_code == 200:
            self.last = r.json()
        return r

    @property
    def state(self):
        return self.last["state"]
