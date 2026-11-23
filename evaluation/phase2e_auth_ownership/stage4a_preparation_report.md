# Phase 2E — Stage 4A Preparation Report

**Date: 2026-11-22**

## Outcome

Updated the secure setup workflow and ran focused tests only against temporary databases. The configured production/project database was not opened, backed up, migrated, or modified. The setup CLI was not run.

## Workflow safeguards added

1. The CLI validates that the configured database resolves exactly to the existing project database path and that the file exists.
2. It checks the configured backend port, scans process arguments for Uvicorn/backend/database use, and fails closed if process inspection fails. It also probes SQLite for a current writer using a rolled-back `BEGIN IMMEDIATE` transaction.
3. It performs read-only integrity, row-count, ID, and payload-fingerprint preflight before creating a timestamped backup with SQLite's backup API.
4. It compares the read-only preflight against the backed-up database, including unique IDs from preferences, conversations, checkpoints, and writes and fingerprints for persisted payload tables.
5. It repeats application/writer checks after backup and immediately before migration. The password prompt is reached only after backup verification succeeds.
6. Migration verifies the persisted-state fingerprints before commit; CLI performs a read-only integrity and preservation check afterward. The report is written beside the verified backup and includes the backup path, counts, fingerprints, and outcome.
7. Existing migration state is checked and left unchanged on repeat execution; a new candidate owner UUID/password hash cannot overwrite the stored owner, password, or mappings.

## Focused test results

Command used an isolated `TRAVEL_PLANNER_DATABASE` and pytest `--basetemp` under the workspace:

`python -m pytest -p no:cacheprovider --basetemp <workspace-temp> tests/test_manage_auth.py tests/test_auth_migration.py tests/test_auth_security.py -q`

**15 passed.** Coverage includes password-prompt ordering, backup failure before prompt/initialization, wrong configured path, active service/listener handling, SQLite writer detection, migration rollback, repeat execution without overwriting owner/password/mappings, session/CSRF protections, and byte-preservation of checkpoint/write payloads.

Temporary test directories were removed after the run. No frontend tests or build were needed for this backend workflow change.

## Safe manual shutdown

1. In the terminal running Uvicorn, press `Ctrl+C` and wait for the process to exit normally.
2. Check the configured port (default 8000) with `Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction SilentlyContinue`; no output means there is no listener on that port.
3. Close any SQLite GUI or other program that may write to the database.
4. Run `python -m backend.manage_auth` from the project root. The CLI will repeat its process and SQLite-writer checks and stop without prompting if it cannot prove the application is stopped.

Do not terminate Python processes by PID. The prior Stage 4 report found Uvicorn on port 8000 (PIDs 35520 and 12488); this Stage 4A task did not recheck or stop those processes.

## Remaining blocker and status

The real migration is still pending. Before Stage 4 can proceed, the operator must stop the previously observed backend normally and rerun the CLI after confirming the service and database writer checks pass. No backup location or migration outcome exists yet. Do not proceed to Stage 5 until the resulting migration report has been reviewed.

## Exact files changed

- `backend/manage_auth.py`
- `backend/migrations.py`
- `tests/test_auth_migration.py`
- `tests/test_manage_auth.py`
- `evaluation/phase2e_auth_ownership/implementation_report.md`
- `evaluation/phase2e_auth_ownership/stage4a_preparation_report.md`
