import sqlite3

import pytest

from backend import auth, main

TEST_OWNER = "test-owner"


def register_test_trip(store, trip_id=None):
    connection = sqlite3.connect(store.database_path)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS trip_ownership (
            trip_id TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL,
            source TEXT NOT NULL
        )
        """
    )
    if trip_id is not None:
        connection.execute(
            "INSERT OR REPLACE INTO trip_ownership (trip_id, owner_id, source) VALUES (?, ?, 'legacy')",
            (trip_id, TEST_OWNER),
        )
    connection.commit()
    connection.close()


@pytest.fixture
def allow_local_owner():
    principal = auth.LocalPrincipal(TEST_OWNER, "test-session-hash", "test-csrf-hash")
    previous = main.app.dependency_overrides.copy()
    main.app.dependency_overrides[auth.require_local_owner] = lambda: principal
    main.app.dependency_overrides[auth.require_csrf] = lambda: principal
    try:
        yield principal
    finally:
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(previous)
