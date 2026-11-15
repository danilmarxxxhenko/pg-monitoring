"""Load generator settings taken from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, default))


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, default))


@dataclass(frozen=True)
class Settings:
    service_name: str = os.getenv("SERVICE_NAME", "load-generator")
    http_port: int = _int("HTTP_PORT", 8080)

    users: int = _int("USERS", 20)
    rps: float = _float("RPS", 10)

    db_host: str = os.getenv("DB_HOST", "postgres")
    db_port: int = _int("DB_PORT", 5432)
    db_name: str = os.getenv("DB_NAME", "shop")
    db_user: str = os.getenv("DB_USER", "generator")
    db_password: str = os.getenv("DB_PASSWORD", "")
    db_pool_max: int = _int("DB_POOL_MAX", 15)

    # Background job periods (seconds) and order lifecycle thresholds (minutes)
    restock_every: float = _float("RESTOCK_EVERY_S", 20)
    reprice_every: float = _float("REPRICE_EVERY_S", 30)
    archive_every: float = _float("ARCHIVE_EVERY_S", 60)
    purge_every: float = _float("PURGE_EVERY_S", 120)
    archive_after_min: int = _int("ARCHIVE_AFTER_MIN", 15)
    purge_after_min: int = _int("PURGE_AFTER_MIN", 1440)
    purge_batch: int = _int("PURGE_BATCH", 200)

    @property
    def conninfo(self) -> str:
        return (
            f"host={self.db_host} port={self.db_port} dbname={self.db_name} "
            f"user={self.db_user} password={self.db_password} "
            f"application_name={self.service_name}"
        )


settings = Settings()
