"""Windows-only ACL policy checks on disposable test paths."""

import json
import os
import shutil
import subprocess

import pytest

from backend import migrations

pytestmark = pytest.mark.skipif(
    os.name != "nt" or not (shutil.which("powershell.exe") or shutil.which("powershell")),
    reason="Windows PowerShell ACL APIs are required",
)


def _powershell(script: str, **environment: str) -> subprocess.CompletedProcess[str]:
    executable = shutil.which("powershell.exe") or shutil.which("powershell")
    process_environment = os.environ.copy()
    process_environment.update(environment)
    return subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-Command", script],
        check=False,
        capture_output=True,
        text=True,
        env=process_environment,
        timeout=20,
    )


def test_backup_directory_and_file_have_only_protected_owner_system_admin_rules(tmp_path):
    """Exercise the intended policy using only a disposable test directory/file."""
    directory = tmp_path / "new-parent" / "app-data" / "private-backups"
    backup_file = directory / "dummy.sqlite3"

    # Exercise the production secure-create and read-only verification path.
    migrations._prepare_windows_backup_directory(directory)
    assert directory.is_dir()

    # File creation occurs only inside the restricted directory. The production
    # helper then protects and verifies it before SQLite writes backup pages.
    with backup_file.open("xb") as handle:
        handle.write(b"test-only")

    migrations._apply_windows_backup_file_acl(directory, backup_file)

    inspect_script = r"""
$ErrorActionPreference = 'Stop'
$targets = @(
  $env:PHASE2E_TEST_PARENT1,
  $env:PHASE2E_TEST_PARENT2,
  $env:PHASE2E_TEST_DIRECTORY,
  $env:PHASE2E_TEST_FILE
)
$result = @()
foreach ($target in $targets) {
  $acl = Get-Acl -LiteralPath $target
  $rules = @($acl.Access | ForEach-Object {
    [ordered]@{
      sid = $_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value
      allow = $_.AccessControlType.ToString()
      full_control = (($_.FileSystemRights -band [System.Security.AccessControl.FileSystemRights]::FullControl) -eq [System.Security.AccessControl.FileSystemRights]::FullControl)
      inherited = $_.IsInherited
      inheritance = $_.InheritanceFlags.ToString()
    }
  })
  $result += [ordered]@{
    protected = $acl.AreAccessRulesProtected
    owner_sid = $acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value
    rules = @($rules)
  }
}
[Console]::Out.WriteLine(($result | ConvertTo-Json -Compress -Depth 5))
"""
    inspected = _powershell(
        inspect_script,
        PHASE2E_TEST_DIRECTORY=str(directory),
        PHASE2E_TEST_PARENT1=str(directory.parent.parent),
        PHASE2E_TEST_PARENT2=str(directory.parent),
        PHASE2E_TEST_FILE=str(backup_file),
    )
    assert inspected.returncode == 0, "temporary ACL policy inspection failed"
    targets = json.loads(inspected.stdout)
    if isinstance(targets, dict):
        targets = [targets]
    owner_sid = _powershell(
        "[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value"
    )
    assert owner_sid.returncode == 0
    current_sid = owner_sid.stdout.strip()
    expected_sids = {current_sid, "S-1-5-18", "S-1-5-32-544"}
    assert len(targets) == 4
    for target in targets:
        assert target["protected"] is True
        assert target["owner_sid"] == current_sid
        assert len(target["rules"]) == 3
        assert {rule["sid"] for rule in target["rules"]} == expected_sids
        assert all(rule["allow"] == "Allow" for rule in target["rules"])
        assert all(rule["full_control"] is True for rule in target["rules"])
        assert all(rule["inherited"] is False for rule in target["rules"])
    for directory_target in targets[:3]:
        assert all("ContainerInherit" in rule["inheritance"] for rule in directory_target["rules"])
        assert all("ObjectInherit" in rule["inheritance"] for rule in directory_target["rules"])
    assert all(rule["inheritance"] == "None" for rule in targets[3]["rules"])


def test_directory_creation_or_verification_failure_stops_without_file_creation(
    monkeypatch, tmp_path
):
    directory = tmp_path / "failed-backups"
    monkeypatch.setattr(migrations.shutil, "which", lambda _: "powershell.exe")
    monkeypatch.setattr(
        migrations.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 1, stdout="", stderr="Get-Acl failed CategoryInfo : PermissionDenied"
        ),
    )

    with pytest.raises(migrations.MigrationSafetyError, match="permissions could not be verified"):
        migrations._prepare_windows_backup_directory(directory)

    assert not directory.exists()


def test_existing_directory_with_invalid_acl_fails_closed_without_rewriting_it(
    monkeypatch, tmp_path
):
    directory = tmp_path / "already-present"
    directory.mkdir()
    commands = []
    monkeypatch.setattr(migrations.shutil, "which", lambda _: "powershell.exe")

    def invalid_acl(*args, **kwargs):
        commands.append(args[0][-1])
        return subprocess.CompletedProcess(
            args[0], 1, stdout="", stderr="Backup ACL verification failed."
        )

    monkeypatch.setattr(migrations.subprocess, "run", invalid_acl)
    with pytest.raises(migrations.MigrationSafetyError):
        migrations._prepare_windows_backup_directory(directory)
    assert len(commands) == 1
    assert "Set-Acl -LiteralPath" not in commands[0]
