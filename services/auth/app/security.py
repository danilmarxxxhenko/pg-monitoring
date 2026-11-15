"""Password hashing (bcrypt) and JWT issuing/validation."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from .config import settings

# A valid hash used to equalise response time for unknown usernames
_DUMMY_HASH = bcrypt.hashpw(b"dummy-password", bcrypt.gensalt(rounds=settings.bcrypt_rounds))


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=settings.bcrypt_rounds)).decode()


def check_password(password: str, password_hash: str | None) -> bool:
    if password_hash is None:
        bcrypt.checkpw(password.encode(), _DUMMY_HASH)  # constant-ish timing
        return False
    return bcrypt.checkpw(password.encode(), password_hash.encode())


@dataclass(frozen=True)
class Claims:
    username: str
    role: str
    jti: str
    expires_at: datetime


def issue_token(username: str, role: str, ttl_min: int, kind: str) -> tuple[str, Claims]:
    now = datetime.now(timezone.utc)
    claims = Claims(username, role, uuid.uuid4().hex, now + timedelta(minutes=ttl_min))
    token = jwt.encode({
        "sub": username, "role": role, "typ": kind, "jti": claims.jti,
        "iat": int(now.timestamp()), "exp": int(claims.expires_at.timestamp()), "iss": "pgmon-auth",
    }, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, claims


def decode_token(token: str) -> Claims:
    """Raises jwt.InvalidTokenError (incl. ExpiredSignatureError) on failure."""
    data = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm],
                      issuer="pgmon-auth", options={"require": ["sub", "role", "exp", "jti"]})
    return Claims(data["sub"], data["role"], data["jti"],
                  datetime.fromtimestamp(data["exp"], tz=timezone.utc))
