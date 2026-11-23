"""Initialize the one local application owner after verifying a database backup."""

from __future__ import annotations

import getpass
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from argon2 import PasswordHasher

from .config import BACKEND_DIR, BACKEND_PORT, DATABASE_PATH
from .migrations import (
    MigrationSafetyError,
    assert_application_stopped,
    assert_database_quiescent,
    create_verified_backup,
    migrate_local_owner,
    read_only_preflight,
    verify_migration_preservation,
    verify_owner_migration,
)


def _backup_directory() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise MigrationSafetyError("The per-user local application data directory is unavailable.")
    return Path(local_app_data) / "TravelBudgetPlanner" / "backups"


def _expected_project_database_path() -> Path:
    return BACKEND_DIR / "data" / "travel_planner.sqlite3"


def _validate_database_path(database_path: Path) -> Path:
    configured = database_path.resolve()
    expected = _expected_project_database_path().resolve()
    if configured != expected:
        raise MigrationSafetyError(
            "Configured database path does not match the existing project database."
        )
    if not configured.is_file():
        raise MigrationSafetyError("The configured database does not exist.")
    return configured


def _backup_comparison_checks(preflight: dict[str, object], backup) -> dict[str, bool]:
    return {
        "snapshot": preflight.get("snapshot_hash") == backup.snapshot_hash,
        "table_counts": preflight.get("row_counts") == backup.row_counts,
        "record_ids": preflight.get("id_fingerprints") == backup.id_fingerprints,
        "payload_fingerprints": preflight.get("payload_fingerprints") == backup.payload_fingerprints,
    }


def _write_migration_report(backup, outcome: str, owner_verification: dict[str, int]) -> Path:
    report_path = backup.backup_path.with_name(
        backup.backup_path.stem + "_migration_report.txt"
    )
    lines = [
        "Phase 2E local authentication migration report",
        f"Timestamp (UTC): {datetime.now(timezone.utc).isoformat()}",
        f"Database: {backup.database_path.resolve()}",
        f"Verified backup: {backup.backup_path.resolve()}",
        f"Verified snapshot sha256: {backup.snapshot_hash}",
        "Backup integrity and source/backup snapshot comparison: PASS",
        f"Outcome: {outcome}",
        "Post-migration integrity and persisted-state preservation: PASS",
        "Authentication owner/session schema verification: PASS",
        f"Owner rows: {owner_verification['owner_rows']}",
        f"Ownership mappings: {owner_verification['ownership_rows']}",
        f"Session rows: {owner_verification['session_rows']}",
        f"Migration version: {owner_verification['migration_version']}",
        "",
        "Verified row counts:",
    ]
    lines.extend(f"  {table}: {count}" for table, count in sorted(backup.row_counts.items()))
    lines.append("\nVerified unique ID counts and fingerprints:")
    for table, details in sorted(backup.id_fingerprints.items()):
        lines.append(f"  {table}:")
        for identifier, value in sorted(details.items()):
            lines.append(f"    {identifier}={value}")
    lines.append("\nVerified payload fingerprints by table:")
    lines.extend(f"  {table}: sha256={fingerprint}" for table, fingerprint in sorted(backup.payload_fingerprints.items()))
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if os.name != "nt":
        report_path.chmod(0o600)
    return report_path


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    stage = "preflight"
    try:
        print("CLI_STAGE: preflight", flush=True)
        database_path = _validate_database_path(DATABASE_PATH)
        assert_application_stopped(database_path, BACKEND_PORT)
        assert_database_quiescent(database_path)
        preflight = read_only_preflight(database_path)
        stage = "backup"
        print("CLI_STAGE: backup", flush=True)
        try:
            backup = create_verified_backup(database_path, _backup_directory())
        except Exception:
            print("CLI_RESULT: backup_failure", flush=True)
            raise
        matching_checks = _backup_comparison_checks(preflight, backup)
        failed_checks = [name for name, passed in matching_checks.items() if not passed]
        if failed_checks:
            print(
                "CLI_RESULT: backup_failure preflight_comparison=FAIL checks="
                + ",".join(failed_checks),
                flush=True,
            )
            raise MigrationSafetyError("Backup verification did not match the read-only preflight.")
        # Recheck after backup creation and again before migration. No password
        # prompt occurs until the verified backup is complete.
        assert_application_stopped(database_path, BACKEND_PORT)
        assert_database_quiescent(database_path)
        if not backup.backup_path.is_file():
            print("CLI_RESULT: backup_failure backup_file_exists=FAIL", flush=True)
            raise MigrationSafetyError("The verified backup file is missing before password entry.")
        print(
            "CLI_RESULT: backup_verified "
            f"backup_path={backup.backup_path.resolve()} backup_file_exists=PASS "
            "integrity=PASS table_counts=PASS record_ids=PASS payload_fingerprints=PASS "
            "preflight_comparison=PASS",
            flush=True,
        )
    except MigrationSafetyError as exc:
        category = "backup_failure" if stage == "backup" else "preflight_failure"
        print(f"CLI_RESULT: {category}", flush=True)
        print(f"Authentication migration stopped before password entry: {exc}", flush=True)
        return 1

    print("CLI_STAGE: password_entry", flush=True)
    password = getpass.getpass("Create the local application password (minimum 12 characters): ")
    confirmation = getpass.getpass("Confirm password: ")
    if len(password) < 12:
        print("Password must contain at least 12 characters.")
        return 2
    if password != confirmation:
        print("Passwords do not match.")
        return 2
    owner_id = str(uuid.uuid4())
    password_hash = PasswordHasher().hash(password)
    try:
        print("CLI_STAGE: migration", flush=True)
        assert_application_stopped(database_path, BACKEND_PORT)
        assert_database_quiescent(database_path)
        changed = migrate_local_owner(backup, owner_id, password_hash)
        verify_migration_preservation(database_path, backup)
        owner_verification = verify_owner_migration(database_path, backup)
    except MigrationSafetyError as exc:
        print("CLI_RESULT: migration_failure", flush=True)
        print(f"Authentication migration stopped; see verified backup: {backup.backup_path}. {exc}")
        return 1
    outcome = "MIGRATED" if changed else "ALREADY_INITIALIZED; no password or ownership changes made"
    report_path = _write_migration_report(backup, outcome, owner_verification)
    if changed:
        print("CLI_RESULT: normal_exit", flush=True)
        print(f"Local authentication initialized. Migration report: {report_path}")
    else:
        print("CLI_RESULT: normal_exit", flush=True)
        print(f"Local authentication is already initialized; no records were changed. Report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
