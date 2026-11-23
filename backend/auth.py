"""Single-user local authentication and session handling."""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .config import CORS_ORIGINS, DATABASE_PATH

logger = logging.getLogger(__name__)

SESSION_COOKIE = "travel_planner_session"
CSRF_COOKIE = "travel_planner_csrf"
SESSION_HOURS = 12
MAX_FAILED_LOGINS = 5
LOCKOUT_SECONDS = 15 * 60
_password_hasher = PasswordHasher()

router = APIRouter(prefix="/api/auth", tags=["authentication"])


@dataclass(frozen=True)
class LocalPrincipal:
    owner_id: str
    session_hash: str
    csrf_hash: str


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


class LocalAuthUnavailable(RuntimeError):
    """Local authentication storage is missing or unavailable."""


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _connect(path: Path = DATABASE_PATH) -> sqlite3.Connection:
    try:
        connection = sqlite3.connect(path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        return connection
    except sqlite3.Error as exc:
        logger.warning("Authentication database unavailable (error_type=%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Local authentication is temporarily unavailable.") from exc


def _check_origin(request: Request) -> None:
    if request.headers.get("origin") not in CORS_ORIGINS:
        raise HTTPException(status_code=403, detail="Request origin is not allowed.")


def _auth_tables_ready(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'local_owner'"
    ).fetchone()
    return row is not None


def _get_owner() -> sqlite3.Row:
    connection = _connect()
    try:
        if not _auth_tables_ready(connection):
            raise LocalAuthUnavailable
        owner = connection.execute(
            "SELECT owner_id, password_hash, failed_login_count, locked_until FROM local_owner"
        ).fetchone()
        if owner is None:
            raise LocalAuthUnavailable
        return owner
    except sqlite3.Error as exc:
        logger.warning("Authentication lookup failed (error_type=%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Local authentication is temporarily unavailable.") from exc
    except LocalAuthUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail="Local authentication has not been initialized.",
        ) from exc
    finally:
        connection.close()


def _record_failed_login() -> None:
    connection = _connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            UPDATE local_owner
            SET failed_login_count = failed_login_count + 1,
                locked_until = CASE
                    WHEN failed_login_count + 1 >= ? THEN ?
                    ELSE locked_until
                END
            """,
            (MAX_FAILED_LOGINS, time.time() + LOCKOUT_SECONDS),
        )
        connection.commit()
    except sqlite3.Error as exc:
        connection.rollback()
        logger.warning("Authentication update failed (error_type=%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Local authentication is temporarily unavailable.") from exc
    finally:
        connection.close()


def _create_session(owner_id: str, password_hash: str) -> tuple[str, str]:
    raw_session = secrets.token_urlsafe(32)
    raw_csrf = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    connection = _connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            UPDATE local_owner
            SET failed_login_count = 0, locked_until = NULL, password_hash = ?
            """,
            (password_hash,),
        )
        # A successful login rotates the browser session and invalidates older ones.
        connection.execute("DELETE FROM auth_sessions WHERE owner_id = ?", (owner_id,))
        connection.execute(
            """
            INSERT INTO auth_sessions (session_hash, owner_id, csrf_hash, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                _token_hash(raw_session),
                owner_id,
                _token_hash(raw_csrf),
                now.isoformat(),
                (now + timedelta(hours=SESSION_HOURS)).isoformat(),
            ),
        )
        connection.commit()
    except sqlite3.Error as exc:
        connection.rollback()
        logger.warning("Session creation failed (error_type=%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Local authentication is temporarily unavailable.") from exc
    finally:
        connection.close()
    return raw_session, raw_csrf


def _set_auth_cookies(response: Response, session_token: str, csrf_token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        session_token,
        max_age=SESSION_HOURS * 60 * 60,
        httponly=True,
        secure=False,
        samesite="strict",
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE,
        csrf_token,
        max_age=SESSION_HOURS * 60 * 60,
        httponly=False,
        secure=False,
        samesite="strict",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"


def get_current_principal(request: Request) -> LocalPrincipal:
    raw_session = request.cookies.get(SESSION_COOKIE)
    if not raw_session:
        raise HTTPException(status_code=401, detail="Authentication required.")
    session_hash = _token_hash(raw_session)
    connection = _connect()
    try:
        if not _auth_tables_ready(connection):
            raise LocalAuthUnavailable
        row = connection.execute(
            """
            SELECT owner_id, csrf_hash, expires_at
            FROM auth_sessions
            WHERE session_hash = ?
            """,
            (session_hash,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=401, detail="Authentication required.")
        expires_at = datetime.fromisoformat(row["expires_at"])
        if expires_at <= datetime.now(timezone.utc):
            raise HTTPException(status_code=401, detail="Authentication required.")
        return LocalPrincipal(
            owner_id=row["owner_id"],
            session_hash=session_hash,
            csrf_hash=row["csrf_hash"],
        )
    except sqlite3.Error as exc:
        logger.warning("Session lookup failed (error_type=%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Local authentication is temporarily unavailable.") from exc
    except LocalAuthUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail="Local authentication has not been initialized.",
        ) from exc
    finally:
        connection.close()


def require_local_owner(
    principal: LocalPrincipal = Depends(get_current_principal),
) -> LocalPrincipal:
    return principal


def require_csrf(
    request: Request,
    principal: LocalPrincipal = Depends(get_current_principal),
) -> LocalPrincipal:
    _check_origin(request)
    csrf_header = request.headers.get("x-csrf-token", "")
    csrf_cookie = request.cookies.get(CSRF_COOKIE, "")
    if (
        not csrf_header
        or not csrf_cookie
        or not hmac.compare_digest(csrf_header, csrf_cookie)
        or not hmac.compare_digest(_token_hash(csrf_cookie), principal.csrf_hash)
    ):
        raise HTTPException(status_code=403, detail="CSRF validation failed.")
    return principal


@router.post("/login")
def login(payload: LoginRequest, request: Request, response: Response) -> dict[str, str]:
    _check_origin(request)
    owner = _get_owner()
    locked_until = owner["locked_until"]
    if locked_until is not None and locked_until > time.time():
        raise HTTPException(status_code=429, detail="Login temporarily unavailable. Try again later.")
    try:
        valid = _password_hasher.verify(owner["password_hash"], payload.password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        valid = False
    if not valid:
        _record_failed_login()
        raise HTTPException(status_code=401, detail="Invalid password.")
    password_hash = owner["password_hash"]
    if _password_hasher.check_needs_rehash(password_hash):
        password_hash = _password_hasher.hash(payload.password)
    session_token, csrf_token = _create_session(owner["owner_id"], password_hash)
    _set_auth_cookies(response, session_token, csrf_token)
    return {"authenticated": "true"}


@router.get("/me")
def current_user(
    response: Response,
    principal: LocalPrincipal = Depends(require_local_owner),
) -> dict[str, str]:
    response.headers["Cache-Control"] = "no-store"
    return {"authenticated": "true", "owner": "local"}


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    principal: LocalPrincipal = Depends(require_csrf),
) -> dict[str, str]:
    connection = _connect()
    try:
        connection.execute(
            "DELETE FROM auth_sessions WHERE session_hash = ?",
            (principal.session_hash,),
        )
        connection.commit()
    except sqlite3.Error as exc:
        connection.rollback()
        logger.warning("Session invalidation failed (error_type=%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Local authentication is temporarily unavailable.") from exc
    finally:
        connection.close()
    response.delete_cookie(SESSION_COOKIE, path="/", samesite="strict")
    response.delete_cookie(CSRF_COOKIE, path="/", samesite="strict")
    response.headers["Cache-Control"] = "no-store"
    return {"authenticated": "false"}
