"""Order API settings from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    service_name: str = os.getenv("SERVICE_NAME", "order-api")
    http_port: int = int(os.getenv("HTTP_PORT", "8000"))

    db_host: str = os.getenv("DB_HOST", "postgres")
    db_port: int = int(os.getenv("DB_PORT", "5432"))
    db_name: str = os.getenv("DB_NAME", "shop")
    db_user: str = os.getenv("DB_USER", "app")
    db_password: str = os.getenv("DB_PASSWORD", "")
    db_pool_min: int = int(os.getenv("DB_POOL_MIN", "2"))
    db_pool_max: int = int(os.getenv("DB_POOL_MAX", "20"))

    # Every request is authorised by GET {auth_url}/verify (enabled at stage 4)
    auth_enabled: bool = os.getenv("AUTH_ENABLED", "true").lower() == "true"
    auth_url: str = os.getenv("AUTH_URL", "http://auth:8000")

    # Orders stuck in "created" (e.g. publish failed) are re-published
    republish_after_s: int = int(os.getenv("REPUBLISH_AFTER_S", "60"))
    republish_every_s: int = int(os.getenv("REPUBLISH_EVERY_S", "30"))

    @property
    def conninfo(self) -> str:
        return (f"host={self.db_host} port={self.db_port} dbname={self.db_name} "
                f"user={self.db_user} password={self.db_password} application_name={self.service_name}")


settings = Settings()
