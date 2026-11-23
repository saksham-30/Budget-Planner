"""Verified-backup-gated migration for the single local application owner."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .process_identity import current_process_creation_filetime

logger = logging.getLogger(__name__)

OWNER_MIGRATION_VERSION = 1
_STATE_TABLES = ("trip_preferences", "trip_conversations", "checkpoints", "writes")
_WINDOWS_ACL_DIRECTORY_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$directory = [System.IO.Path]::GetFullPath($env:PHASE2E_BACKUP_DIR)
$missingDirectories = [System.Collections.Generic.List[string]]::new()
$cursor = $directory
while (-not [System.IO.Directory]::Exists($cursor)) {
  $missingDirectories.Add($cursor)
  $parent = [System.IO.Directory]::GetParent($cursor)
  if ($null -eq $parent) { throw 'No existing parent for backup directory.' }
  $cursor = $parent.FullName
}
$currentSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
$sids = @(
  $currentSid,
  [System.Security.Principal.SecurityIdentifier]::new('S-1-5-18'),
  [System.Security.Principal.SecurityIdentifier]::new('S-1-5-32-544')
)
$expectedInheritance = [System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [System.Security.AccessControl.InheritanceFlags]::ObjectInherit
for ($index = $missingDirectories.Count - 1; $index -ge 0; $index--) {
  $security = [System.Security.AccessControl.DirectorySecurity]::new()
  $security.SetAccessRuleProtection($true, $false)
  foreach ($sid in $sids) {
    $rule = [System.Security.AccessControl.FileSystemAccessRule]::new(
      $sid,
      [System.Security.AccessControl.FileSystemRights]::FullControl,
      $expectedInheritance,
      [System.Security.AccessControl.PropagationFlags]::None,
      [System.Security.AccessControl.AccessControlType]::Allow
    )
    [void]$security.AddAccessRule($rule)
  }
  [void][System.IO.Directory]::CreateDirectory($missingDirectories[$index], $security)
}

function Assert-BackupDirectoryAcl([string]$target, $expectedSids, $currentSid, $expectedInheritance) {
  # Read-only validation. Never call Set-Acl on any backup directory.
  $verified = Get-Acl -LiteralPath $target
  $actualSids = @($verified.Access | ForEach-Object {
    $_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value
  })
  if (-not $verified.AreAccessRulesProtected -or
      $verified.GetOwner([System.Security.Principal.SecurityIdentifier]).Value -ne $currentSid.Value -or
      $verified.Access.Count -ne 3 -or
      (@($actualSids | Sort-Object) -join ',') -ne (@($expectedSids | Sort-Object) -join ',')) {
    throw 'Backup ACL verification failed.'
  }
  foreach ($rule in $verified.Access) {
    if ($rule.AccessControlType -ne [System.Security.AccessControl.AccessControlType]::Allow -or
        $rule.FileSystemRights -ne [System.Security.AccessControl.FileSystemRights]::FullControl -or
        $rule.IsInherited -or
        $rule.InheritanceFlags -ne $expectedInheritance -or
        $rule.PropagationFlags -ne [System.Security.AccessControl.PropagationFlags]::None) {
      throw 'Backup ACL verification failed.'
    }
  }
}
$expectedSids = @($sids | ForEach-Object { $_.Value })
foreach ($createdDirectory in $missingDirectories) {
  Assert-BackupDirectoryAcl $createdDirectory $expectedSids $currentSid $expectedInheritance
}
Assert-BackupDirectoryAcl $directory $expectedSids $currentSid $expectedInheritance
"""

_WINDOWS_ACL_FILE_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$file = $env:PHASE2E_BACKUP_FILE
$currentSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
$sids = @(
  $currentSid,
  [System.Security.Principal.SecurityIdentifier]::new('S-1-5-18'),
  [System.Security.Principal.SecurityIdentifier]::new('S-1-5-32-544')
)
$acl = Get-Acl -LiteralPath $file
if ($acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value -ne $currentSid.Value) {
  throw 'Backup ACL verification failed.'
}
$acl.SetAccessRuleProtection($true, $false)
foreach ($sid in $sids) {
  $rule = [System.Security.AccessControl.FileSystemAccessRule]::new(
    $sid,
    [System.Security.AccessControl.FileSystemRights]::FullControl,
    [System.Security.AccessControl.InheritanceFlags]::None,
    [System.Security.AccessControl.PropagationFlags]::None,
    [System.Security.AccessControl.AccessControlType]::Allow
  )
  [void]$acl.AddAccessRule($rule)
}
Set-Acl -LiteralPath $file -AclObject $acl
$verified = Get-Acl -LiteralPath $file
$actualSids = @($verified.Access | ForEach-Object {
  $_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value
})
$expectedSids = @($sids | ForEach-Object { $_.Value })
if (-not $verified.AreAccessRulesProtected -or
    $verified.GetOwner([System.Security.Principal.SecurityIdentifier]).Value -ne $currentSid.Value -or
    $verified.Access.Count -ne 3 -or
    (@($actualSids | Sort-Object) -join ',') -ne (@($expectedSids | Sort-Object) -join ',')) {
  throw 'Backup ACL verification failed.'
}
foreach ($rule in $verified.Access) {
  if ($rule.AccessControlType -ne [System.Security.AccessControl.AccessControlType]::Allow -or
      $rule.FileSystemRights -ne [System.Security.AccessControl.FileSystemRights]::FullControl -or
      $rule.IsInherited -or
      $rule.InheritanceFlags -ne [System.Security.AccessControl.InheritanceFlags]::None -or
      $rule.PropagationFlags -ne [System.Security.AccessControl.PropagationFlags]::None) {
    throw 'Backup ACL verification failed.'
  }
}
"""


class MigrationSafetyError(RuntimeError):
    """The database or backup could not be verified safely."""


@dataclass(frozen=True)
class ProcessSnapshotEntry:
    pid: int
    parent_pid: int | None
    executable: str
    executable_path: str | None
    creation_time: str | None
    creation_filetime: str | None
    match_reasons: tuple[str, ...]
    classification: str


@dataclass(frozen=True)
class ProcessObservation:
    snapshot: str
    exists: bool
    executable: str | None
    executable_path: str | None
    creation_time: str | None
    parent_pid: int | None
    parent_exists: bool
    parent_executable: str | None
    parent_executable_path: str | None
    parent_creation_time: str | None


@dataclass(frozen=True)
class ApplicationProcessCandidate:
    pid: int
    parent_pid: int | None
    executable: str
    creation_time: str | None
    match_reasons: tuple[str, ...]
    recheck_status: str
    rechecked_creation_time: str | None = None
    classification: str = "unclassified_match"
    observations: tuple[ProcessObservation, ...] = ()
    parent_recheck_status: str = "unknown"


@dataclass(frozen=True)
class ApplicationProcessSnapshot:
    current_cli_pid: int
    excluded_pids: tuple[int, ...]
    candidates: tuple[ApplicationProcessCandidate, ...]
    observation_mode: bool = False
    runner_pid: int | None = None
    invocation_verified: bool = False
    verified_invocation_identities: tuple[tuple[int, str], ...] = ()
    current_cli_creation_filetime: str | None = None
    runner_creation_filetime: str | None = None


@dataclass(frozen=True)
class VerifiedBackup:
    database_path: Path
    backup_path: Path
    snapshot_hash: str
    row_counts: dict[str, int]
    id_fingerprints: dict[str, dict[str, str | int]] = field(default_factory=dict)
    payload_fingerprints: dict[str, str] = field(default_factory=dict)


def _open_database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=10.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _integrity_check(connection: sqlite3.Connection) -> None:
    rows = connection.execute("PRAGMA integrity_check").fetchall()
    if len(rows) != 1 or rows[0][0] != "ok":
        raise MigrationSafetyError("SQLite integrity check failed.")


def _logical_snapshot(connection: sqlite3.Connection) -> tuple[str, dict[str, int]]:
    tables = connection.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    ).fetchall()
    digest = hashlib.sha256()
    counts: dict[str, int] = {}
    for table_row in tables:
        table = table_row["name"]
        if not table.replace("_", "").isalnum():
            raise MigrationSafetyError("Database contains an unsupported table name.")
        rows = connection.execute(f'SELECT * FROM "{table}"').fetchall()
        encoded_rows = sorted(
            json.dumps(tuple(row), ensure_ascii=True, separators=(",", ":"), default=str)
            for row in rows
        )
        counts[table] = len(rows)
        digest.update(table.encode("utf-8"))
        digest.update(b"\0")
        for encoded in encoded_rows:
            digest.update(encoded.encode("utf-8"))
            digest.update(b"\n")
    return digest.hexdigest(), counts


def _state_id_fingerprints(connection: sqlite3.Connection) -> dict[str, dict[str, str | int]]:
    existing_tables = {
        row["name"]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    result: dict[str, dict[str, str | int]] = {}
    for table in _STATE_TABLES:
        if table not in existing_tables:
            result[table] = {"count": 0, "sha256": hashlib.sha256(b"[]").hexdigest()}
            continue
        columns = {
            row["name"] for row in connection.execute(f'PRAGMA table_info("{table}")')
        }
        key = "trip_id" if table in {"trip_preferences", "trip_conversations"} else "thread_id"
        if key not in columns:
            raise MigrationSafetyError("A persisted trip table has an unexpected schema.")
        raw_values = {
            row[0] for row in connection.execute(f'SELECT DISTINCT "{key}" FROM "{table}"')
        }
        if any(not isinstance(value, str) or not value or len(value) > 100 for value in raw_values):
            raise MigrationSafetyError("A persisted trip identifier is invalid.")
        identity_columns = {
            "trip_preferences": ("trip_id",),
            "trip_conversations": ("turn_id", "trip_id"),
            "checkpoints": ("thread_id", "checkpoint_ns", "checkpoint_id", "parent_checkpoint_id"),
            "writes": ("thread_id", "task_id", "idx", "channel"),
        }[table]
        table_fingerprints: dict[str, str | int] = {}
        for column in identity_columns:
            if column not in columns:
                continue
            values = [
                row[0]
                for row in connection.execute(
                    f'SELECT DISTINCT "{column}" FROM "{table}"'
                )
            ]
            encoded_values = sorted(
                json.dumps(value, ensure_ascii=True, separators=(",", ":"), default=str)
                for value in values
            )
            encoded = json.dumps(
                encoded_values, ensure_ascii=True, separators=(",", ":")
            ).encode("utf-8")
            table_fingerprints[f"{column}_count"] = len(values)
            table_fingerprints[f"{column}_sha256"] = hashlib.sha256(encoded).hexdigest()
        primary_values = {
            row[0] for row in connection.execute(f'SELECT DISTINCT "{key}" FROM "{table}"')
        }
        primary_encoded = json.dumps(
            sorted(primary_values), ensure_ascii=True, separators=(",", ":")
        ).encode("utf-8")
        table_fingerprints["count"] = len(primary_values)
        table_fingerprints["sha256"] = hashlib.sha256(primary_encoded).hexdigest()
        result[table] = table_fingerprints
    return result


def _payload_fingerprints(connection: sqlite3.Connection) -> dict[str, str]:
    tables = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    fingerprints: dict[str, str] = {}
    for table_row in tables:
        table = table_row["name"]
        if not table.replace("_", "").isalnum():
            raise MigrationSafetyError("Database contains an unsupported table name.")
        encoded_rows = sorted(
            json.dumps(tuple(row), ensure_ascii=True, separators=(",", ":"), default=str)
            for row in connection.execute(f'SELECT * FROM "{table}"').fetchall()
        )
        digest = hashlib.sha256()
        for encoded in encoded_rows:
            digest.update(encoded.encode("utf-8"))
            digest.update(b"\n")
        fingerprints[table] = digest.hexdigest()
    return fingerprints


def read_only_preflight(database_path: Path | str) -> dict[str, object]:
    """Inspect the existing database without opening it for writes."""
    path = Path(database_path).resolve()
    if not path.is_file():
        raise MigrationSafetyError("The configured database does not exist.")
    connection = None
    try:
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5.0)
        connection.row_factory = sqlite3.Row
        _integrity_check(connection)
        snapshot_hash, row_counts = _logical_snapshot(connection)
        id_fingerprints = _state_id_fingerprints(connection)
        payload_fingerprints = _payload_fingerprints(connection)
        return {
            "snapshot_hash": snapshot_hash,
            "row_counts": row_counts,
            "id_fingerprints": id_fingerprints,
            "payload_fingerprints": payload_fingerprints,
        }
    except MigrationSafetyError:
        raise
    except (OSError, sqlite3.Error) as exc:
        logger.warning("Read-only database preflight failed (error_type=%s)", type(exc).__name__)
        raise MigrationSafetyError("Read-only database preflight failed.") from exc
    finally:
        if connection is not None:
            connection.close()


def assert_database_quiescent(database_path: Path | str) -> None:
    """Fail if another SQLite writer currently holds a write reservation."""
    connection = None
    try:
        connection = sqlite3.connect(database_path, timeout=0.1)
        connection.execute("BEGIN IMMEDIATE")
        connection.rollback()
    except sqlite3.Error as exc:
        if connection is not None:
            connection.rollback()
        logger.warning("Database writer preflight failed (error_type=%s)", type(exc).__name__)
        raise MigrationSafetyError("An active database writer was detected or could not be ruled out.") from exc
    finally:
        if connection is not None:
            connection.close()


_WINDOWS_CREATION_TIME_HELPER = r"""
function Convert-CreationDateToFileTime($dateValue) {
  if ($dateValue -is [datetime]) {
    $fileTimeValue = $dateValue.ToUniversalTime().ToFileTimeUtc()
    return [string]$fileTimeValue
  }
  $parsedDate = [Management.ManagementDateTimeConverter]::ToDateTime([string]$dateValue)
  $fileTimeValue = $parsedDate.ToUniversalTime().ToFileTimeUtc()
  return [string]$fileTimeValue
}
"""


_WINDOWS_PROCESS_SCAN = _WINDOWS_CREATION_TIME_HELPER + r"""
$ErrorActionPreference = 'Stop'
$currentPid = [int]$env:PHASE2E_CURRENT_PID
$currentCreation = $env:PHASE2E_CURRENT_CREATION_FILETIME
$runnerPid = if ($env:PHASE2E_AUTH_RUNNER_PID) { [int]$env:PHASE2E_AUTH_RUNNER_PID } else { $null }
$runnerCreation = if ($env:PHASE2E_AUTH_RUNNER_CREATION_FILETIME) { $env:PHASE2E_AUTH_RUNNER_CREATION_FILETIME } else { $null }
$databasePattern = [regex]::Escape($env:PHASE2E_DATABASE_PATH)
$observe = $env:PHASE2E_PROCESS_OBSERVE -eq '1'
$stages = @('initial', 'recheck')
if ($observe) { $stages += 'followup' }
$snapshots = @()
foreach ($stage in $stages) {
  $processes = @(Get-CimInstance Win32_Process)
  $rows = @()
  foreach ($process in $processes) {
    $reasons = @()
    if ($process.CommandLine -match '(?i)uvicorn') { $reasons += 'uvicorn_token' }
    if ($process.CommandLine -match '(?i)backend\.main') { $reasons += 'backend_main_token' }
    if ($process.CommandLine -match '(?i)manage_auth') { $reasons += 'manage_auth_token' }
    if ($process.CommandLine -match $databasePattern) { $reasons += 'configured_database_path_reference' }
    $classification = 'unclassified_match'
    if ($process.CommandLine -match '(?i)test_manage_auth\.py') { $classification = 'auth_test_script' }
    elseif ($process.CommandLine -match '(?i)(?:^|\s)-m\s+backend\.manage_auth(?:\s|$)') { $classification = 'auth_cli_module' }
    elseif ($process.CommandLine -match '(?i)(?:^|[\\/])manage_auth\.py(?:["'']?(?:\s|$))') { $classification = 'auth_cli_script' }
    elseif ($process.CommandLine -match '(?i)(?:^|\s)-m\s+uvicorn(?:\s|$)') { $classification = 'uvicorn_module' }
    elseif ($process.CommandLine -match '(?i)backend\.main') { $classification = 'backend_main_reference' }
    elseif ($process.CommandLine -match $databasePattern) { $classification = 'database_path_reference' }
    $creationFileTime = if ($process.CreationDate) { Convert-CreationDateToFileTime $process.CreationDate } else { $null }
    $rows += [ordered]@{
      pid = [int]$process.ProcessId
      parent_pid = [int]$process.ParentProcessId
      executable = [string]$process.Name
      executable_path = if ($process.ExecutablePath) { [string]$process.ExecutablePath } else { $null }
      creation_time = if ($process.CreationDate) { [string]$process.CreationDate } else { $null }
      creation_filetime = $creationFileTime
      match_reasons = @($reasons)
      classification = $classification
    }
  }
  $snapshots += [ordered]@{ label = $stage; processes = @($rows) }
}
$result = [ordered]@{
  current_cli_pid = $currentPid
  current_cli_creation_filetime = $currentCreation
  runner_pid = $runnerPid
  runner_creation_filetime = $runnerCreation
  observation_mode = [bool]$observe
  snapshots = @($snapshots)
}
[Console]::Out.WriteLine(($result | ConvertTo-Json -Compress -Depth 8))
"""


_PROCESS_MATCH_REASONS = {
    "uvicorn_token",
    "backend_main_token",
    "manage_auth_token",
    "configured_database_path_reference",
}
_PROCESS_RECHECK_STATES = {"persistent", "disappeared", "pid_reused", "recheck_unverifiable"}
_SAFE_POWERSHELL_CATEGORIES = {
    "PermissionDenied",
    "ObjectNotFound",
    "InvalidOperation",
    "ResourceUnavailable",
    "SecurityError",
    "NotSpecified",
}
_SAFE_ACL_ERROR_CATEGORIES = {
    "PermissionDenied",
    "ObjectNotFound",
    "InvalidOperation",
    "SecurityError",
    "NotSpecified",
}


def _classify_powershell_scan_error(
    stderr: str | bytes | None,
    return_code: int | None,
) -> dict[str, str | int | None]:
    """Extract only allow-listed error metadata; never return stderr text."""
    if isinstance(stderr, bytes):
        text = stderr.decode("utf-8", errors="replace")
    else:
        text = stderr or ""
    hresult_match = re.search(r"\b0x[0-9a-fA-F]{8}\b", text)
    category_match = re.search(r"(?i)CategoryInfo\s*:\s*([A-Za-z]+)", text)
    raw_category = category_match.group(1) if category_match else None
    safe_category = raw_category if raw_category in _SAFE_POWERSHELL_CATEGORIES else None
    lowered = text.casefold()
    if hresult_match and hresult_match.group(0).upper() == "0X80041003":
        classification = "cim_access_denied"
    elif safe_category == "PermissionDenied" or "access is denied" in lowered:
        classification = "cim_access_denied"
    elif "parsererror" in lowered or "pssecurityexception" in lowered or "execution policy" in lowered:
        classification = "powershell_execution_failure"
    elif "managementdatetimesconverter" in lowered or "convert-creationdatetofiletime" in lowered:
        classification = "process_metadata_conversion_failure"
    elif "convertto-json" in lowered or "serialization" in lowered:
        classification = "snapshot_serialization_failure"
    elif "get-ciminstance" in lowered or "cimexception" in lowered:
        classification = "cim_process_enumeration_failure"
    else:
        classification = "powershell_process_scan_failure"
    return {
        "classification": classification,
        "return_code": return_code,
        "hresult": hresult_match.group(0).upper() if hresult_match else None,
        "category": safe_category,
    }


def _execute_windows_process_scan(environment: dict[str, str]) -> ApplicationProcessSnapshot:
    executable = shutil.which("powershell.exe") or shutil.which("powershell")
    if executable is None:
        logger.warning(
            "Application process scan failed (stage=powershell_resolution classification=powershell_not_found)"
        )
        raise MigrationSafetyError("Cannot verify that the application is stopped.")
    try:
        result = subprocess.run(
            [executable, "-NoProfile", "-NonInteractive", "-Command", _WINDOWS_PROCESS_SCAN],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
            timeout=20,
        )
    except subprocess.TimeoutExpired as exc:
        diagnostic = _classify_powershell_scan_error(exc.stderr, None)
        logger.warning(
            "Application process scan failed (stage=powershell_execution classification=powershell_timeout "
            "hresult=%s category=%s)",
            diagnostic["hresult"] or "unknown",
            diagnostic["category"] or "unknown",
        )
        raise MigrationSafetyError("Cannot verify that the application is stopped.") from exc
    except OSError as exc:
        logger.warning(
            "Application process scan failed (stage=powershell_launch classification=powershell_launch_failure "
            "error_type=%s)",
            type(exc).__name__,
        )
        raise MigrationSafetyError("Cannot verify that the application is stopped.") from exc
    if result.returncode != 0:
        diagnostic = _classify_powershell_scan_error(result.stderr, result.returncode)
        logger.warning(
            "Application process scan failed (stage=powershell_execution return_code=%s classification=%s "
            "hresult=%s category=%s)",
            diagnostic["return_code"],
            diagnostic["classification"],
            diagnostic["hresult"] or "unknown",
            diagnostic["category"] or "unknown",
        )
        raise MigrationSafetyError("Cannot verify that the application is stopped.")
    try:
        return _parse_windows_process_snapshot(result.stdout)
    except MigrationSafetyError as exc:
        lowered = str(exc).casefold()
        if "runner" in lowered or "ancestry" in lowered or "parent identity" in lowered:
            classification = "invocation_identity_verification_failure"
        elif "current cli" in lowered:
            classification = "current_cli_identity_verification_failure"
        else:
            classification = "process_identity_verification_failure"
        logger.warning(
            "Application process scan failed (stage=identity_verification classification=%s)",
            classification,
        )
        raise
    except (json.JSONDecodeError, TypeError, ValueError, KeyError) as exc:
        logger.warning(
            "Application process scan failed (stage=snapshot_deserialization "
            "classification=snapshot_serialization_failure error_type=%s)",
            type(exc).__name__,
        )
        raise MigrationSafetyError("Cannot verify that the application is stopped.") from exc


def _classify_backup_acl_error(
    stderr: str | bytes | None,
    return_code: int | None,
) -> dict[str, str | int | None]:
    """Extract allow-listed ACL failure metadata without retaining raw PowerShell text."""
    if isinstance(stderr, bytes):
        text = stderr.decode("utf-8", errors="replace")
    else:
        text = stderr or ""
    lowered = text.casefold()
    hresult_match = re.search(r"\b0x[0-9a-fA-F]{8}\b", text)
    category_match = re.search(r"(?i)CategoryInfo\s*:\s*([A-Za-z]+)", text)
    raw_category = category_match.group(1) if category_match else None
    safe_category = raw_category if raw_category in _SAFE_ACL_ERROR_CATEGORIES else None
    if "backup acl verification failed" in lowered:
        classification = "acl_rule_verification_failure"
    elif (
        (hresult_match and hresult_match.group(0).upper() in {"0X80070005", "0X80041003"})
        or safe_category == "PermissionDenied"
        or "access is denied" in lowered
    ):
        classification = "acl_permission_denied"
    elif "parsererror" in lowered or "pssecurityexception" in lowered:
        classification = "powershell_execution_failure"
    elif "new-object" in lowered or "filesystemaccessrule" in lowered:
        classification = "acl_rule_construction_failure"
    elif "set-acl" in lowered or "setaccessruleprotection" in lowered:
        classification = "acl_application_failure"
    elif "get-acl" in lowered:
        classification = "acl_inspection_failure"
    else:
        classification = "acl_powershell_failure"
    return {
        "classification": classification,
        "return_code": return_code,
        "hresult": hresult_match.group(0).upper() if hresult_match else None,
        "category": safe_category,
    }


def _run_windows_acl_script(script: str, environment: dict[str, str], stage: str) -> None:
    executable = shutil.which("powershell.exe") or shutil.which("powershell")
    if executable is None:
        logger.warning(
            "Backup ACL check failed (stage=%s classification=powershell_not_found)", stage
        )
        raise MigrationSafetyError("Cannot verify Windows backup permissions.")
    try:
        result = subprocess.run(
            [executable, "-NoProfile", "-NonInteractive", "-Command", script],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        diagnostic = _classify_backup_acl_error(exc.stderr, None)
        logger.warning(
            "Backup ACL check failed (stage=%s classification=powershell_timeout "
            "hresult=%s category=%s)",
            stage,
            diagnostic["hresult"] or "unknown",
            diagnostic["category"] or "unknown",
        )
        raise MigrationSafetyError("Backup permissions could not be verified.") from exc
    except OSError as exc:
        logger.warning(
            "Backup ACL check failed (stage=%s classification=powershell_launch_failure "
            "error_type=%s)",
            stage,
            type(exc).__name__,
        )
        raise MigrationSafetyError("Backup permissions could not be verified.") from exc
    if result.returncode != 0:
        diagnostic = _classify_backup_acl_error(result.stderr, result.returncode)
        logger.warning(
            "Backup ACL check failed (stage=%s return_code=%s classification=%s "
            "hresult=%s category=%s)",
            stage,
            diagnostic["return_code"],
            diagnostic["classification"],
            diagnostic["hresult"] or "unknown",
            diagnostic["category"] or "unknown",
        )
        raise MigrationSafetyError("Backup permissions could not be verified.")


def _windows_acl_environment(directory: Path, backup_file: Path | None = None) -> dict[str, str]:
    environment = os.environ.copy()
    environment["PHASE2E_BACKUP_DIR"] = str(directory)
    if backup_file is not None:
        environment["PHASE2E_BACKUP_FILE"] = str(backup_file)
    return environment


def _prepare_windows_backup_directory(directory: Path) -> None:
    _run_windows_acl_script(
        _WINDOWS_ACL_DIRECTORY_SCRIPT,
        _windows_acl_environment(directory),
        "directory_creation_or_verification",
    )


def _apply_windows_backup_file_acl(directory: Path, backup_file: Path) -> None:
    _run_windows_acl_script(
        _WINDOWS_ACL_FILE_SCRIPT,
        _windows_acl_environment(directory, backup_file),
        "file_acl_application_or_verification",
    )
def _verified_invocation_identities(
    process_rows: list[ProcessSnapshotEntry],
    current_cli_pid: int,
    current_cli_creation_filetime: str,
    runner_pid: int | None,
    runner_creation_filetime: str | None,
) -> set[tuple[int, str]]:
    """Return only identities verified by creation time and parent ancestry."""
    by_pid = {row.pid: row for row in process_rows}
    if len(by_pid) != len(process_rows):
        raise MigrationSafetyError("Process snapshot contains duplicate PIDs; invocation identity is ambiguous.")
    current = by_pid.get(current_cli_pid)
    if (
        current is None
        or not current.creation_filetime
        or current.creation_filetime != current_cli_creation_filetime
    ):
        raise MigrationSafetyError("Current CLI PID/creation identity could not be verified.")

    invocation_rows: dict[int, ProcessSnapshotEntry] = {current.pid: current}
    if (runner_pid is None) != (runner_creation_filetime is None):
        raise MigrationSafetyError("Runner invocation identity is incomplete.")
    if runner_pid is not None:
        runner = by_pid.get(runner_pid)
        if (
            runner is None
            or not runner.creation_filetime
            or runner.creation_filetime != runner_creation_filetime
            or runner_pid == current_cli_pid
        ):
            raise MigrationSafetyError("Runner PID/creation identity could not be verified.")
        if int(runner.creation_filetime) > int(current.creation_filetime):
            raise MigrationSafetyError("Runner creation time is inconsistent with the CLI ancestry.")
        seen = {current.pid}
        cursor = current
        while cursor.parent_pid != runner_pid:
            parent_pid = cursor.parent_pid
            if parent_pid is None or parent_pid in seen:
                raise MigrationSafetyError("Runner-to-CLI ancestry could not be verified.")
            parent = by_pid.get(parent_pid)
            if parent is None or not parent.creation_filetime:
                raise MigrationSafetyError("Runner-to-CLI parent identity is missing from the process snapshot.")
            if int(parent.creation_filetime) > int(cursor.creation_filetime or 0):
                raise MigrationSafetyError("Runner-to-CLI ancestry contains a reused or inconsistent PID.")
            invocation_rows[parent.pid] = parent
            seen.add(parent.pid)
            cursor = parent
        invocation_rows[runner.pid] = runner

    # Include only descendants of this verified CLI process, never other Python
    # processes or descendants of unrelated processes.
    descendants = {current.pid}
    changed = True
    while changed:
        changed = False
        for row in process_rows:
            parent = by_pid.get(row.parent_pid) if row.parent_pid is not None else None
            if (
                row.parent_pid in descendants
                and row.pid not in descendants
                and row.creation_filetime
                and parent
                and parent.creation_filetime
                and int(parent.creation_filetime) <= int(row.creation_filetime)
            ):
                descendants.add(row.pid)
                invocation_rows[row.pid] = row
                changed = True
    if any(not row.creation_filetime for row in invocation_rows.values()):
        raise MigrationSafetyError("A process in the migration invocation has no verifiable creation time.")
    return {(row.pid, row.creation_filetime) for row in invocation_rows.values() if row.creation_filetime}


def _process_match_reasons(command_line: str, database_path: Path | None = None) -> tuple[str, ...]:
    """Mirror the conservative substring rules and return labels only."""
    command = command_line.casefold()
    checks = [
        ("uvicorn", "uvicorn_token"),
        ("backend.main", "backend_main_token"),
        ("manage_auth", "manage_auth_token"),
    ]
    if database_path is not None:
        checks.append((str(database_path.resolve()).casefold(), "configured_database_path_reference"))
    return tuple(reason for token, reason in checks if token and token in command)


def _parse_windows_process_snapshot(output: str) -> ApplicationProcessSnapshot:
    result = json.loads(output)
    current_cli_pid = int(result["current_cli_pid"])
    current_creation = str(result.get("current_cli_creation_filetime") or "")
    runner_pid = int(result["runner_pid"]) if result.get("runner_pid") else None
    runner_creation = (
        str(result["runner_creation_filetime"])
        if result.get("runner_creation_filetime")
        else None
    )
    raw_snapshots = result.get("snapshots")
    if not isinstance(raw_snapshots, list) or not raw_snapshots or raw_snapshots[0].get("label") != "initial":
        raise ValueError("Process snapshot omitted the initial process table.")

    snapshots: list[tuple[str, list[ProcessSnapshotEntry]]] = []
    for raw_snapshot in raw_snapshots:
        rows = []
        for raw in raw_snapshot.get("processes", []):
            reasons = tuple(str(reason) for reason in raw.get("match_reasons", []))
            if not set(reasons).issubset(_PROCESS_MATCH_REASONS):
                raise ValueError("Process snapshot contains an unknown reason category.")
            rows.append(
                ProcessSnapshotEntry(
                    pid=int(raw["pid"]),
                    parent_pid=int(raw["parent_pid"]) if raw.get("parent_pid") is not None else None,
                    executable=Path(str(raw.get("executable") or "unknown")).name,
                    executable_path=raw.get("executable_path"),
                    creation_time=raw.get("creation_time"),
                    creation_filetime=(
                        str(raw["creation_filetime"])
                        if raw.get("creation_filetime") is not None
                        else None
                    ),
                    match_reasons=reasons,
                    classification=str(raw.get("classification", "unclassified_match")),
                )
            )
        snapshots.append((str(raw_snapshot["label"]), rows))

    initial_rows = snapshots[0][1]
    verified_invocation = _verified_invocation_identities(
        initial_rows,
        current_cli_pid,
        current_creation,
        runner_pid,
        runner_creation,
    )
    excluded_pids = tuple(sorted(pid for pid, _ in verified_invocation))
    all_initial = {row.pid: row for row in initial_rows}
    recheck_rows = next((rows for label, rows in snapshots if label == "recheck"), [])
    followup_rows = next((rows for label, rows in snapshots if label == "followup"), [])
    recheck_by_pid = {row.pid: row for row in recheck_rows}
    followup_by_pid = {row.pid: row for row in followup_rows}
    candidates: list[ApplicationProcessCandidate] = []
    python_names = {"python", "python.exe", "pythonw", "pythonw.exe", "uvicorn", "uvicorn.exe"}
    for initial in initial_rows:
        if not initial.match_reasons or initial.executable.casefold() not in python_names:
            continue
        identity = (initial.pid, initial.creation_filetime or "")
        if identity in verified_invocation:
            continue
        recheck = recheck_by_pid.get(initial.pid)
        followup = followup_by_pid.get(initial.pid)
        if recheck is None:
            recheck_status = "disappeared"
        elif not initial.creation_filetime or not recheck.creation_filetime:
            recheck_status = "recheck_unverifiable"
        elif initial.creation_filetime != recheck.creation_filetime:
            recheck_status = "pid_reused"
        else:
            recheck_status = "persistent"
        parent_recheck_status = "unknown"
        if initial.parent_pid is not None:
            parent_initial = all_initial.get(initial.parent_pid)
            parent_recheck = recheck_by_pid.get(initial.parent_pid)
            if parent_recheck is None:
                parent_recheck_status = "disappeared"
            elif not parent_initial or not parent_initial.creation_filetime or not parent_recheck.creation_filetime:
                parent_recheck_status = "recheck_unverifiable"
            elif parent_initial.creation_filetime != parent_recheck.creation_filetime:
                parent_recheck_status = "pid_reused"
            else:
                parent_recheck_status = "persistent"
        observations: list[ProcessObservation] = []
        for label, rows in snapshots:
            row = {entry.pid: entry for entry in rows}.get(initial.pid)
            parent_pid = row.parent_pid if row else initial.parent_pid
            parent = {entry.pid: entry for entry in rows}.get(parent_pid) if parent_pid is not None else None
            observations.append(
                ProcessObservation(
                    snapshot=label,
                    exists=row is not None,
                    executable=row.executable if row else None,
                    executable_path=row.executable_path if row else None,
                    creation_time=row.creation_time if row else None,
                    parent_pid=parent_pid,
                    parent_exists=parent is not None,
                    parent_executable=parent.executable if parent else None,
                    parent_executable_path=parent.executable_path if parent else None,
                    parent_creation_time=parent.creation_time if parent else None,
                )
            )
        candidates.append(
            ApplicationProcessCandidate(
                pid=initial.pid,
                parent_pid=initial.parent_pid,
                executable=initial.executable,
                creation_time=initial.creation_time,
                match_reasons=initial.match_reasons,
                recheck_status=recheck_status,
                rechecked_creation_time=recheck.creation_time if recheck else None,
                classification=initial.classification,
                observations=tuple(observations),
                parent_recheck_status=parent_recheck_status,
            )
        )
    return ApplicationProcessSnapshot(
        current_cli_pid,
        excluded_pids,
        tuple(candidates),
        bool(result.get("observation_mode", False)),
        runner_pid,
        True,
        tuple(sorted(verified_invocation)),
        current_creation,
        runner_creation,
    )


def _application_process_snapshot(
    database_path: Path,
    *,
    observation_mode: bool | None = None,
) -> ApplicationProcessSnapshot:
    if os.name == "nt":
        environment = os.environ.copy()
        environment["PHASE2E_CURRENT_PID"] = str(os.getpid())
        environment["PHASE2E_DATABASE_PATH"] = str(database_path.resolve())
        environment["PHASE2E_AUTH_RUNNER_PID"] = os.environ.get(
            "PHASE2E_AUTH_RUNNER_PID", ""
        )
        environment["PHASE2E_AUTH_RUNNER_CREATION_FILETIME"] = os.environ.get(
            "PHASE2E_AUTH_RUNNER_CREATION_FILETIME", ""
        )
        if observation_mode is None:
            observation_mode = os.environ.get("PHASE2E_PROCESS_OBSERVE", "").casefold() in {
                "1", "true", "yes"
            }
        environment["PHASE2E_PROCESS_OBSERVE"] = "1" if observation_mode else "0"
        try:
            environment["PHASE2E_CURRENT_CREATION_FILETIME"] = str(
                current_process_creation_filetime()
            )
        except OSError as exc:
            logger.warning(
                "Application process scan failed (stage=current_identity classification=current_identity_unavailable "
                "error_type=%s)",
                type(exc).__name__,
            )
            raise MigrationSafetyError("Cannot verify that the application is stopped.") from exc
        return _execute_windows_process_scan(environment)

    if os.path.isdir("/proc"):
        matches: list[ApplicationProcessCandidate] = []
        current_pid = os.getpid()
        for entry in os.scandir("/proc"):
            if not entry.name.isdigit() or int(entry.name) == current_pid:
                continue
            try:
                process_name = Path(entry.path, "comm").read_text(encoding="utf-8").strip().casefold()
                if not (process_name.startswith("python") or process_name == "uvicorn"):
                    continue
                command = Path(entry.path, "cmdline").read_bytes().replace(b"\0", b" ").decode(
                    "utf-8", errors="ignore"
                )
                status = Path(entry.path, "status").read_text(encoding="utf-8", errors="ignore")
                parent_match = re.search(r"(?m)^PPid:\s*(\d+)", status)
                parent_pid = int(parent_match.group(1)) if parent_match else None
                stat_text = Path(entry.path, "stat").read_text(encoding="utf-8", errors="ignore")
                creation_time = stat_text.rsplit(")", 1)[1].split()[19]
            except FileNotFoundError:
                continue
            except PermissionError as exc:
                raise MigrationSafetyError(
                    "Cannot verify that the application is stopped."
                ) from exc
            except OSError as exc:
                logger.warning("Application process scan failed (error_type=%s)", type(exc).__name__)
                raise MigrationSafetyError("Cannot verify that the application is stopped.") from exc
            reasons = _process_match_reasons(command)
            if reasons:
                matches.append(
                    ApplicationProcessCandidate(
                        pid=int(entry.name),
                        parent_pid=parent_pid,
                        executable=process_name,
                        creation_time=creation_time,
                        match_reasons=reasons,
                        recheck_status="pending",
                    )
                )
        rechecked = []
        for candidate in matches:
            try:
                current_stat = Path("/proc", str(candidate.pid), "stat").read_text(encoding="utf-8")
                current_creation = current_stat.rsplit(")", 1)[1].split()[19]
                state = "persistent" if current_creation == candidate.creation_time else "pid_reused"
            except FileNotFoundError:
                current_creation = None
                state = "disappeared"
            rechecked.append(
                ApplicationProcessCandidate(
                    **{**candidate.__dict__, "recheck_status": state, "rechecked_creation_time": current_creation}
                )
            )
        return ApplicationProcessSnapshot(current_pid, (current_pid,), tuple(rechecked), False)
    raise MigrationSafetyError("Cannot verify that the application is stopped on this platform.")


def assert_application_stopped(database_path: Path | str, port: int) -> None:
    """Check common loopback listeners and process arguments; fail closed on scan errors."""
    path = Path(database_path).resolve()
    for family, address in ((socket.AF_INET, ("127.0.0.1", port)), (socket.AF_INET6, ("::1", port))):
        probe = socket.socket(family, socket.SOCK_STREAM)
        probe.settimeout(0.2)
        try:
            if probe.connect_ex(address) == 0:
                raise MigrationSafetyError(f"An application service is listening on configured port {port}.")
        finally:
            probe.close()
    snapshot = _application_process_snapshot(path)
    logger.info(
        "Application process snapshot current_cli_identity=%s runner_identity=%s "
        "verified_exclusions=%s invocation_verified=%s candidate_count=%d observation_mode=%s",
        f"{snapshot.current_cli_pid}@{snapshot.current_cli_creation_filetime or 'unknown'}",
        (
            f"{snapshot.runner_pid}@{snapshot.runner_creation_filetime or 'unknown'}"
            if snapshot.runner_pid is not None
            else "standalone"
        ),
        ",".join(f"{pid}@{created}" for pid, created in snapshot.verified_invocation_identities),
        snapshot.invocation_verified,
        len(snapshot.candidates),
        snapshot.observation_mode,
    )
    if snapshot.candidates:
        status_counts = {state: 0 for state in _PROCESS_RECHECK_STATES}
        for candidate in snapshot.candidates:
            status_counts[candidate.recheck_status] += 1
            logger.warning(
                "Application process candidate pid=%d parent_pid=%s executable=%s creation_time=%s "
                "executable_path=%s classification=%s match_reasons=%s recheck_status=%s "
                "rechecked_creation_time=%s parent_recheck_status=%s",
                candidate.pid,
                candidate.parent_pid if candidate.parent_pid is not None else "unknown",
                candidate.executable,
                candidate.creation_time or "unknown",
                (
                    candidate.observations[0].executable_path
                    if candidate.observations
                    else None
                ) or "unknown",
                candidate.classification,
                ",".join(candidate.match_reasons) or "unknown",
                candidate.recheck_status,
                candidate.rechecked_creation_time or "unknown",
                candidate.parent_recheck_status,
            )
            for observation in candidate.observations:
                logger.warning(
                    "Process observation candidate_pid=%d snapshot=%s exists=%s executable=%s "
                    "executable_path=%s creation_time=%s parent_pid=%s parent_exists=%s "
                    "parent_executable=%s parent_executable_path=%s parent_creation_time=%s",
                    candidate.pid,
                    observation.snapshot,
                    observation.exists,
                    observation.executable or "unknown",
                    observation.executable_path or "unknown",
                    observation.creation_time or "unknown",
                    observation.parent_pid if observation.parent_pid is not None else "unknown",
                    observation.parent_exists,
                    observation.parent_executable or "unknown",
                    observation.parent_executable_path or "unknown",
                    observation.parent_creation_time or "unknown",
                )
        summary = ",".join(f"{state}={count}" for state, count in sorted(status_counts.items()) if count)
        logger.warning("Application process candidate recheck summary: %s", summary)
        candidate_text = "; ".join(
            f"pid={candidate.pid}, parent_pid={candidate.parent_pid if candidate.parent_pid is not None else 'unknown'}, "
            f"executable={candidate.executable}, creation_time={candidate.creation_time or 'unknown'}, "
            f"executable_path={(candidate.observations[0].executable_path or 'unknown') if candidate.observations else 'unknown'}, "
            f"classification={candidate.classification}, matched={','.join(candidate.match_reasons) or 'unknown'}, "
            f"recheck={candidate.recheck_status}, parent_recheck={candidate.parent_recheck_status}"
            for candidate in snapshot.candidates
        )
        raise MigrationSafetyError(
            "An application process candidate was detected ("
            + candidate_text
            + f"; current_cli_pid={snapshot.current_cli_pid}, excluded_pids={','.join(map(str, snapshot.excluded_pids))}). "
            "The safety gate remains closed; stop it normally and retry."
        )


def _restrict_backup_permissions(directory: Path, backup_file: Path) -> None:
    if os.name == "nt":
        _apply_windows_backup_file_acl(directory, backup_file)
        return
    directory.chmod(0o700)
    backup_file.chmod(0o600)
    if directory.stat().st_mode & 0o077 or backup_file.stat().st_mode & 0o077:
        raise MigrationSafetyError("Backup permissions could not be verified.")


def create_verified_backup(database_path: Path | str, backup_directory: Path | str) -> VerifiedBackup:
    source_path = Path(database_path)
    backup_dir = Path(backup_directory)
    if not source_path.is_file():
        raise MigrationSafetyError("The configured database does not exist.")
    if os.name == "nt":
        _prepare_windows_backup_directory(backup_dir)
    else:
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_dir.chmod(0o700)
    backup_file = backup_dir / (
        "trip_planner_pre_auth_"
        + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        + "_"
        + uuid.uuid4().hex[:8]
        + ".sqlite3"
    )
    source = None
    destination = None
    backup_stage = "source_open"
    try:
        source = _open_database(source_path)
        backup_stage = "source_integrity_before"
        _integrity_check(source)
        logger.info("BACKUP_STAGE: source_integrity_before status=PASS")
        backup_stage = "source_snapshot_before"
        before_hash, before_counts = _logical_snapshot(source)
        before_ids = _state_id_fingerprints(source)
        before_payloads = _payload_fingerprints(source)
        # Create an empty file exclusively, apply/verify confidentiality ACLs,
        # and only then let SQLite write any backup pages into it.
        backup_stage = "backup_file_creation"
        with backup_file.open("xb"):
            pass
        if not backup_file.is_file():
            raise MigrationSafetyError("The backup file was not created.")
        logger.info(
            "BACKUP_STAGE: backup_file_created path=%s exists=true", backup_file.resolve()
        )
        backup_stage = "backup_file_acl"
        _restrict_backup_permissions(backup_dir, backup_file)
        backup_stage = "sqlite_backup_api"
        destination = sqlite3.connect(backup_file)
        source.backup(destination)
        destination.close()
        destination = None
        logger.info("BACKUP_STAGE: sqlite_backup_api status=PASS")
        if not backup_file.is_file():
            raise MigrationSafetyError("The verified backup file is missing.")
        logger.info(
            "BACKUP_STAGE: backup_file_after_copy path=%s exists=true size_bytes=%s",
            backup_file.resolve(),
            backup_file.stat().st_size,
        )
        backup_stage = "source_integrity_after"
        source_after = _open_database(source_path)
        try:
            _integrity_check(source_after)
            logger.info("BACKUP_STAGE: source_integrity_after status=PASS")
            after_hash, after_counts = _logical_snapshot(source_after)
            after_ids = _state_id_fingerprints(source_after)
            after_payloads = _payload_fingerprints(source_after)
        finally:
            source_after.close()
        backup_stage = "backup_integrity"
        backup = _open_database(backup_file)
        try:
            _integrity_check(backup)
            logger.info("BACKUP_STAGE: backup_integrity status=PASS")
            backup_hash, backup_counts = _logical_snapshot(backup)
            backup_ids = _state_id_fingerprints(backup)
            backup_payloads = _payload_fingerprints(backup)
        finally:
            backup.close()
        backup_stage = "logical_snapshot_match"
        if not (before_hash == after_hash == backup_hash):
            raise MigrationSafetyError("Database changed during backup verification.")
        backup_stage = "table_counts"
        if not (before_counts == after_counts == backup_counts):
            raise MigrationSafetyError("Backup row counts do not match the database.")
        logger.info(
            "BACKUP_STAGE: table_counts status=PASS tables=%s",
            ",".join(f"{table}:{count}" for table, count in sorted(backup_counts.items())),
        )
        backup_stage = "source_backup_ids"
        if not (before_ids == after_ids == backup_ids):
            raise MigrationSafetyError("Backup trip, checkpoint, or writes IDs do not match.")
        id_counts = []
        for table, details in sorted(backup_ids.items()):
            for key, value in sorted(details.items()):
                if isinstance(value, int) and (key == "count" or key.endswith("_count")):
                    id_counts.append(f"{table}.{key.removesuffix('_count')}={value}")
        logger.info("BACKUP_STAGE: record_ids status=PASS counts=%s", ",".join(id_counts))
        backup_stage = "source_backup_payload_fingerprints"
        if not (before_payloads == after_payloads == backup_payloads):
            raise MigrationSafetyError("Backup data fingerprints do not match the database.")
        logger.info(
            "BACKUP_STAGE: payload_fingerprints status=PASS tables=%s",
            ",".join(sorted(backup_payloads)),
        )
        logger.info(
            "BACKUP_RESULT: status=PASS path=%s exists=true integrity=PASS counts=PASS "
            "record_ids=PASS payload_fingerprints=PASS",
            backup_file.resolve(),
        )
        return VerifiedBackup(
            source_path,
            backup_file,
            backup_hash,
            backup_counts,
            backup_ids,
            backup_payloads,
        )
    except MigrationSafetyError as exc:
        logger.error(
            "BACKUP_RESULT: status=FAIL stage=%s error_type=%s",
            backup_stage,
            type(exc).__name__,
        )
        raise
    except (OSError, sqlite3.Error) as exc:
        logger.warning(
            "BACKUP_RESULT: status=FAIL stage=%s error_type=%s",
            backup_stage,
            type(exc).__name__,
        )
        raise MigrationSafetyError("Database backup could not be verified.") from exc
    finally:
        if destination is not None:
            destination.close()
        if source is not None:
            source.close()


def _trip_ids(connection: sqlite3.Connection) -> set[str]:
    existing_tables = {
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    trip_ids: set[str] = set()
    for table in _STATE_TABLES:
        if table not in existing_tables:
            continue
        columns = {
            row["name"]
            for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()
        }
        key = "trip_id" if table in {"trip_preferences", "trip_conversations"} else "thread_id"
        if key not in columns:
            raise MigrationSafetyError("A persisted trip table has an unexpected schema.")
        rows = connection.execute(f'SELECT DISTINCT "{key}" FROM "{table}"').fetchall()
        for row in rows:
            value = row[0]
            if not isinstance(value, str) or not value or len(value) > 100:
                raise MigrationSafetyError("A persisted trip identifier is invalid.")
            trip_ids.add(value)
    return trip_ids


def migrate_local_owner(
    backup: VerifiedBackup,
    owner_id: str,
    password_hash: str,
) -> bool:
    """Apply the additive owner mapping only if a verified backup still matches."""
    if backup.database_path.resolve() == backup.backup_path.resolve():
        raise MigrationSafetyError("The backup cannot replace the configured database.")
    source = _open_database(backup.database_path)
    backup_connection = _open_database(backup.backup_path)
    try:
        # Hold the write reservation while comparing the verified snapshot and
        # applying the schema changes. This closes the check/use race.
        source.execute("BEGIN IMMEDIATE")
        _integrity_check(source)
        _integrity_check(backup_connection)
        current_hash, current_counts = _logical_snapshot(source)
        backup_hash, backup_counts = _logical_snapshot(backup_connection)
        if not (
            current_hash == backup_hash == backup.snapshot_hash
            and current_counts == backup_counts == backup.row_counts
        ):
            raise MigrationSafetyError("The database changed after the verified backup.")
        ids = _trip_ids(source)
        existing_version = source.execute(
            "SELECT version FROM schema_migrations WHERE version = ?",
            (OWNER_MIGRATION_VERSION,),
        ).fetchone() if _table_exists(source, "schema_migrations") else None
        if existing_version is not None:
            owner = source.execute("SELECT owner_id FROM local_owner WHERE singleton = 1").fetchone()
            mappings = {
                row["trip_id"]
                for row in source.execute(
                    "SELECT trip_id FROM trip_ownership WHERE owner_id = ?",
                    (owner["owner_id"],) if owner is not None else ("",),
                ).fetchall()
            }
            if owner is None or not ids.issubset(mappings):
                raise MigrationSafetyError("Existing local-owner migration state is inconsistent.")
            source.rollback()
            return False

        for table in ("local_owner", "auth_sessions", "trip_ownership", "schema_migrations"):
            if _table_exists(source, table):
                raise MigrationSafetyError("Incomplete authentication migration state was found.")

        source.execute(
            """
            CREATE TABLE local_owner (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                owner_id TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                failed_login_count INTEGER NOT NULL DEFAULT 0 CHECK (failed_login_count >= 0),
                locked_until REAL
            )
            """
        )
        source.execute(
            """
            CREATE TABLE auth_sessions (
                session_hash TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL REFERENCES local_owner(owner_id),
                csrf_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
            """
        )
        source.execute(
            """
            CREATE TABLE trip_ownership (
                trip_id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL REFERENCES local_owner(owner_id),
                source TEXT NOT NULL CHECK (source IN ('legacy', 'created'))
            )
            """
        )
        source.execute(
            "CREATE INDEX auth_sessions_owner_idx ON auth_sessions(owner_id)"
        )
        source.execute(
            """
            CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )
        source.execute(
            "INSERT INTO local_owner (singleton, owner_id, password_hash) VALUES (1, ?, ?)",
            (owner_id, password_hash),
        )
        source.executemany(
            "INSERT INTO trip_ownership (trip_id, owner_id, source) VALUES (?, ?, 'legacy')",
            ((trip_id, owner_id) for trip_id in sorted(ids)),
        )
        source.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            (OWNER_MIGRATION_VERSION, datetime.now(timezone.utc).isoformat()),
        )
        _verify_preserved_state(source, backup)
        source.commit()
        return True
    except (sqlite3.Error, MigrationSafetyError) as exc:
        source.rollback()
        if isinstance(exc, MigrationSafetyError):
            raise
        logger.warning("Authentication migration failed (error_type=%s)", type(exc).__name__)
        raise MigrationSafetyError("Authentication migration failed without changing application data.") from exc
    finally:
        source.close()
        backup_connection.close()


def _verify_preserved_state(connection: sqlite3.Connection, backup: VerifiedBackup) -> None:
    current_ids = _state_id_fingerprints(connection)
    current_payloads = _payload_fingerprints(connection)
    for table in _STATE_TABLES:
        if current_ids[table] != backup.id_fingerprints.get(table):
            raise MigrationSafetyError(f"Migration changed persisted IDs in {table}.")
        if current_payloads.get(table) != backup.payload_fingerprints.get(table):
            raise MigrationSafetyError(f"Migration changed persisted data in {table}.")
        current_count = connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] \
            if _table_exists(connection, table) else 0
        if current_count != backup.row_counts.get(table, 0):
            raise MigrationSafetyError(f"Migration changed row counts in {table}.")


def verify_migration_preservation(database_path: Path | str, backup: VerifiedBackup) -> None:
    """Read-only post-migration check that the original trip state is intact."""
    connection = _open_database(Path(database_path))
    try:
        _integrity_check(connection)
        _verify_preserved_state(connection, backup)
    finally:
        connection.close()


def verify_owner_migration(database_path: Path | str, backup: VerifiedBackup) -> dict[str, int]:
    """Verify owner, session, version, and complete legacy ownership state read-only."""
    path = Path(database_path).resolve()
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5.0)
    connection.row_factory = sqlite3.Row
    try:
        _integrity_check(connection)
        required_tables = {"local_owner", "auth_sessions", "trip_ownership", "schema_migrations"}
        existing = {
            row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if not required_tables.issubset(existing):
            raise MigrationSafetyError("Authentication migration tables are incomplete.")
        owners = connection.execute(
            "SELECT owner_id, password_hash FROM local_owner WHERE singleton = 1"
        ).fetchall()
        if (
            len(owners) != 1
            or not isinstance(owners[0]["password_hash"], str)
            or not owners[0]["password_hash"].startswith("$argon2id$")
        ):
            raise MigrationSafetyError("Local owner password storage did not verify.")
        owner_id = owners[0]["owner_id"]
        session_columns = {
            row["name"] for row in connection.execute('PRAGMA table_info("auth_sessions")')
        }
        if not {"session_hash", "owner_id", "csrf_hash", "created_at", "expires_at"}.issubset(
            session_columns
        ):
            raise MigrationSafetyError("Authentication session schema is incomplete.")
        versions = {
            row[0] for row in connection.execute("SELECT version FROM schema_migrations")
        }
        if OWNER_MIGRATION_VERSION not in versions:
            raise MigrationSafetyError("Authentication migration version is missing.")
        mappings = {
            row["trip_id"]: row["owner_id"]
            for row in connection.execute("SELECT trip_id, owner_id FROM trip_ownership")
        }
        expected_ids = set()
        for table in _STATE_TABLES:
            if not _table_exists(connection, table):
                continue
            key = "trip_id" if table in {"trip_preferences", "trip_conversations"} else "thread_id"
            expected_ids.update(
                row[0] for row in connection.execute(f'SELECT DISTINCT "{key}" FROM "{table}"')
            )
        if set(mappings) != expected_ids or any(value != owner_id for value in mappings.values()):
            raise MigrationSafetyError("Legacy trip ownership mappings are incomplete or conflicting.")
        foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_errors:
            raise MigrationSafetyError("Authentication schema foreign-key verification failed.")
        session_count = connection.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0]
        return {
            "owner_rows": len(owners),
            "ownership_rows": len(mappings),
            "session_rows": session_count,
            "migration_version": OWNER_MIGRATION_VERSION,
        }
    except MigrationSafetyError:
        raise
    except sqlite3.Error as exc:
        logger.warning("Owner migration verification failed (error_type=%s)", type(exc).__name__)
        raise MigrationSafetyError("Owner migration verification failed.") from exc
    finally:
        connection.close()


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone() is not None
