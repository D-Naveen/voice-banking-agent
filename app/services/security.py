
import hashlib
import hmac
import os
import secrets


# Production default 600k; tests lower it via PBKDF2_ITERATIONS to stay fast.
ITERATIONS = int(os.getenv("PBKDF2_ITERATIONS", "600000"))


def hash_pin(pin: str) -> str:
    if not pin.isdigit() or not 4 <= len(pin) <= 12:
        raise ValueError("PIN must contain 4 to 12 digits.")

    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        pin.encode("utf-8"),
        salt.encode("utf-8"),
        ITERATIONS,
    ).hex()

    return f"pbkdf2_sha256${ITERATIONS}${salt}${digest}"


def verify_pin(pin: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations, salt, expected = stored_hash.split("$")

        if algorithm != "pbkdf2_sha256":
            return False

        if not pin.isdigit() or not 4 <= len(pin) <= 12:
            return False

        actual = hashlib.pbkdf2_hmac(
            "sha256",
            pin.encode("utf-8"),
            salt.encode("utf-8"),
            int(iterations),
        ).hex()

        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def verify_password(password: str, stored_hash: str) -> bool:
    """Check a demo user's password against its PBKDF2 hash."""
    try:
        algorithm, iterations, salt, expected = stored_hash.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            int(iterations),
        ).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False
