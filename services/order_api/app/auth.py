"""Request authorisation through the auth service (`GET /verify`)."""
from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx
from fastapi import HTTPException, Request, status

from .config import settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Principal:
    username: str
    role: str


ANONYMOUS = Principal("anonymous", "service")


async def require_user(request: Request) -> Principal:
    """FastAPI dependency: every business endpoint requires a valid Bearer JWT.

    Verification is delegated to the auth service on every request so that it
    shows up as a separate span in the distributed trace.
    """
    if not settings.auth_enabled:
        return ANONYMOUS
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token",
                            headers={"WWW-Authenticate": "Bearer"})
    client: httpx.AsyncClient = request.app.state.http
    try:
        resp = await client.get(f"{settings.auth_url}/verify", headers={"Authorization": header})
    except httpx.HTTPError as exc:
        log.error("auth service unavailable", extra={"error": str(exc)})
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "auth service unavailable") from exc
    if resp.status_code != 200:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token",
                            headers={"WWW-Authenticate": "Bearer"})
    return Principal(resp.headers.get("x-user", ""), resp.headers.get("x-role", ""))
