import copy
from dataclasses import replace
import json
import logging
import sqlite3
import socket

import pytest

from backend import manage_auth, migrations
from backend.persistence import TripStore
from scripts import observe_auth_processes

PASSWORD = "local-test-password-123"


def _process_row(pid, parent_pid, executable, created, reasons=(), classification="unclassified_match"):
    return {
        "pid": pid,
        "parent_pid": parent_pid,
        "executable": executable,
        "executable_path": rf"C:\\Bin\\{executable}",
        "creation_time": f"dmtf-{created}",
        "creation_filetime": str(created),
        "match_reasons": list(reasons),
        "classification": classification,
    }


def _process_tree_snapshot(candidate_rows, *, followup_rows=None, observation=True):
    runner = _process_row(33000, 1, "python.exe", 100)
    cli = _process_row(
        34000, 33000, "python.exe", 200, ("manage_auth_token",), "auth_cli_module"
    )
    external_parent = _process_row(45276, 1, "powershell.exe", 150)
    common = [runner, cli, external_parent]
    initial_rows = [*common, *candidate_rows]
    recheck_rows = copy.deepcopy([*common, *candidate_rows])
    snapshots = [
        {"label": "initial", "processes": initial_rows},
        {"label": "recheck", "processes": recheck_rows},
    ]
    if observation:
        snapshots.append({"label": "followup", "processes": followup_rows or recheck_rows})
    return {
        "current_cli_pid": 34000,
        "current_cli_creation_filetime": "200",
        "runner_pid": 33000,
        "runner_creation_filetime": "100",
        "observation_mode": observation,
        "snapshots": snapshots,
    }


def _configure_cli(tmp_path, monkeypatch):
    database = tmp_path / "project" / "data" / "travel_planner.sqlite3"
    database.parent.mkdir(parents=True)
    TripStore(database)
    backup_directory = tmp_path / "private-backups"
    monkeypatch.setattr(manage_auth, "DATABASE_PATH", database)
    monkeypatch.setattr(manage_auth, "_expected_project_database_path", lambda: database)
    monkeypatch.setattr(manage_auth, "_backup_directory", lambda: backup_directory)
    monkeypatch.setattr(migrations, "_restrict_backup_permissions", lambda *_: None)
    monkeypatch.setattr(manage_auth, "assert_application_stopped", lambda *_: None)
    monkeypatch.setattr(manage_auth, "assert_database_quiescent", lambda *_: None)
    return database, backup_directory


def test_password_prompt_occurs_only_after_verified_backup(tmp_path, monkeypatch, capsys, caplog):
    caplog.set_level(logging.INFO, logger="backend.migrations")
    database, backup_directory = _configure_cli(tmp_path, monkeypatch)
    events = []
    actual_preflight = manage_auth.read_only_preflight
    actual_backup = manage_auth.create_verified_backup
    actual_migration = manage_auth.migrate_local_owner

    def preflight(path):
        events.append("read_only_preflight")
        return actual_preflight(path)

    def backup(path, directory):
        result = actual_backup(path, directory)
        events.append("verified_backup")
        return result

    def prompt(_):
        events.append("password_prompt")
        return PASSWORD

    def migration(*args):
        events.append("migration")
        return actual_migration(*args)

    monkeypatch.setattr(manage_auth, "read_only_preflight", preflight)
    monkeypatch.setattr(manage_auth, "create_verified_backup", backup)
    monkeypatch.setattr(manage_auth, "migrate_local_owner", migration)
    monkeypatch.setattr(manage_auth.getpass, "getpass", prompt)

    assert manage_auth.main() == 0
    assert events.index("verified_backup") < events.index("password_prompt")
    assert events.index("password_prompt") < events.index("migration")
    output = capsys.readouterr().out
    assert output.index("CLI_RESULT: backup_verified") < output.index("CLI_STAGE: password_entry")
    assert f"backup_path={next(backup_directory.glob('trip_planner_pre_auth_*.sqlite3')).resolve()}" in output
    assert "backup_file_exists=PASS" in output
    assert "integrity=PASS table_counts=PASS record_ids=PASS payload_fingerprints=PASS" in output
    assert "private-backups" in output
    assert PASSWORD not in output
    for stage in (
        "sqlite_backup_api status=PASS",
        "backup_integrity status=PASS",
        "table_counts status=PASS",
        "record_ids status=PASS",
        "payload_fingerprints status=PASS",
        "BACKUP_RESULT: status=PASS",
    ):
        assert stage in caplog.text
    assert list(backup_directory.glob("*_migration_report.txt"))
    connection = sqlite3.connect(database)
    stored_hash = connection.execute("SELECT password_hash FROM local_owner").fetchone()[0]
    assert stored_hash.startswith("$argon2id$")
    assert PASSWORD not in stored_hash
    connection.close()


@pytest.mark.parametrize(
    ("field", "failed_check"),
    [
        ("snapshot_hash", "snapshot"),
        ("row_counts", "table_counts"),
        ("id_fingerprints", "record_ids"),
        ("payload_fingerprints", "payload_fingerprints"),
    ],
)
def test_password_prompt_is_unreachable_when_any_preflight_comparison_fails(
    tmp_path, monkeypatch, capsys, field, failed_check
):
    database, _ = _configure_cli(tmp_path, monkeypatch)
    actual_backup = manage_auth.create_verified_backup
    verified_backup = actual_backup(database, manage_auth._backup_directory())
    current = getattr(verified_backup, field)
    if isinstance(current, dict):
        changed = {**current, "test_mismatch": "different"}
    else:
        changed = "different"
    altered_backup = replace(verified_backup, **{field: changed})
    prompts = []
    migrations_called = []
    monkeypatch.setattr(manage_auth, "create_verified_backup", lambda *_: altered_backup)
    monkeypatch.setattr(manage_auth.getpass, "getpass", lambda *_: prompts.append(True))
    monkeypatch.setattr(
        manage_auth, "migrate_local_owner", lambda *_: migrations_called.append(True)
    )

    assert manage_auth.main() == 1
    assert prompts == []
    assert migrations_called == []
    assert f"checks={failed_check}" in capsys.readouterr().out


@pytest.mark.parametrize(
    "failed_stage",
    ["sqlite_backup_api", "integrity", "table_counts", "record_ids", "payload_fingerprints"],
)
def test_password_prompt_is_unreachable_when_internal_backup_check_fails(
    tmp_path, monkeypatch, failed_stage
):
    database, _ = _configure_cli(tmp_path, monkeypatch)
    actual_create_backup = manage_auth.create_verified_backup
    armed = {"value": False}
    prompts = []
    migrations_called = []

    def create_backup(path, directory):
        armed["value"] = True
        return actual_create_backup(path, directory)

    monkeypatch.setattr(manage_auth, "create_verified_backup", create_backup)
    if failed_stage == "sqlite_backup_api":
        original = migrations._open_database
        source_wrapped = {"value": False}

        class BackupFailureConnection:
            def __init__(self, connection):
                self._connection = connection

            def __getattr__(self, name):
                return getattr(self._connection, name)

            def backup(self, _destination):
                raise sqlite3.OperationalError("isolated backup API failure")

        def fail_backup_api(path):
            connection = original(path)
            if armed["value"] and path == database and not source_wrapped["value"]:
                source_wrapped["value"] = True
                return BackupFailureConnection(connection)
            return connection

        monkeypatch.setattr(migrations, "_open_database", fail_backup_api)
    elif failed_stage == "integrity":
        original = migrations._integrity_check
        count = {"value": 0}

        def fail_backup_integrity(connection):
            if armed["value"]:
                count["value"] += 1
                if count["value"] == 3:
                    raise migrations.MigrationSafetyError("SQLite integrity check failed.")
            return original(connection)

        monkeypatch.setattr(migrations, "_integrity_check", fail_backup_integrity)
    elif failed_stage == "table_counts":
        original = migrations._logical_snapshot
        count = {"value": 0}

        def alter_backup_counts(connection):
            result_hash, counts = original(connection)
            if armed["value"]:
                count["value"] += 1
                if count["value"] == 3:
                    counts = {**counts, "test_mismatch": 1}
            return result_hash, counts

        monkeypatch.setattr(migrations, "_logical_snapshot", alter_backup_counts)
    elif failed_stage == "record_ids":
        original = migrations._state_id_fingerprints
        count = {"value": 0}

        def alter_backup_ids(connection):
            result = original(connection)
            if armed["value"]:
                count["value"] += 1
                if count["value"] == 3:
                    result = {**result, "writes": {**result["writes"], "count": 99}}
            return result

        monkeypatch.setattr(migrations, "_state_id_fingerprints", alter_backup_ids)
    else:
        original = migrations._payload_fingerprints
        count = {"value": 0}

        def alter_backup_payloads(connection):
            result = original(connection)
            if armed["value"]:
                count["value"] += 1
                if count["value"] == 3:
                    result = {**result, "writes": "different"}
            return result

        monkeypatch.setattr(migrations, "_payload_fingerprints", alter_backup_payloads)

    monkeypatch.setattr(manage_auth.getpass, "getpass", lambda *_: prompts.append(True))
    monkeypatch.setattr(
        manage_auth, "migrate_local_owner", lambda *_: migrations_called.append(True)
    )
    assert manage_auth.main() == 1
    assert prompts == []
    assert migrations_called == []


def test_backup_failure_prevents_password_prompt_and_auth_initialization(
    tmp_path, monkeypatch
):
    database, _ = _configure_cli(tmp_path, monkeypatch)
    prompts = []
    migrations_called = []
    monkeypatch.setattr(
        manage_auth,
        "create_verified_backup",
        lambda *_: (_ for _ in ()).throw(migrations.MigrationSafetyError("backup failed")),
    )
    monkeypatch.setattr(manage_auth.getpass, "getpass", lambda *_: prompts.append(True))
    monkeypatch.setattr(manage_auth, "migrate_local_owner", lambda *_: migrations_called.append(True))

    assert manage_auth.main() == 1
    assert prompts == []
    assert migrations_called == []
    connection = sqlite3.connect(database)
    assert connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='local_owner'"
    ).fetchone() is None
    connection.close()


def test_backup_acl_failure_prevents_password_and_migration(tmp_path, monkeypatch):
    database, _ = _configure_cli(tmp_path, monkeypatch)
    prompts = []
    migrations_called = []

    def acl_failure(*_):
        raise migrations.MigrationSafetyError("backup ACL verification failed")

    monkeypatch.setattr(migrations, "_prepare_windows_backup_directory", acl_failure)
    monkeypatch.setattr(
        manage_auth,
        "create_verified_backup",
        lambda database_path, backup_directory: migrations._prepare_windows_backup_directory(
            backup_directory
        ),
    )
    monkeypatch.setattr(manage_auth.getpass, "getpass", lambda *_: prompts.append(True))
    monkeypatch.setattr(
        manage_auth, "migrate_local_owner", lambda *_: migrations_called.append(True)
    )

    assert manage_auth.main() == 1
    assert prompts == []
    assert migrations_called == []
    connection = sqlite3.connect(database)
    assert connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='local_owner'"
    ).fetchone() is None
    connection.close()


def test_active_application_detection_stops_cli_before_backup_or_prompt(
    tmp_path, monkeypatch
):
    database, _ = _configure_cli(tmp_path, monkeypatch)
    calls = []

    def active(*_):
        calls.append("application_check")
        raise migrations.MigrationSafetyError("active application detected")

    monkeypatch.setattr(manage_auth, "assert_application_stopped", active)
    monkeypatch.setattr(manage_auth, "create_verified_backup", lambda *_: calls.append("backup"))
    monkeypatch.setattr(manage_auth.getpass, "getpass", lambda *_: calls.append("prompt"))

    assert manage_auth.main() == 1
    assert calls == ["application_check"]
    connection = sqlite3.connect(database)
    assert connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='local_owner'"
    ).fetchone() is None
    connection.close()


def test_listener_on_configured_port_is_detected(monkeypatch, tmp_path):
    database = tmp_path / "planner.sqlite3"
    database.touch()
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    monkeypatch.setattr(
        migrations,
        "_application_process_snapshot",
        lambda *_: migrations.ApplicationProcessSnapshot(999, (999,), ()),
    )
    try:
        with pytest.raises(migrations.MigrationSafetyError, match="listening"):
            migrations.assert_application_stopped(database, listener.getsockname()[1])
    finally:
        listener.close()


def test_application_process_candidate_reports_sanitized_identity_and_match_reason(
    monkeypatch, tmp_path, caplog
):
    database = tmp_path / "planner.sqlite3"
    database.touch()
    candidate = migrations.ApplicationProcessCandidate(
        pid=27296,
        parent_pid=1000,
        executable="python.exe",
        creation_time="20261122170000.000000+000",
        match_reasons=("manage_auth_token",),
        recheck_status="persistent",
        rechecked_creation_time="20261122170000.000000+000",
    )
    monkeypatch.setattr(
        migrations,
        "_application_process_snapshot",
        lambda *_: migrations.ApplicationProcessSnapshot(999, (999,), (candidate,)),
    )

    with pytest.raises(migrations.MigrationSafetyError) as error:
        migrations.assert_application_stopped(database, 0)

    message = str(error.value)
    assert "pid=27296" in message
    assert "parent_pid=1000" in message
    assert "executable=python.exe" in message
    assert "matched=manage_auth_token" in message
    assert "CommandLine" not in message
    assert "Application process candidate pid=27296" in caplog.text


def test_unrelated_test_process_can_match_broad_manage_auth_substring():
    command_line = r"python.exe -m pytest tests\test_manage_auth.py -q"

    assert migrations._process_match_reasons(command_line) == ("manage_auth_token",)
    assert "manage_auth" in migrations._WINDOWS_PROCESS_SCAN


def test_verified_runner_cli_tree_excludes_only_exact_invocation_descendants():
    rows = [
        migrations.ProcessSnapshotEntry(10, 1, "python.exe", "runner.exe", "r", "100", (), "unclassified"),
        migrations.ProcessSnapshotEntry(20, 10, "python.exe", "cli.exe", "c", "200", ("manage_auth_token",), "auth_cli_module"),
        migrations.ProcessSnapshotEntry(30, 20, "python.exe", "cli-helper.exe", "h", "300", ("manage_auth_token",), "auth_cli_module"),
        migrations.ProcessSnapshotEntry(40, 1, "python.exe", "other.exe", "o", "150", ("manage_auth_token",), "auth_cli_module"),
    ]

    verified = migrations._verified_invocation_identities(rows, 20, "200", 10, "100")

    assert verified == {(10, "100"), (20, "200"), (30, "300")}
    assert (40, "150") not in verified


def test_runner_pid_reuse_fails_closed_instead_of_excluding_process():
    rows = [
        migrations.ProcessSnapshotEntry(10, 1, "python.exe", "reused.exe", "new", "999", (), "unclassified"),
        migrations.ProcessSnapshotEntry(20, 10, "python.exe", "cli.exe", "c", "200", (), "auth_cli_module"),
    ]

    with pytest.raises(migrations.MigrationSafetyError, match="Runner PID/creation identity"):
        migrations._verified_invocation_identities(rows, 20, "200", 10, "100")


def test_missing_parent_identity_fails_closed_for_runner_invocation():
    rows = [
        migrations.ProcessSnapshotEntry(10, 1, "python.exe", "runner.exe", "r", "100", (), "unclassified"),
        migrations.ProcessSnapshotEntry(20, 99, "python.exe", "cli.exe", "c", "200", (), "auth_cli_module"),
    ]

    with pytest.raises(migrations.MigrationSafetyError, match="parent identity is missing"):
        migrations._verified_invocation_identities(rows, 20, "200", 10, "100")


@pytest.mark.parametrize(
    ("recheck_exists", "recheck_creation", "expected_state"),
    [
        (True, "20261122170000.000000+000", "persistent"),
        (False, None, "disappeared"),
        (True, "20261122180000.000000+000", "pid_reused"),
    ],
)
def test_windows_snapshot_preserves_short_lived_and_persistent_candidate_states(
    recheck_exists, recheck_creation, expected_state
):
    candidate = _process_row(
        34020,
        45276,
        "python.exe",
        300,
        ("manage_auth_token",),
        "auth_test_script",
    )
    result = _process_tree_snapshot([candidate])
    if not recheck_exists:
        result["snapshots"][1]["processes"] = [
            row for row in result["snapshots"][1]["processes"] if row["pid"] != 34020
        ]
    elif recheck_creation == "20261122170000.000000+000":
        result["snapshots"][1]["processes"][-1]["creation_filetime"] = "300"
    else:
        result["snapshots"][1]["processes"][-1]["creation_filetime"] = "400"

    snapshot = migrations._parse_windows_process_snapshot(json.dumps(result))

    assert snapshot.current_cli_pid == 34000
    assert snapshot.excluded_pids == (33000, 34000)
    assert snapshot.runner_pid == 33000
    assert snapshot.invocation_verified
    assert snapshot.verified_invocation_identities == ((33000, "100"), (34000, "200"))
    assert len(snapshot.candidates) == 1
    candidate = snapshot.candidates[0]
    assert candidate.pid == 34020
    assert (candidate.pid, candidate.parent_pid, candidate.executable) == (
        34020,
        45276,
        "python.exe",
    )
    assert candidate.creation_time == "dmtf-300"
    assert candidate.recheck_status == expected_state
    assert candidate.classification == "auth_test_script"
    assert snapshot.observation_mode


def test_process_snapshot_output_is_sanitized_and_omits_command_line():
    result = _process_tree_snapshot([
        _process_row(34020, 45276, "python.exe", 300, ("manage_auth_token",), "auth_test_script")
    ])
    result["snapshots"][0]["processes"][-1]["executable_path"] = r"C:\Python\python.exe"
    snapshot = migrations._parse_windows_process_snapshot(json.dumps(result))

    rendered = repr(snapshot)
    assert "manage_auth_token" in rendered
    assert "CommandLine" not in rendered
    assert "test_manage_auth.py" not in rendered
    assert snapshot.candidates[0].observations[0].executable_path == r"C:\Python\python.exe"


def test_parent_disappearance_is_tracked_independently_of_candidate():
    result = _process_tree_snapshot([
        _process_row(34020, 45276, "python.exe", 300, ("manage_auth_token",), "auth_cli_module")
    ], observation=False)
    result["snapshots"][1]["processes"] = [
        row for row in result["snapshots"][1]["processes"] if row["pid"] != 45276
    ]

    candidate = migrations._parse_windows_process_snapshot(json.dumps(result)).candidates[0]

    assert candidate.recheck_status == "persistent"
    assert candidate.parent_recheck_status == "disappeared"
    assert candidate.observations[1].parent_pid == 45276


def test_diagnostic_observer_requests_bounded_mode_without_running_auth_cli(
    monkeypatch, capsys
):
    observation = migrations.ProcessObservation(
        snapshot="initial", exists=True, executable="python.exe",
        executable_path=r"C:\Python\python.exe", creation_time="candidate-created",
        parent_pid=45276, parent_exists=False, parent_executable=None,
        parent_executable_path=None, parent_creation_time=None,
    )
    candidate = migrations.ApplicationProcessCandidate(
        pid=31852, parent_pid=45276, executable="python.exe",
        creation_time="candidate-created", match_reasons=("manage_auth_token",),
        recheck_status="persistent", classification="auth_test_script",
        observations=(observation,), parent_recheck_status="disappeared",
    )
    observed_args = []

    def snapshot(path, *, observation_mode):
        observed_args.append((path, observation_mode))
        return migrations.ApplicationProcessSnapshot(34072, (34072,), (candidate,), True)

    monkeypatch.setattr(observe_auth_processes, "_application_process_snapshot", snapshot)

    assert observe_auth_processes.main(["--observe"]) == 1

    output = capsys.readouterr().out
    assert observed_args == [(observe_auth_processes.DATABASE_PATH_FOR_MATCHING_ONLY, True)]
    assert "snapshot=initial" in output
    assert "parent_exists=False" in output
    assert "CommandLine" not in output
    assert "current_cli_pid=34072" in output


def test_disappeared_candidate_still_keeps_application_safety_gate_closed(
    monkeypatch, tmp_path, caplog
):
    database = tmp_path / "planner.sqlite3"
    database.touch()
    candidate = migrations.ApplicationProcessCandidate(
        pid=34020,
        parent_pid=45276,
        executable="python.exe",
        creation_time="20261122170000.000000+000",
        match_reasons=("manage_auth_token",),
        recheck_status="disappeared",
    )
    monkeypatch.setattr(
        migrations,
        "_application_process_snapshot",
        lambda *_: migrations.ApplicationProcessSnapshot(34000, (34000,), (candidate,)),
    )

    with pytest.raises(migrations.MigrationSafetyError, match="recheck=disappeared"):
        migrations.assert_application_stopped(database, 0)

    assert "recheck_status=disappeared" in caplog.text


def test_sqlite_writer_lock_is_detected(tmp_path):
    database = tmp_path / "planner.sqlite3"
    TripStore(database)
    writer = sqlite3.connect(database, timeout=0)
    writer.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(migrations.MigrationSafetyError, match="active database writer"):
            migrations.assert_database_quiescent(database)
    finally:
        writer.rollback()
        writer.close()


def test_configured_path_mismatch_prevents_prompt_and_backup(tmp_path, monkeypatch):
    database, _ = _configure_cli(tmp_path, monkeypatch)
    other = tmp_path / "other.sqlite3"
    other.touch()
    monkeypatch.setattr(manage_auth, "DATABASE_PATH", other)
    calls = []
    monkeypatch.setattr(manage_auth, "create_verified_backup", lambda *_: calls.append("backup"))
    monkeypatch.setattr(manage_auth.getpass, "getpass", lambda *_: calls.append("prompt"))

    assert manage_auth.main() == 1
    assert calls == []
    connection = sqlite3.connect(database)
    assert connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='local_owner'"
    ).fetchone() is None
    connection.close()
