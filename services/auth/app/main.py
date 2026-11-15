"""Authorization server (FastAPI + SQLite).

* GET/POST /login   HTML form; sets an HttpOnly cookie with a JWT, redirects to `next`
* POST /api/token   JSON login for services, returns a JWT
* GET  /verify      for nginx `auth_request` and order-api; cookie or Bearer token;
                    200 + X-User / X-Role, 401 (no/invalid session) or 403 (role)
* GET|POST /logout  revokes the token and clears the cookie
* /api/users        user CRUD, admin role only
"""
from __future__ import annotations

import logging
import sqlite3
from contextlib import asynccontextmanager
from typing import Annotated, Literal
from urllib.parse import unquote

import jwt
import uvicorn
from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from opentelemetry.instrumentation.utils import suppress_instrumentation
from prometheus_client import Counter, Gauge
from pydantic import BaseModel, Field

from common.logging import setup_logging
from common.metrics import HttpMetricsMiddleware, metrics_response
from common.telemetry import setup_tracing

from . import db
from .config import settings
from .login_page import render_login
from .security import Claims, check_password, decode_token, hash_password, issue_token

log = setup_logging(settings.service_name)

LOGINS = Counter("auth_login_total", "Login attempts", ["method", "result"])
VERIFY = Counter("auth_verify_total", "Session/token verifications", ["scope", "result"])
DENIED = Counter("auth_denied_total", "Requests denied by the auth service", ["reason"])
LOGOUTS = Counter("auth_logout_total", "Logouts")
USERS = Gauge("auth_users", "Active users by role", ["role"])


def _refresh_user_gauge(conn: sqlite3.Connection) -> None:
    counts = db.count_by_role(conn)
    for role in db.ROLES:
        USERS.labels(role).set(counts.get(role, 0))


def _bootstrap() -> None:
    db.init_db()
    with db.connect() as conn:
        for username, password, role in settings.bootstrap_users:
            if username and password and db.get_user(conn, username) is None:
                db.create_user(conn, username, hash_password(password), role)
                log.info("bootstrap user created", extra={"username": username, "role": role})
        _refresh_user_gauge(conn)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _bootstrap()
    yield


app = FastAPI(title="auth", version="1.0.0", lifespan=lifespan)
app.add_middleware(HttpMetricsMiddleware)
setup_tracing(settings.service_name, fastapi_app=app, sqlite3=True)


# ------------------------------------------------------------- helpers
def client_ip(request: Request) -> str:
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def safe_next(value: str | None) -> str:
    """Only same-site relative paths are allowed (no open redirect)."""
    if not value or not value.startswith("/") or value.startswith("//") or value.startswith("/\\"):
        return "/"
    return value


def extract_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return request.cookies.get(settings.cookie_name)


def authenticate(username: str, password: str, ip: str, method: str) -> tuple[sqlite3.Row | None, str]:
    """Returns (user, result) where result is success | bad_credentials | inactive | throttled."""
    with db.connect() as conn:
        if db.recent_failures(conn, username, ip, settings.failure_window_min) >= settings.max_failures:
            result, user = "throttled", None
        else:
            user = db.get_user(conn, username)
            if not check_password(password, user["password_hash"] if user else None):
                result, user = "bad_credentials", None
            elif not user["is_active"]:
                result, user = "inactive", None
            else:
                result = "success"
        db.record_attempt(conn, username, ip, method, result == "success",
                          None if result == "success" else result)
    LOGINS.labels(method, result).inc()
    extra = {"event": "login_success" if user else "login_failed", "username": username,
             "client_ip": ip, "method": method, "result": result}
    if user:
        log.info("login succeeded", extra=extra)
    else:
        DENIED.labels(result).inc()
        log.warning("login failed: %s", result, extra=extra)
    return user, result


def validate(request: Request) -> tuple[Claims | None, str]:
    """Returns (claims, result) where result is ok | missing | expired | invalid | revoked | inactive."""
    token = extract_token(request)
    if not token:
        return None, "missing"
    try:
        claims = decode_token(token)
    except jwt.ExpiredSignatureError:
        return None, "expired"
    except jwt.InvalidTokenError:
        return None, "invalid"
    with db.connect() as conn:
        if db.is_revoked(conn, claims.jti):
            return None, "revoked"
        user = db.get_user(conn, claims.username)
    if user is None or not user["is_active"]:
        return None, "inactive"
    # Role is taken from the DB, so role changes apply to existing sessions
    return Claims(claims.username, user["role"], claims.jti, claims.expires_at), "ok"


def require_admin(request: Request) -> Claims:
    claims, result = validate(request)
    if claims is None:
        DENIED.labels(result).inc()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication required",
                            headers={"WWW-Authenticate": "Bearer"})
    if claims.role != "admin":
        DENIED.labels("forbidden").inc()
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin role required")
    return claims


Admin = Annotated[Claims, Depends(require_admin)]


# ------------------------------------------------------------- service
@app.get("/health", include_in_schema=False)
def health():
    # Healthchecks run every few seconds: keep them out of the traces
    with suppress_instrumentation(), db.connect() as conn:
        conn.cursor().execute("SELECT 1")
    return {"status": "ok"}


@app.get("/metrics", include_in_schema=False)
def metrics(request: Request):
    return metrics_response(request)


# --------------------------------------------------------------- login
@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    # nginx passes the original URI unencoded: /login?next=/d/x?orgId=1&from=now
    query = request.url.query
    next_url = safe_next(unquote(query[5:]) if query.startswith("next=") else None)
    claims, _ = validate(request)
    if claims is not None and claims.role != "service":
        return RedirectResponse(next_url, status_code=status.HTTP_303_SEE_OTHER)
    return HTMLResponse(render_login(next_url))


@app.post("/login")
def login_submit(request: Request, username: Annotated[str, Form(max_length=64)],
                 password: Annotated[str, Form(max_length=128)],
                 next: Annotated[str, Form(max_length=2048)] = "/"):  # noqa: A002
    next_url = safe_next(next)
    user, result = authenticate(username, password, client_ip(request), "form")
    if user is None:
        message = ("Забагато невдалих спроб. Спробуйте пізніше." if result == "throttled"
                   else "Невірне ім'я користувача або пароль.")
        code = status.HTTP_429_TOO_MANY_REQUESTS if result == "throttled" else status.HTTP_401_UNAUTHORIZED
        return HTMLResponse(render_login(next_url, message, username), status_code=code)
    if user["role"] == "service":
        DENIED.labels("forbidden").inc()
        return HTMLResponse(render_login(next_url, "Сервісний акаунт не може входити в інтерфейс.", username),
                            status_code=status.HTTP_403_FORBIDDEN)
    token, claims = issue_token(user["username"], user["role"], settings.session_ttl_min, "session")
    resp = RedirectResponse(next_url, status_code=status.HTTP_303_SEE_OTHER)
    resp.set_cookie(settings.cookie_name, token, max_age=settings.session_ttl_min * 60, httponly=True,
                    secure=settings.cookie_secure, samesite="lax", path="/")
    return resp


class TokenRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


@app.post("/api/token")
def api_token(request: Request, body: TokenRequest):
    user, result = authenticate(body.username, body.password, client_ip(request), "api")
    if user is None:
        code = status.HTTP_429_TOO_MANY_REQUESTS if result == "throttled" else status.HTTP_401_UNAUTHORIZED
        return JSONResponse({"detail": result}, status_code=code)
    token, _ = issue_token(user["username"], user["role"], settings.token_ttl_min, "access")
    return {"access_token": token, "token_type": "bearer", "expires_in": settings.token_ttl_min * 60,
            "role": user["role"]}


# -------------------------------------------------------------- verify
@app.get("/verify")
def verify(request: Request, scope: Annotated[Literal["any", "grafana", "admin"], Query()] = "any"):
    claims, result = validate(request)
    if claims is None:
        VERIFY.labels(scope, result).inc()
        DENIED.labels(result).inc()
        return Response(status_code=status.HTTP_401_UNAUTHORIZED, headers={"WWW-Authenticate": "Bearer"})
    allowed = (scope == "any"
               or (scope == "grafana" and claims.role in ("admin", "viewer"))
               or (scope == "admin" and claims.role == "admin"))
    if not allowed:
        VERIFY.labels(scope, "forbidden").inc()
        DENIED.labels("forbidden").inc()
        log.warning("access forbidden", extra={"event": "access_forbidden", "username": claims.username,
                                               "role": claims.role, "scope": scope,
                                               "uri": request.headers.get("x-original-uri", ""),
                                               "client_ip": client_ip(request)})
        return Response(status_code=status.HTTP_403_FORBIDDEN)
    VERIFY.labels(scope, "ok").inc()
    return Response(status_code=status.HTTP_200_OK, headers={"X-User": claims.username, "X-Role": claims.role})


# -------------------------------------------------------------- logout
@app.api_route("/logout", methods=["GET", "POST"])
def logout(request: Request):
    token = extract_token(request)
    if token:
        try:
            claims = decode_token(token)
            with db.connect() as conn:
                db.revoke(conn, claims.jti, claims.expires_at)
            log.info("logout", extra={"event": "logout", "username": claims.username,
                                      "client_ip": client_ip(request)})
            LOGOUTS.inc()
        except jwt.InvalidTokenError:
            pass
    resp = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    resp.delete_cookie(settings.cookie_name, path="/")
    return resp


# --------------------------------------------------------------- users
class UserIn(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: str = Field(min_length=8, max_length=128)
    role: Literal["admin", "viewer", "service"]


class UserPatch(BaseModel):
    password: str | None = Field(default=None, min_length=8, max_length=128)
    role: Literal["admin", "viewer", "service"] | None = None
    is_active: bool | None = None


@app.get("/api/users")
def users_list(admin: Admin):
    with db.connect() as conn:
        return [dict(r) for r in db.list_users(conn)]


@app.post("/api/users", status_code=status.HTTP_201_CREATED)
def users_create(admin: Admin, body: UserIn):
    with db.connect() as conn:
        try:
            db.create_user(conn, body.username, hash_password(body.password), body.role)
        except sqlite3.IntegrityError:
            raise HTTPException(status.HTTP_409_CONFLICT, "user already exists") from None
        _refresh_user_gauge(conn)
    log.info("user created", extra={"event": "user_created", "username": body.username,
                                    "role": body.role, "by": admin.username})
    return {"username": body.username, "role": body.role}


@app.patch("/api/users/{username}")
def users_update(admin: Admin, username: str, body: UserPatch):
    fields: dict[str, object] = {}
    if body.password is not None:
        fields["password_hash"] = hash_password(body.password)
    if body.role is not None:
        fields["role"] = body.role
    if body.is_active is not None:
        fields["is_active"] = int(body.is_active)
    with db.connect() as conn:
        if not db.update_user(conn, username, **fields):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
        _refresh_user_gauge(conn)
    log.info("user updated", extra={"event": "user_updated", "username": username,
                                    "fields": sorted(k for k in fields if k != "password_hash")
                                    + (["password"] if "password_hash" in fields else []),
                                    "by": admin.username})
    return {"username": username, "updated": True}


@app.delete("/api/users/{username}", status_code=status.HTTP_204_NO_CONTENT)
def users_delete(admin: Admin, username: str):
    if username == admin.username:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "cannot delete yourself")
    with db.connect() as conn:
        if not db.delete_user(conn, username):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
        _refresh_user_gauge(conn)
    log.info("user deleted", extra={"event": "user_deleted", "username": username, "by": admin.username})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=settings.http_port, log_config=None,
                proxy_headers=True, forwarded_allow_ips="*")
