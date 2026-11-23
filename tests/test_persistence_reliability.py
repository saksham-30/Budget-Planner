import json
import sqlite3
from datetime import date

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.models import TripPreferences
from backend.persistence import (
    CorruptPersistenceError,
    PersistenceError,
    PersistenceUnavailableError,
    TripStore,
)
from conftest import register_test_trip


def preferences(budget=30000):
    return TripPreferences(
        destination="Goa",
        start_date=date(2027, 1, 10),
        end_date=date(2027, 1, 15),
        travelers=2,
        budget=budget,
        currency="INR",
    )


def insert_raw_preferences(database, value):
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            """
            INSERT INTO trip_preferences (trip_id, preferences_json)
            VALUES (?, ?)
            """,
            ("corrupt-trip", value),
        )
        connection.commit()
    finally:
        connection.close()


def test_malformed_persisted_json_is_reported_without_changing_original(tmp_path, caplog):
    database = tmp_path / "malformed.sqlite3"
    store = TripStore(database)
    original = '{"destination":"PRIVATE_MARKER",'
    insert_raw_preferences(database, original)

    with pytest.raises(CorruptPersistenceError):
        store.get_preferences("corrupt-trip")

    assert store.list_trips() == []
    with sqlite3.connect(database) as connection:
        saved = connection.execute(
            "SELECT preferences_json FROM trip_preferences WHERE trip_id = ?",
            ("corrupt-trip",),
        ).fetchone()[0]
    assert saved == original
    assert "PRIVATE_MARKER" not in caplog.text
    assert "Invalid persisted value" in caplog.text


@pytest.mark.parametrize("stored_value", ["null", "{}", "[]"])
def test_missing_or_unexpected_persisted_json_is_rejected(tmp_path, stored_value):
    database = tmp_path / f"unexpected-{stored_value.encode().hex()}.sqlite3"
    store = TripStore(database)
    insert_raw_preferences(database, stored_value)

    with pytest.raises(CorruptPersistenceError):
        store.get_preferences("corrupt-trip")

    with sqlite3.connect(database) as connection:
        saved = connection.execute(
            "SELECT preferences_json FROM trip_preferences WHERE trip_id = ?",
            ("corrupt-trip",),
        ).fetchone()[0]
    assert saved == stored_value


def test_null_legacy_values_in_preferences_and_conversation_are_controlled(tmp_path):
    database = tmp_path / "nullable-legacy.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE trip_preferences (
                trip_id TEXT PRIMARY KEY,
                preferences_json TEXT,
                updated_at TEXT
            )
            """
        )
        connection.execute(
            "INSERT INTO trip_preferences VALUES (?, ?, ?)",
            ("null-trip", None, None),
        )
        connection.execute(
            """
            CREATE TABLE trip_conversations (
                turn_id INTEGER PRIMARY KEY,
                trip_id TEXT,
                role TEXT,
                content TEXT,
                created_at TEXT
            )
            """
        )
        connection.execute(
            "INSERT INTO trip_conversations VALUES (?, ?, ?, ?, ?)",
            (1, "null-trip", "assistant", None, None),
        )

    store = TripStore(database)
    with pytest.raises(CorruptPersistenceError):
        store.get_preferences("null-trip")
    with pytest.raises(CorruptPersistenceError):
        store.get_conversation("null-trip")

    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT preferences_json FROM trip_preferences WHERE trip_id = ?",
            ("null-trip",),
        ).fetchone()[0] is None
        assert connection.execute(
            "SELECT content FROM trip_conversations WHERE trip_id = ?",
            ("null-trip",),
        ).fetchone()[0] is None


def test_locked_database_returns_a_controlled_unavailable_error(tmp_path):
    database = tmp_path / "locked.sqlite3"
    store = TripStore(database, timeout=0.01)
    blocker = sqlite3.connect(database, timeout=0.01, isolation_level=None)
    try:
        blocker.execute("BEGIN EXCLUSIVE")
        with pytest.raises(PersistenceUnavailableError):
            store.get_preferences("missing-trip")
    finally:
        blocker.rollback()
        blocker.close()


def test_connection_failure_is_wrapped_without_database_details(tmp_path, monkeypatch, caplog):
    database = tmp_path / "connection-failure.sqlite3"
    store = TripStore(database)

    def fail_connect(*args, **kwargs):
        raise sqlite3.OperationalError("unable to open database file: private path")

    monkeypatch.setattr("backend.persistence.sqlite3.connect", fail_connect)

    with pytest.raises(PersistenceUnavailableError) as error:
        store.list_trips()
    assert "private path" not in str(error.value)
    assert "private path" not in caplog.text


def test_failed_transaction_rolls_back_prior_writes(tmp_path):
    store = TripStore(tmp_path / "rollback.sqlite3")

    with pytest.raises(RuntimeError, match="forced failure"):
        with store.connect() as connection:
            connection.execute(
                "INSERT INTO trip_preferences (trip_id, preferences_json) VALUES (?, ?)",
                ("rolled-back", "{}"),
            )
            raise RuntimeError("forced failure")

    assert store.get_preferences("rolled-back") is None


def test_plan_preferences_and_conversation_are_one_atomic_transaction(tmp_path):
    database = tmp_path / "atomic-plan.sqlite3"
    store = TripStore(database)
    original = preferences()
    store.save_preferences("atomic-trip", original)
    store.append_conversation("atomic-trip", "old question", "old answer")
    with store.connect() as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_assistant_turn
            BEFORE INSERT ON trip_conversations
            WHEN NEW.role = 'assistant'
            BEGIN
                SELECT RAISE(ABORT, 'forced conversation failure');
            END
            """
        )

    with pytest.raises(PersistenceError):
        store.save_plan_update(
            "atomic-trip",
            original.model_copy(update={"budget": 45000}),
            "new question",
            "new answer",
        )

    assert store.get_preferences("atomic-trip") == original
    assert store.get_conversation("atomic-trip") == [
        {"role": "user", "content": "old question"},
        {"role": "assistant", "content": "old answer"},
    ]


def test_repeated_initialization_preserves_existing_trip_data(tmp_path):
    database = tmp_path / "repeat-init.sqlite3"
    store = TripStore(database)
    saved = preferences()
    store.save_plan_update("preserved-trip", saved, "question", "answer")

    TripStore(database)
    reopened = TripStore(database)

    assert reopened.get_preferences("preserved-trip") == saved
    assert reopened.get_conversation("preserved-trip") == [
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "answer"},
    ]


def test_persistence_failures_have_safe_api_responses(tmp_path, monkeypatch, allow_local_owner):
    store = TripStore(tmp_path / "api-corruption.sqlite3")
    insert_raw_preferences(store.database_path, "not-json")
    register_test_trip(store, "corrupt-trip")
    monkeypatch.setattr(main, "TripStore", lambda: store)
    client = TestClient(main.app)

    response = client.get("/api/trips/corrupt-trip/preferences")

    assert response.status_code == 500
    assert response.json()["error"] == "Stored trip data is invalid."
    assert str(store.database_path) not in response.text
    assert "not-json" not in response.text


def test_database_unavailability_has_safe_api_response(monkeypatch, allow_local_owner):
    def unavailable_store():
        raise PersistenceUnavailableError

    monkeypatch.setattr(main, "TripStore", unavailable_store)
    response = TestClient(main.app).get("/api/trips")

    assert response.status_code == 503
    assert response.json()["error"] == "Trip data is temporarily unavailable."
    assert "database" not in response.text.casefold()


def test_trip_listing_skips_corrupt_rows_but_preserves_them(tmp_path):
    database = tmp_path / "list-corrupt.sqlite3"
    store = TripStore(database)
    store.save_preferences("valid-trip", preferences())
    insert_raw_preferences(database, "null")

    trips = store.list_trips()

    assert [trip["trip_id"] for trip in trips] == ["valid-trip"]
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT preferences_json FROM trip_preferences WHERE trip_id = ?",
            ("corrupt-trip",),
        ).fetchone()[0] == "null"


def test_preferences_serialization_round_trip_remains_unchanged(tmp_path):
    store = TripStore(tmp_path / "round-trip.sqlite3")
    saved = preferences()

    store.save_preferences("round-trip", saved)

    assert store.get_preferences("round-trip") == saved
    connection = sqlite3.connect(store.database_path)
    try:
        persisted = connection.execute(
            "SELECT preferences_json FROM trip_preferences WHERE trip_id = ?",
            ("round-trip",),
        ).fetchone()[0]
    finally:
        connection.close()
    assert json.loads(persisted)["destination"] == "Goa"
