# Phase 2E — Windows Process Snapshot Diagnostic Update

**Scope:** Process detector diagnostics only.  
**Status:** Implemented and verified with isolated tests. No authentication CLI or runner was executed.

## Changes

The Windows process scan in `backend/migrations.py` now emits a sanitized point-in-time snapshot containing the current CLI PID, excluded PID list, and candidate count, including when there are no candidates. For each candidate it records PID, parent PID, executable basename, process creation time, and exact reason labels. It never emits a command line.

Each initial candidate is re-queried by PID immediately during the same PowerShell scan. Its creation time is compared with the snapshot so the result is classified as `persistent`, `disappeared`, `pid_reused`, or `recheck_unverifiable`. The CLI reports snapshot and candidate metadata in sanitized warning/error output.

The existing conservative behavior remains: **any** initial candidate continues to block the safety gate, including a candidate that disappeared during recheck or whose PID appears reused. This task did not change the substring matching rules. An unrelated process containing `test_manage_auth.py` can still match `manage_auth_token`; until matching accuracy is independently established, it remains blocked and is now identified as such in diagnostics.

## Example diagnostic output

Persistent candidate:

```text
WARNING: Application process snapshot current_cli_pid=34000 excluded_pids=34000 candidate_count=1
WARNING: Application process candidate pid=34020 parent_pid=45276 executable=python.exe creation_time=20261122170000.000000+000 match_reasons=manage_auth_token recheck_status=persistent rechecked_creation_time=20261122170000.000000+000
WARNING: Application process candidate recheck summary: persistent=1
```

Candidate exited after initial snapshot (still blocks conservatively):

```text
WARNING: Application process candidate pid=34020 parent_pid=45276 executable=python.exe creation_time=20261122170000.000000+000 match_reasons=manage_auth_token recheck_status=disappeared rechecked_creation_time=unknown
ERROR: An application process candidate was detected (... recheck=disappeared; current_cli_pid=34000, excluded_pids=34000). The safety gate remains closed; stop it normally and retry.
```

These are format examples only; they are not live process records.

## Tests and limitations

`tests/test_manage_auth.py`: **13 passed** using temporary test paths/databases. Coverage includes a pytest command containing `tests/test_manage_auth.py`, persistent/disappeared/reused candidate snapshots, sanitized fields, and the rule that disappearance still blocks.

The tests exercise parsing and status handling with synthetic snapshot rows. The PowerShell/WMI scan itself was not executed in this task. The operating system process table can still change immediately after the recheck; the recheck narrows the observation window but cannot eliminate that race. WMI/query failures remain fail-closed through the existing safety error path.

## Exact files changed

- `backend/migrations.py`
- `tests/test_manage_auth.py`
- `evaluation/phase2e_auth_ownership/stage4b_process_snapshot_diagnostic.md`

No database or backup was accessed. No password was requested. No process was terminated. No migration behavior or schema was changed. Stage 5 was not started.
