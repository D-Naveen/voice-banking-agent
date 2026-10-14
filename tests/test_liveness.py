import time

import pytest
from sqlalchemy import update

from app.models import LivenessChallenge
from app.services.liveness import create_challenge, spoken_to_digits, verify_challenge


@pytest.mark.parametrize("spoken, digits", [
    ("4 7 2", "472"), ("472", "472"), ("four seven two", "472"), ("char saat do", "472"),
    ("४ ७ २", "472"), ("चार सात दो", "472"), ("the code is 4 7 2", "472"),
])
def test_spoken_digit_normalisation(spoken, digits):
    assert spoken_to_digits(spoken) == digits


def test_challenge_is_random_three_digits(db):
    seen = {create_challenge(db, f"s{i}")["digits"] for i in range(30)}
    assert all(len(d) == 3 and d.isdigit() for d in seen) and len(seen) > 5


def test_correct_digits_pass_once_only(db):
    ch = create_challenge(db, "sess-a")
    verify_challenge(db, ch["challenge_id"], "sess-a", ch["digits"])
    with pytest.raises(ValueError, match="already used"):
        verify_challenge(db, ch["challenge_id"], "sess-a", ch["digits"])


def test_wrong_digits_rejected_and_extra_digits_rejected(db):
    ch = create_challenge(db, "sess-b")
    wrong = "".join(str((int(c) + 1) % 10) for c in ch["digits"])
    with pytest.raises(ValueError, match="did not match"):
        verify_challenge(db, ch["challenge_id"], "sess-b", wrong)
    # background speech adding digits must not pass either
    with pytest.raises(ValueError, match="did not match"):
        verify_challenge(db, ch["challenge_id"], "sess-b", ch["digits"] + " 9")


def test_other_session_cannot_use_challenge(db):
    ch = create_challenge(db, "sess-c")
    with pytest.raises(ValueError, match="not found"):
        verify_challenge(db, ch["challenge_id"], "someone-else", ch["digits"])


def test_expired_challenge_rejected(db):
    ch = create_challenge(db, "sess-d")
    db.execute(update(LivenessChallenge).values(expires_at_epoch=int(time.time()) - 1))
    db.commit()
    with pytest.raises(ValueError, match="expired"):
        verify_challenge(db, ch["challenge_id"], "sess-d", ch["digits"])


def test_new_challenge_invalidates_the_old_one(db):
    old = create_challenge(db, "sess-e")
    create_challenge(db, "sess-e")
    with pytest.raises(ValueError, match="already used"):
        verify_challenge(db, old["challenge_id"], "sess-e", old["digits"])
