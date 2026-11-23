# Phase 2E — Stage 4B Runner Failure Diagnostic

**Investigation:** Read-only  
**Date:** 2026-11-22  
**Outcome:** Exact CLI failure point is unconfirmed; no migration was observed or executed during this investigation.

## Findings

### CLI order and backup gate

`backend/manage_auth.py` runs configured-path validation, application and SQLite-writer checks, read-only preflight, backup creation, source/backup fingerprint comparison, then repeats quiescence checks. Only after those steps does it print the verified-backup message and call `getpass.getpass` twice. Migration starts after password validation and hashing. A caught `MigrationSafetyError` in the pre-prompt block prints a sanitized stop message and returns status 1.

`backend/migrations.py` uses `sqlite3.Connection.backup()` and checks integrity, logical snapshot, row counts, persisted IDs, and payload fingerprints. The current CLI has no durable per-stage log or runner result record. Consequently, if its visible terminal output was not captured, later inspection cannot identify which pre-prompt gate failed.

### Where execution stopped

The strongest supported conclusion is that the wrapper process outlived its CLI child and was left waiting at the wrapper's final `Read-Host`. The previous Stage 4B report records that no new Stage 4B backup or migration report appeared, and the subsequent read-only state check found auth/migration tables absent and legacy row counts unchanged. Therefore migration did not complete. Those observations do **not** distinguish a process/path/preflight failure, a backup-directory or backup-verification failure, or a CLI launch/terminal interaction failure.

No sanitized CLI stdout/stderr or Python child exit code was retained. The root cause of CLI non-completion is therefore **unknown**, not proven to be a specific migration or backup defect. A current read-only process check found no process with the former runner PID 35396 and no port 8000 listener. It does not establish what happened inside the earlier transient CLI process.

### Final prompt purpose

The runner script used for the attempt invoked `python -m backend.manage_auth`, saved `$LASTEXITCODE`, printed `Authentication CLI exit code: ...`, and then executed `Read-Host 'Press Enter to close this window'`. That final prompt is a **pause to keep the visible console open** so its preceding output can be read. It is not a password confirmation, migration approval, backup verification, or cleanup operation. It explains why PowerShell PID 35396 could remain after Python exited. The present investigation did not send input to or terminate that process; it is no longer present.

### Backup artifact evidence

The previous Stage 4B report states the backup directory contained only the older file `trip_planner_pre_phase2e_20261122_174908.sqlite3` and no timestamped Stage 4B `pre_auth` backup. During this investigation, access to `%LOCALAPPDATA%\TravelBudgetPlanner\backups` was denied by the current sandbox, so the directory could not be independently re-enumerated. No backup was created, opened, changed, or deleted in this investigation. No backup file was overwritten.

### Pytest temporary-directory cleanup failures

Both `_phase2e_stage4b_tests` and `_phase2e_stage4b_tests_final` remain. Their top-level ACLs include Modify permission for the current user and Codex sandbox group, but an earlier recursive cleanup returned `Access to the path ...\pytest is denied`. Read-only enumeration shows these are pytest basetemp artifact trees. There is no evidence from the recorded error that identifies whether denial came from a nested child ACL, sandbox policy, or a transient file handle. The precise cleanup cause is therefore **unknown**. No cleanup was retried, and no directory was removed or altered.

## Safe retry procedure

1. Close any prior runner window through its normal UI if one reappears; do not kill the process. Confirm port 8000 is unbound and inspect sanitized process names/parent-child relationships for the app and Python CLI. Close SQLite GUI tools and other writers normally.
2. Re-run the read-only path, integrity, migration-state, and quiescence checks. Do not proceed if any check fails or process inspection is inconclusive.
3. Invoke the documented CLI from a terminal that remains visible and captures its sanitized stdout/stderr and exit code. Do not place a password in command arguments, scripts, environment variables, logs, or chat. Preserve the CLI's final exit status separately from any terminal pause.
4. Confirm that the CLI reports a verified backup path and successful source/backup verification before entering either password prompt. If it stops, retain the sanitized error and stop; do not retry by bypassing gates.
5. After a future authorized run, compare its resulting migration report and backup verification evidence before proceeding to any later stage.

## Runner observability follow-up

The investigation did not recover a specific underlying CLI failure; the earlier root cause remains unknown. The runner issue itself is now addressed: `scripts/stage4b_auth_runner.py` resolves the project virtual-environment interpreter, inherits the interactive console for secure `getpass` entry, mirrors stdout/stderr while writing timestamped logs, omits child arguments, redacts common secret/hash patterns, records a classification and the child's exit code as soon as it exits, and returns that same exit code. It has no pause by default. `backend.manage_auth` now emits non-secret stage/result markers so preflight, backup, migration, and normal outcomes are distinguishable. A traceback takes precedence as a Python exception classification.

For a later, separately authorized retry, open a normal interactive PowerShell terminal at the project root and run:

```powershell
.\venv\Scripts\python.exe .\scripts\stage4b_auth_runner.py
```

Use the visible terminal for any secure password prompts. The runner displays those prompts from the child but never reads the entered password; it does not put arguments in its log and does not pause after completion. Review its printed log path and exit code before deciding on any further action.

## Process-detection follow-up

### Detector behavior

`backend.manage_auth.main()` calls `assert_application_stopped()` before read-only preflight. That function checks loopback listeners on the configured port, then calls `_application_process_ids()`.

On Windows, `_application_process_ids()` asks `Get-CimInstance Win32_Process` for process rows. It selects a row only when:

- executable name is `python.exe`, `pythonw.exe`, or `uvicorn.exe` (case-insensitive; optional `.exe` handling is explicit in the expression);
- PID is not the current CLI Python PID; and
- command line contains `uvicorn`, `backend.main`, `manage_auth`, or the configured absolute database path.

Matching is substring/regex matching, not parsed command-line arguments. The code does not inspect parent/child relationships, open SQLite handles, or verify a writer lock at this stage. It performs one process snapshot; it does not retain a before/after snapshot or process creation time. The separate SQLite writer probe runs only after this app-process check passes. The Linux implementation similarly checks candidate Python process names and command-line substrings, excluding only the current PID.

### Evidence and conclusion for PID 27296

The retained runner log `stage4b_auth_runner_20261122T165954_126334Z.log` records `CLI_STAGE: preflight`, the sanitized preflight failure with PID 27296, and child exit code 1. It does not contain the detector's raw snapshot, parent PID, executable, or matching token. Subsequent inspection found no current Win32 process record for PID 27296 and no listener on port 8000. A fresh sanitized Python/Uvicorn process listing returned no candidate rows.

Therefore the PID could have exited between the process snapshot and later manual inspection. PID reuse is also theoretically possible, but there is no recorded creation time or earlier process identity to establish it. The historical evidence does not prove either case, does not identify which substring matched, and cannot establish that PID 27296 was a database writer or an unrelated Python process. **Root cause / false-positive status: unknown.** The detector is an application-command heuristic, not proof of active database writing.

### Diagnostic improvement

`backend/migrations.py` now preserves the existing matching criteria but returns structured candidate metadata and emits only PID, parent PID, executable basename, and sanitized match-reason labels (`uvicorn_token`, `backend_main_token`, `manage_auth_token`, `configured_database_path_reference`). It never logs the full command line. The CLI stop message also includes those sanitized fields. A focused isolated test verifies the metadata and ensures command-line text is absent. No false positive was proven, so matching policy was not weakened or otherwise changed.

Focused test run after this diagnostic change: `tests/test_manage_auth.py` — **7 passed**, using temporary test databases/paths only. The report update itself did not access SQLite.

## PID 34020 / `manage_auth_token` follow-up

### Meaning of the label

`manage_auth_token` is a diagnostic reason label, not a credential or an application token. The Windows scan assigns it when the process's raw `Win32_Process.CommandLine` contains the case-insensitive substring `manage_auth`. The label does not distinguish a module invocation from a script filename or an unrelated argument. In this project, legitimate occurrences include the runner's child command `-m backend.manage_auth` and the test module `tests/test_manage_auth.py`.

The detector is configured to exclude the CLI process itself using `PHASE2E_CURRENT_PID`, populated from that CLI process's `os.getpid()`. It does not exclude its parent or children and does not query environment variables of candidate processes. Parent PID is included only as descriptive metadata; it is not part of the selection rule.

### Evidence and confidence

The latest retained runner log, `stage4b_auth_runner_20261122T172409_199141Z.log`, records candidate PID 34020, parent PID 45276, executable `python.exe`, reason `manage_auth_token`, and exit code 1 before password entry. It has no full command line (intentionally), process creation time, or scan-excluded PID. Subsequent `Get-CimInstance` queries found no current records for either PID; no Python/Uvicorn process rows were returned in the fresh sanitized listing. This is consistent with a short-lived process exiting before manual inspection, but does not identify what command line it had.

A concrete false-positive mechanism exists in the detector: an unrelated Python test invocation whose command line contains `tests/test_manage_auth.py` satisfies the `manage_auth` substring test. A separate concurrent `backend.manage_auth` invocation would also match by design. However, the retained evidence cannot show which command line PID 34020 had. Its parent PID alone is insufficient to identify the process. Whether this specific candidate was a test process, another authentication CLI, a helper, or something unrelated remains **unknown**. Confidence that the matcher is overly broad is **high**; confidence in the specific root cause of PID 34020 is **low / unproven**.

No regression test or detector behavior change was made in this investigation because the historical candidate's actual command line is unavailable; a test asserting the known substring behavior would not establish the identity or root cause of PID 34020. To preserve the next attempt's privacy while resolving the ambiguity, the minimal diagnostic follow-up is to log the scan's excluded/current PID and each candidate's creation time along with the existing sanitized PID, parent, executable, and match-reason fields. Do not log command lines. If a future log shows the candidate PID equals the excluded/current CLI PID, treat that as a detector defect and stop before any migration work.

No Stage 4B runner or CLI was executed for this investigation. No password was requested, and no database or backup was accessed. No process was terminated. The safe next action is to use a diagnostic-only process snapshot that captures the excluded/current PID and process creation time, then rerun only the process inspection; do not retry migration until the candidate is identified and the app/writer-stop gate passes.

## Scope and changes

- Reviewed `backend/manage_auth.py`, `backend/migrations.py`, prior Stage 4A/4B reports, and the prior runner description.
- Changed `scripts/stage4b_auth_runner.py` to provide observable, secret-conscious launch/stream/exit handling.
- Changed `backend/manage_auth.py` only to emit sanitized stage/result markers; migration logic and schema were not changed.
- Added isolated coverage in `tests/test_stage4b_auth_runner.py` for exit propagation, launch failure, redaction/argument omission, and stage classification.
- Updated this report with the follow-up and safe retry command.
- Focused tests: 4 passed using a temporary test directory.
- No `.env` contents were inspected. No database was opened or modified. No backup was created, inspected, or deleted. No password was requested or handled by the runner. The test child printed synthetic redaction fixtures only. No migration was executed. No process was terminated, and no pytest temp folder was deleted. Stage 5 was not started.
