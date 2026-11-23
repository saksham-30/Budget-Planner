import sqlite3

import pytest

from backend import migrations


def _seed_database(path):
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE trip_preferences (trip_id TEXT PRIMARY KEY, preferences_json TEXT NOT NULL);
        CREATE TABLE trip_conversations (turn_id INTEGER PRIMARY KEY, trip_id TEXT, role TEXT, content TEXT);
        CREATE TABLE checkpoints (thread_id TEXT, checkpoint BLOB, PRIMARY KEY(thread_id));
        CREATE TABLE writes (thread_id TEXT, value BLOB, PRIMARY KEY(thread_id));
        INSERT INTO trip_preferences VALUES ('pref-trip', '{"keep":true}');
        INSERT INTO trip_conversations VALUES (1, 'conversation-trip', 'user', 'keep conversation');
        INSERT INTO checkpoints VALUES ('checkpoint-trip', X'0001FEFF');
        INSERT INTO writes VALUES ('write-trip', X'102030');
        """
    )
    connection.commit()
    connection.close()


def _rows(path):
    connection = sqlite3.connect(path)
    tables = ("trip_preferences", "trip_conversations", "checkpoints", "writes")
    result = {
        table: connection.execute(f'SELECT * FROM "{table}" ORDER BY 1').fetchall()
        for table in tables
    }
    connection.close()
    return result


@pytest.fixture
def unmodified_acl_check(monkeypatch):
    # These tests exercise SQLite backup and migration behavior, while Windows
    # ACL mechanics are inspected separately and are OS-specific.
    monkeypatch.setattr(migrations, "_restrict_backup_permissions", lambda *_: None)


def test_verified_backup_and_migration_preserve_all_legacy_payloads(
    tmp_path, unmodified_acl_check
):
    database = tmp_path / "planner.sqlite3"
    _seed_database(database)
    original_rows = _rows(database)

    backup = migrations.create_verified_backup(database, tmp_path / "backups")
    assert backup.backup_path.is_file()
    assert set(backup.row_counts) >= {
        "trip_preferences", "trip_conversations", "checkpoints", "writes"
    }
    assert migrations.migrate_local_owner(backup, "first-owner", "argon-hash") is True

    assert _rows(database) == original_rows
    connection = sqlite3.connect(database)
    original_owner = connection.execute(
        "SELECT owner_id, password_hash FROM local_owner WHERE singleton = 1"
    ).fetchone()
    mappings = dict(connection.execute("SELECT trip_id, owner_id FROM trip_ownership"))
    assert mappings == {
        "pref-trip": "first-owner",
        "conversation-trip": "first-owner",
        "checkpoint-trip": "first-owner",
        "write-trip": "first-owner",
    }
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    connection.close()

    fresh_backup = migrations.create_verified_backup(database, tmp_path / "backups")
    assert migrations.migrate_local_owner(fresh_backup, "new-cli-owner-id", "new-hash") is False
    connection = sqlite3.connect(database)
    assert connection.execute(
        "SELECT owner_id, password_hash FROM local_owner WHERE singleton = 1"
    ).fetchone() == original_owner
    assert dict(connection.execute("SELECT trip_id, owner_id FROM trip_ownership")) == mappings
    connection.close()


def test_stale_backup_rejects_migration_without_modifying_database(
    tmp_path, unmodified_acl_check
):
    database = tmp_path / "planner.sqlite3"
    _seed_database(database)
    backup = migrations.create_verified_backup(database, tmp_path / "backups")
    connection = sqlite3.connect(database)
    connection.execute("INSERT INTO writes VALUES ('late-trip', X'FF')")
    connection.commit()
    connection.close()
    changed_rows = _rows(database)

    with pytest.raises(migrations.MigrationSafetyError, match="changed after"):
        migrations.migrate_local_owner(backup, "owner", "hash")

    assert _rows(database) == changed_rows
    connection = sqlite3.connect(database)
    assert connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='local_owner'"
    ).fetchone() is None
    connection.close()


def test_migration_sql_failure_rolls_back_all_schema_changes(
    tmp_path, unmodified_acl_check
):
    database = tmp_path / "planner.sqlite3"
    _seed_database(database)
    original_rows = _rows(database)
    backup = migrations.create_verified_backup(database, tmp_path / "backups")

    with pytest.raises(migrations.MigrationSafetyError, match="failed without changing"):
        migrations.migrate_local_owner(backup, None, "hash")

    assert _rows(database) == original_rows
    connection = sqlite3.connect(database)
    new_tables = {
        row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    } & {"local_owner", "auth_sessions", "trip_ownership", "schema_migrations"}
    assert not new_tables
    connection.close()


def test_backup_verification_failure_leaves_source_untouched_and_backup_empty(tmp_path, monkeypatch):
    database = tmp_path / "planner.sqlite3"
    _seed_database(database)
    original_rows = _rows(database)
    backup_directory = tmp_path / "backups"

    def fail_acl_check(*_args):
        raise migrations.MigrationSafetyError("simulated backup verification failure")

    monkeypatch.setattr(migrations, "_restrict_backup_permissions", fail_acl_check)
    with pytest.raises(migrations.MigrationSafetyError, match="backup verification"):
        migrations.create_verified_backup(database, backup_directory)

    assert _rows(database) == original_rows
    backup_files = list(backup_directory.glob("*.sqlite3"))
    assert len(backup_files) == 1
    assert backup_files[0].stat().st_size == 0
    connection = sqlite3.connect(database)
    assert connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='local_owner'"
    ).fetchone() is None
    connection.close()
