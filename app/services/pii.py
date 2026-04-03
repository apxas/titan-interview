import hashlib

from app.core.config import settings


def hash_email(email: str) -> str:
    """SHA-256 hash of lowercased, stripped email. Stable for deduplication."""
    normalized = email.strip().lower()
    return hashlib.sha256(normalized.encode()).hexdigest()


def hash_patron(name: str, email: str) -> str:
    """SHA-256 hash of name + email salted with SECRET_KEY. Never stores raw PII."""
    salt = settings.SECRET_KEY
    payload = f"{salt}:{name.strip()}:{email.strip().lower()}"
    return hashlib.sha256(payload.encode()).hexdigest()
