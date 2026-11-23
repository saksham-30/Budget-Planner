# Phase 2E — Process Candidate Observation Mode

**Scope:** Process diagnostics only.  
**Status:** Implemented and tested. Stage 4B runner and authentication CLI were not executed.

## Findings

The process detector now retains a sanitized Windows snapshot at candidate detection. It records the detector's current PID and explicit excluded PID list. For each initial candidate it records candidate PID, parent PID, executable name and path, process creation time, existing sanitized match reasons, and a category-only script/module classification. The classifier uses command-line text only in memory; no command-line text, arguments, or environment variables are emitted.

The process and its parent are checked in each snapshot. Parent PID, existence, executable name/path, and creation time are captured. Recheck metadata distinguishes a persistent process, disappearance, PID reuse, and an unverifiable process identity. Parent metadata is independently compared so parent disappearance, reuse, or change is visible.

The existing matching rules and fail-closed behavior are unchanged. Any process matching the initial scan still blocks, even if it disappears before recheck or the PID is reused. No intentional sleep or wait was added. Normal checks take the initial snapshot and immediate recheck; optional observation adds one immediate follow-up snapshot, for a maximum of three bounded snapshots.

`scripts/observe_auth_processes.py --observe` is a separate process-only diagnostic. It passes the configured project DB path as a string used only for process-command matching; it does not open SQLite, import the authentication CLI, create a backup, or migrate. It is safer for evidence gathering than invoking the setup runner because it cannot continue into password entry or migration.

## Example output

Synthetic format example only:

```text
Application process snapshot current_cli_pid=5100 excluded_pids=5100 candidate_count=1 observation_mode=True
Application process candidate pid=31852 parent_pid=42748 executable=python.exe creation_time=20261122230659.000000+000 executable_path=C:\Python\python.exe classification=auth_test_script match_reasons=manage_auth_token recheck_status=disappeared rechecked_creation_time=unknown parent_recheck_status=persistent
Process observation candidate_pid=31852 snapshot=initial exists=True executable=python.exe executable_path=C:\Python\python.exe creation_time=20261122230659.000000+000 parent_pid=42748 parent_exists=True parent_executable=powershell.exe parent_executable_path=C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe parent_creation_time=...
Process observation candidate_pid=31852 snapshot=recheck exists=False executable=unknown executable_path=unknown creation_time=unknown parent_pid=42748 parent_exists=True parent_executable=powershell.exe parent_executable_path=... parent_creation_time=...
Process observation candidate_pid=31852 snapshot=followup exists=False executable=unknown executable_path=unknown creation_time=unknown parent_pid=42748 parent_exists=False parent_executable=unknown parent_executable_path=unknown parent_creation_time=unknown
```

## Safe diagnostic procedure

In a normal PowerShell terminal at the project root, create a timestamped log and run only the process observer:

```powershell
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$log = ".\evaluation\phase2e_auth_ownership\process_observation_$stamp.log"
& .\venv\Scripts\python.exe .\scripts\observe_auth_processes.py --observe 2>&1 | Tee-Object -FilePath $log
$observerExitCode = $LASTEXITCODE
"Observer exit code: $observerExitCode"
```

This does not invoke `backend.manage_auth`. Preserve the diagnostic log if a candidate appears. Review candidate and parent identities across `initial`, `recheck`, and `followup`; no process should be terminated by this procedure.

The equivalent module invocation is also supported from the project root:

```powershell
& .\venv\Scripts\python.exe -m scripts.observe_auth_processes --observe
```

## Tests and limitations

`tests/test_manage_auth.py`: **15 passed** using isolated temporary test data. Tests cover the broad `test_manage_auth.py` match classification, short-lived candidate disappearance, PID reuse, parent disappearance, sanitized output, and the process-only observer's opt-in mode. The embedded PowerShell scan passed PowerShell parser syntax validation; it was parsed but not executed.

The tests exercise parsing and reconciliation using synthetic snapshots; the PowerShell/WMI script was not executed during this task. Process state can change after the final snapshot, so the observations narrow the timing window but cannot prove a process remains absent later. WMI failures continue to stop the process gate.

## Exact files changed

- `backend/migrations.py`
- `scripts/observe_auth_processes.py`
- `tests/test_manage_auth.py`
- `evaluation/phase2e_auth_ownership/stage4b_process_observation_mode.md`

No real database or backup was accessed. No password was requested. No process was terminated. No schema or migration behavior was changed. Stage 5 was not started.
