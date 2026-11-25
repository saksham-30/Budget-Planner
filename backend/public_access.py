"""Anonymous public access and shared persistence ownership."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from fastapi import Depends, Request

from .config import DATABASE_PATH


@dataclass(frozen=True)
class LocalPrincipal:
    """Compatibility name for the shared public persistence owner."""

    owner_id: str
    session_hash: str = ""
    csrf_hash: str = ""


def initialize_deployment_owner() -> bool:
    """Ensure the trip ownership registry exists and retain existing trip ownership."""
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH, timeout=10.0)
    try:
        connection.execute("BEGIN IMMEDIATE")
        owner_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='local_owner'"
        ).fetchone()
        owner = connection.execute("SELECT owner_id FROM local_owner LIMIT 1").fetchone() if owner_table else None
        owner_id = owner[0] if owner else "public-anonymous-owner"
        connection.execute("""CREATE TABLE IF NOT EXISTS trip_ownership (
            trip_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
            source TEXT NOT NULL CHECK (source IN ('legacy', 'created'))
        )""")
        for table in (
            "trip_preferences", "trip_itineraries", "trip_conversations",
            "copilot_conversations", "checkpoints",
        ):
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if exists:
                key = "thread_id" if table == "checkpoints" else "trip_id"
                connection.execute(
                    f"INSERT OR IGNORE INTO trip_ownership (trip_id, owner_id, source) "
                    f"SELECT DISTINCT {key}, ?, 'legacy' FROM {table} WHERE {key} IS NOT NULL",
                    (owner_id,),
                )
        connection.commit()
        return True
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def get_current_principal(_request: Request) -> LocalPrincipal:
    """Resolve the shared owner without requiring a browser session."""
    owner_id = "public-anonymous-owner"
    try:
        with sqlite3.connect(DATABASE_PATH, timeout=5.0) as connection:
            row = connection.execute(
                "SELECT owner_id FROM local_owner WHERE singleton = 1"
            ).fetchone()
            if row and row[0]:
                owner_id = row[0]
    except sqlite3.Error:
        pass
    return LocalPrincipal(owner_id)


def require_local_owner(
    principal: LocalPrincipal = Depends(get_current_principal),
) -> LocalPrincipal:
    return principal


def require_csrf(
    principal: LocalPrincipal = Depends(get_current_principal),
) -> LocalPrincipal:
    """Compatibility dependency; anonymous public requests do not use CSRF tokens."""
    return principal
