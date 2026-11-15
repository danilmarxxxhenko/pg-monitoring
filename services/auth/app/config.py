"""Auth service settings from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _require(name: str) -> str:
    value = os.getenv(name, "")
    if not value:
        raise RuntimeError(f"environment variable {name} is required")
    return value


@dataclass(frozen=True)
class Settings:
    service_name: str = os.getenv("SERVICE_NAME", "auth")
    http_port: int = int(os.getenv("HTTP_PORT", "8000"))
    db_path: str = os.getenv("AUTH_DB_PATH", "/data/auth.sqlite3")

    jwt_secret: str = field(default_factory=lambda: _require("JWT_SECRET"))
    jwt_algorithm: str = "HS256"
    session_ttl_min: int = int(os.getenv("SESSION_TTL_MIN", "480"))   # browser sessions
    token_ttl_min: int = int(os.getenv("TOKEN_TTL_MIN", "60"))         # service tokens
    cookie_name: str = os.getenv("SESSION_COOKIE", "pgmon_session")
    cookie_secure: bool = os.getenv("COOKIE_SECURE", "false").lower() == "true"
    bcrypt_rounds: int = int(os.getenv("BCRYPT_ROUNDS", "10"))

    # Throttling of password guessing: per (client IP, username)
    max_failures: int = int(os.getenv("LOGIN_MAX_FAILURES", "10"))
    failure_window_min: int = int(os.getenv("LOGIN_FAILURE_WINDOW_MIN", "5"))

    # Initial accounts, created at start-up if missing
    bootstrap_users: tuple[tuple[str, str, str], ...] = field(default_factory=lambda: tuple(
        (os.getenv(f"AUTH_{key}_USER", ""), os.getenv(f"AUTH_{key}_PASSWORD", ""), role)
        for key, role in (("ADMIN", "admin"), ("VIEWER", "viewer"), ("SERVICE", "service"))
    ))


settings = Settings()
