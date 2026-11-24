"""Small SQLite stores for trip preferences and thread-independent metadata."""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Iterator, Sequence

from .config import DATABASE_PATH
from .models import TripPreferences

logger = logging.getLogger(__name__)


class PersistenceError(RuntimeError):
    """A persistence operation failed; messages are safe for API responses."""

    status_code = 500
    public_message = "Trip data could not be processed."
    kind = "database_error"


class PersistenceUnavailableError(PersistenceError):
    """SQLite is temporarily unavailable, for example because it is locked."""

    status_code = 503
    public_message = "Trip data is temporarily unavailable."
    kind = "database_unavailable"


class CorruptPersistenceError(PersistenceError):
    """Stored application data failed validation and was left untouched."""

    public_message = "Stored trip data is invalid."
    kind = "corrupt_data"


class TripNotFoundError(PersistenceError):
    """The trip is absent or not owned by the authenticated local user."""

    status_code = 404
    public_message = "Trip not found."
    kind = "trip_not_found"


def _is_temporarily_unavailable(error: sqlite3.Error) -> bool:
    message = str(error).casefold()
    return any(
        marker in message
        for marker in (
            "locked",
            "busy",
            "unable to open database",
            "disk i/o error",
            "readonly database",
        )
    )


class TripStore:
    def __init__(
        self,
        database_path: Path | str = DATABASE_PATH,
        timeout: float = 10.0,
    ):
        self.database_path = Path(database_path)
        self.timeout = timeout
        try:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning(
                "Unable to initialize persistence directory (error_type=%s)",
                type(exc).__name__,
            )
            raise PersistenceUnavailableError from exc
        self.initialize()

    @staticmethod
    def _rollback(connection: sqlite3.Connection | None) -> None:
        if connection is None:
            return
        try:
            connection.rollback()
        except sqlite3.Error as exc:
            logger.warning(
                "SQLite rollback failed (error_type=%s)",
                type(exc).__name__,
            )

    @staticmethod
    def _log_invalid_record(table: str, field: str, error: Exception) -> None:
        logger.warning(
            "Invalid persisted value (table=%s, field=%s, error_type=%s)",
            table,
            field,
            type(error).__name__,
        )

    @staticmethod
    def _sqlite_error(error: sqlite3.Error) -> PersistenceError:
        if _is_temporarily_unavailable(error):
            return PersistenceUnavailableError()
        return PersistenceError()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                self.database_path,
                timeout=self.timeout,
            )
            connection.row_factory = sqlite3.Row
            yield connection
            connection.commit()
        except PersistenceError:
            self._rollback(connection)
            raise
        except sqlite3.Error as exc:
            self._rollback(connection)
            logger.warning(
                "SQLite operation failed (error_type=%s)",
                type(exc).__name__,
            )
            raise self._sqlite_error(exc) from exc
        except Exception:
            self._rollback(connection)
            raise
        finally:
            if connection is not None:
                try:
                    connection.close()
                except sqlite3.Error as exc:
                    logger.warning(
                        "SQLite connection close failed (error_type=%s)",
                        type(exc).__name__,
                    )

    @staticmethod
    def _execute(
        connection: sqlite3.Connection,
        statement: str,
        parameters: Sequence[object] = (),
    ) -> int:
        with closing(connection.cursor()) as cursor:
            cursor.execute(statement, parameters)
            return cursor.rowcount

    @staticmethod
    def _execute_many(
        connection: sqlite3.Connection,
        statement: str,
        parameters: Sequence[Sequence[object]],
    ) -> None:
        with closing(connection.cursor()) as cursor:
            cursor.executemany(statement, parameters)

    @staticmethod
    def _fetchone(
        connection: sqlite3.Connection,
        statement: str,
        parameters: Sequence[object] = (),
    ) -> sqlite3.Row | None:
        with closing(connection.execute(statement, parameters)) as cursor:
            return cursor.fetchone()

    @staticmethod
    def _fetchall(
        connection: sqlite3.Connection,
        statement: str,
        parameters: Sequence[object] = (),
    ) -> list[sqlite3.Row]:
        with closing(connection.execute(statement, parameters)) as cursor:
            return cursor.fetchall()

    def initialize(self) -> None:
        with self.connect() as connection:
            self._execute(
                connection,
                """
                CREATE TABLE IF NOT EXISTS trip_preferences (
                    trip_id TEXT PRIMARY KEY,
                    preferences_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """,
            )
            self._execute(
                connection,
                """
                CREATE TABLE IF NOT EXISTS trip_conversations (
                    turn_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trip_id TEXT NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """,
            )
            # Phase 3 — itinerary storage (additive, backward-compatible)
            self._execute(
                connection,
                """
                CREATE TABLE IF NOT EXISTS trip_itineraries (
                    trip_id TEXT PRIMARY KEY,
                    itinerary_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """,
            )
            # Phase 7 — copilot conversation history (additive)
            self._execute(
                connection,
                """
                CREATE TABLE IF NOT EXISTS copilot_conversations (
                    turn_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trip_id TEXT NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """,
            )

    @staticmethod
    def _save_preferences(
        connection: sqlite3.Connection,
        trip_id: str,
        preferences: TripPreferences,
    ) -> None:
        TripStore._execute(
            connection,
            """
            INSERT INTO trip_preferences (trip_id, preferences_json, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(trip_id) DO UPDATE SET
                preferences_json = excluded.preferences_json,
                updated_at = CURRENT_TIMESTAMP
            """,
            (trip_id, json.dumps(preferences.model_dump(mode="json"))),
        )

    @staticmethod
    def _append_conversation(
        connection: sqlite3.Connection,
        trip_id: str,
        user_message: str,
        assistant_message: str,
    ) -> None:
        TripStore._execute_many(
            connection,
            "INSERT INTO trip_conversations (trip_id, role, content) VALUES (?, ?, ?)",
            [
                (trip_id, "user", user_message),
                (trip_id, "assistant", assistant_message),
            ],
        )

    @staticmethod
    def _decode_preferences(value: object) -> TripPreferences:
        try:
            data = json.loads(value)
            if not isinstance(data, dict):
                raise ValueError("Persisted preferences must be a JSON object.")
            return TripPreferences.model_validate(data)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            TripStore._log_invalid_record("trip_preferences", "preferences_json", exc)
            raise CorruptPersistenceError from exc

    def save_preferences(self, trip_id: str, preferences: TripPreferences) -> None:
        with self.connect() as connection:
            self._save_preferences(connection, trip_id, preferences)

    @staticmethod
    def _assert_trip_owner(
        connection: sqlite3.Connection,
        trip_id: str,
        owner_id: str,
    ) -> None:
        if not TripStore._fetchone(
            connection,
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'trip_ownership'",
        ):
            raise PersistenceUnavailableError()
        row = TripStore._fetchone(
            connection,
            "SELECT owner_id FROM trip_ownership WHERE trip_id = ?",
            (trip_id,),
        )
        if row is None or row["owner_id"] != owner_id:
            raise TripNotFoundError()

    @staticmethod
    def _has_trip_data(connection: sqlite3.Connection, trip_id: str) -> bool:
        tables = {
            row["name"]
            for row in TripStore._fetchall(
                connection,
                "SELECT name FROM sqlite_master WHERE type = 'table'",
            )
        }
        for table, key in (
            ("trip_preferences", "trip_id"),
            ("trip_conversations", "trip_id"),
            ("checkpoints", "thread_id"),
            ("writes", "thread_id"),
        ):
            if table in tables and TripStore._fetchone(
                connection,
                f'SELECT 1 FROM "{table}" WHERE "{key}" = ? LIMIT 1',
                (trip_id,),
            ):
                return True
        return False

    def _assign_new_trip(
        self,
        connection: sqlite3.Connection,
        trip_id: str,
        owner_id: str,
    ) -> None:
        registry = self._fetchone(
            connection,
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'trip_ownership'",
        )
        if registry is None:
            raise PersistenceUnavailableError()
        existing = self._fetchone(
            connection,
            "SELECT owner_id FROM trip_ownership WHERE trip_id = ?",
            (trip_id,),
        )
        if existing is not None:
            if existing["owner_id"] != owner_id:
                raise TripNotFoundError()
            return
        if self._has_trip_data(connection, trip_id):
            raise TripNotFoundError()
        self._execute(
            connection,
            "INSERT INTO trip_ownership (trip_id, owner_id, source) VALUES (?, ?, 'created')",
            (trip_id, owner_id),
        )

    def assert_trip_owner(self, trip_id: str, owner_id: str) -> None:
        with self.connect() as connection:
            self._assert_trip_owner(connection, trip_id, owner_id)

    def save_preferences_for_owner(
        self,
        trip_id: str,
        preferences: TripPreferences,
        owner_id: str,
    ) -> None:
        with self.connect() as connection:
            self._assign_new_trip(connection, trip_id, owner_id)
            self._save_preferences(connection, trip_id, preferences)

    def save_plan_update_for_owner(
        self,
        trip_id: str,
        preferences: TripPreferences,
        user_message: str,
        assistant_message: str,
        owner_id: str,
    ) -> list[dict[str, str]]:
        with self.connect() as connection:
            self._assert_trip_owner(connection, trip_id, owner_id)
            self._save_preferences(connection, trip_id, preferences)
            self._append_conversation(
                connection,
                trip_id,
                user_message,
                assistant_message,
            )
            conversation = self._read_conversation(connection, trip_id)
        return conversation

    def save_plan_update(
        self,
        trip_id: str,
        preferences: TripPreferences,
        user_message: str,
        assistant_message: str,
    ) -> list[dict[str, str]]:
        """Persist the preference update and its conversation pair atomically."""
        with self.connect() as connection:
            self._save_preferences(connection, trip_id, preferences)
            self._append_conversation(
                connection,
                trip_id,
                user_message,
                assistant_message,
            )
            conversation = self._read_conversation(connection, trip_id)
        return conversation

    def get_preferences(self, trip_id: str) -> TripPreferences | None:
        with self.connect() as connection:
            row = self._fetchone(
                connection,
                "SELECT preferences_json FROM trip_preferences WHERE trip_id = ?",
                (trip_id,),
            )
        if row is None:
            return None
        return self._decode_preferences(row["preferences_json"])

    def get_preferences_for_owner(
        self,
        trip_id: str,
        owner_id: str,
    ) -> TripPreferences | None:
        with self.connect() as connection:
            self._assert_trip_owner(connection, trip_id, owner_id)
            row = self._fetchone(
                connection,
                "SELECT preferences_json FROM trip_preferences WHERE trip_id = ?",
                (trip_id,),
            )
        if row is None:
            return None
        return self._decode_preferences(row["preferences_json"])

    def list_trips(self, limit: int = 20) -> list[dict[str, object]]:
        with self.connect() as connection:
            rows = self._fetchall(
                connection,
                """
                SELECT trip_id, preferences_json, updated_at
                FROM trip_preferences
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (max(1, min(limit, 50)),),
            )
        trips: list[dict[str, object]] = []
        for row in rows:
            try:
                preferences = self._decode_preferences(row["preferences_json"])
                if not isinstance(row["trip_id"], str) or not isinstance(row["updated_at"], str):
                    raise ValueError("Persisted trip metadata is incomplete.")
            except (CorruptPersistenceError, TypeError, ValueError) as exc:
                if not isinstance(exc, CorruptPersistenceError):
                    self._log_invalid_record("trip_preferences", "trip_metadata", exc)
                continue
            trips.append(
                {
                    "trip_id": row["trip_id"],
                    "destination": preferences.destination,
                    "budget": preferences.budget,
                    "currency": preferences.currency,
                    "travelers": preferences.travelers,
                    "updated_at": row["updated_at"],
                }
            )
        return trips

    def list_trips_for_owner(
        self,
        owner_id: str,
        limit: int = 20,
    ) -> list[dict[str, object]]:
        with self.connect() as connection:
            rows = self._fetchall(
                connection,
                """
                SELECT p.trip_id, p.preferences_json, p.updated_at
                FROM trip_preferences AS p
                JOIN trip_ownership AS o ON o.trip_id = p.trip_id
                WHERE o.owner_id = ?
                ORDER BY p.updated_at DESC
                LIMIT ?
                """,
                (owner_id, max(1, min(limit, 50))),
            )
        return self._format_trip_rows(rows)

    def _format_trip_rows(self, rows: list[sqlite3.Row]) -> list[dict[str, object]]:
        trips: list[dict[str, object]] = []
        for row in rows:
            try:
                preferences = self._decode_preferences(row["preferences_json"])
                if not isinstance(row["trip_id"], str) or not isinstance(row["updated_at"], str):
                    raise ValueError("Persisted trip metadata is incomplete.")
            except (CorruptPersistenceError, TypeError, ValueError) as exc:
                if not isinstance(exc, CorruptPersistenceError):
                    self._log_invalid_record("trip_preferences", "trip_metadata", exc)
                continue
            trips.append(
                {
                    "trip_id": row["trip_id"],
                    "destination": preferences.destination,
                    "budget": preferences.budget,
                    "currency": preferences.currency,
                    "travelers": preferences.travelers,
                    "updated_at": row["updated_at"],
                }
            )
        return trips

    def append_conversation(
        self,
        trip_id: str,
        user_message: str,
        assistant_message: str,
    ) -> None:
        with self.connect() as connection:
            self._append_conversation(
                connection,
                trip_id,
                user_message,
                assistant_message,
            )

    def get_conversation(self, trip_id: str) -> list[dict[str, str]]:
        with self.connect() as connection:
            return self._read_conversation(connection, trip_id)

    def get_conversation_for_owner(
        self,
        trip_id: str,
        owner_id: str,
    ) -> list[dict[str, str]]:
        with self.connect() as connection:
            self._assert_trip_owner(connection, trip_id, owner_id)
            return self._read_conversation(connection, trip_id)

    @classmethod
    def _read_conversation(
        cls,
        connection: sqlite3.Connection,
        trip_id: str,
    ) -> list[dict[str, str]]:
        rows = cls._fetchall(
            connection,
            "SELECT role, content FROM trip_conversations WHERE trip_id = ? ORDER BY turn_id",
            (trip_id,),
        )
        conversation: list[dict[str, str]] = []
        for row in rows:
            role = row["role"]
            content = row["content"]
            if role not in {"user", "assistant"} or not isinstance(content, str):
                error = ValueError("Persisted conversation row is incomplete.")
                cls._log_invalid_record("trip_conversations", "role_or_content", error)
                raise CorruptPersistenceError from error
            conversation.append({"role": role, "content": content})
        return conversation

    # ── Phase 3: Itinerary persistence ──────────────────────────────────────

    def save_itinerary_for_owner(
        self,
        trip_id: str,
        itinerary_json: str,
        owner_id: str,
    ) -> None:
        """Upsert the itinerary JSON for an owned trip."""
        with self.connect() as connection:
            self._assert_trip_owner(connection, trip_id, owner_id)
            self._execute(
                connection,
                """
                INSERT INTO trip_itineraries (trip_id, itinerary_json, created_at, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(trip_id) DO UPDATE SET
                    itinerary_json = excluded.itinerary_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (trip_id, itinerary_json),
            )

    def get_itinerary_for_owner(
        self,
        trip_id: str,
        owner_id: str,
    ) -> str | None:
        """Return raw itinerary JSON for an owned trip, or None if not saved."""
        with self.connect() as connection:
            self._assert_trip_owner(connection, trip_id, owner_id)
            row = self._fetchone(
                connection,
                "SELECT itinerary_json FROM trip_itineraries WHERE trip_id = ?",
                (trip_id,),
            )
        if row is None:
            return None
        return str(row["itinerary_json"])

    # ── Phase 7: Copilot conversation persistence ────────────────────────────

    def append_copilot_turn(
        self,
        trip_id: str,
        user_message: str,
        assistant_message: str,
        owner_id: str,
    ) -> None:
        """Persist one user/assistant exchange to the copilot conversation."""
        with self.connect() as connection:
            self._assert_trip_owner(connection, trip_id, owner_id)
            self._execute_many(
                connection,
                "INSERT INTO copilot_conversations (trip_id, role, content) VALUES (?, ?, ?)",
                [
                    (trip_id, "user", user_message),
                    (trip_id, "assistant", assistant_message),
                ],
            )

    def get_copilot_history_for_owner(
        self,
        trip_id: str,
        owner_id: str,
        limit: int = 40,
    ) -> list[dict[str, str]]:
        """Return the most recent copilot conversation turns for a trip."""
        with self.connect() as connection:
            self._assert_trip_owner(connection, trip_id, owner_id)
            rows = self._fetchall(
                connection,
                """
                SELECT role, content FROM copilot_conversations
                WHERE trip_id = ?
                ORDER BY turn_id DESC
                LIMIT ?
                """,
                (trip_id, max(1, min(limit, 100))),
            )
        # Reverse so oldest turn is first
        result = []
        for row in reversed(rows):
            role = row["role"]
            content = row["content"]
            if role not in {"user", "assistant"} or not isinstance(content, str):
                raise CorruptPersistenceError
            result.append({"role": role, "content": content})
        return result

    def clear_copilot_history_for_owner(
        self,
        trip_id: str,
        owner_id: str,
    ) -> None:
        """Delete all copilot turns for a trip (user-initiated reset only)."""
        with self.connect() as connection:
            self._assert_trip_owner(connection, trip_id, owner_id)
            self._execute(
                connection,
                "DELETE FROM copilot_conversations WHERE trip_id = ?",
                (trip_id,),
            )
