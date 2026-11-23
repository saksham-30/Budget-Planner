import subprocess
import shutil

import pytest

from backend import migrations


def test_nonzero_powershell_exit_is_fail_closed_and_logs_sanitized_hresult(
    monkeypatch, caplog
):
    stderr = (
        "Get-CimInstance failed. CategoryInfo : PermissionDenied: private-process-detail "
        "HRESULT 0x80041003 CommandLine=secret-argument password=hunter2"
    )
    monkeypatch.setattr(migrations.shutil, "which", lambda _: "powershell.exe")
    monkeypatch.setattr(
        migrations.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 17, stdout="", stderr=stderr
        ),
    )

    with pytest.raises(migrations.MigrationSafetyError, match="Cannot verify"):
        migrations._execute_windows_process_scan({})

    assert "return_code=17" in caplog.text
    assert "classification=cim_access_denied" in caplog.text
    assert "hresult=0X80041003" in caplog.text
    assert "category=PermissionDenied" in caplog.text
    assert "private-process-detail" not in caplog.text
    assert "secret-argument" not in caplog.text
    assert "hunter2" not in caplog.text
    assert "CommandLine=" not in caplog.text


def test_successful_powershell_exit_with_invalid_snapshot_fails_closed_sanitely(
    monkeypatch, caplog
):
    monkeypatch.setattr(migrations.shutil, "which", lambda _: "powershell.exe")
    monkeypatch.setattr(
        migrations.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, stdout="raw-invalid-output-secret", stderr=""
        ),
    )

    with pytest.raises(migrations.MigrationSafetyError, match="Cannot verify"):
        migrations._execute_windows_process_scan({})

    assert "stage=snapshot_deserialization" in caplog.text
    assert "classification=snapshot_serialization_failure" in caplog.text
    assert "raw-invalid-output-secret" not in caplog.text


def test_hresult_classification_does_not_retain_raw_stderr():
    diagnostic = migrations._classify_powershell_scan_error(
        b"CategoryInfo : PermissionDenied HRESULT 0x80041003 secret-command-line",
        9,
    )

    assert diagnostic == {
        "classification": "cim_access_denied",
        "return_code": 9,
        "hresult": "0X80041003",
        "category": "PermissionDenied",
    }
    assert "secret-command-line" not in repr(diagnostic)


def test_process_timestamp_conversion_error_has_a_distinct_safe_classification():
    diagnostic = migrations._classify_powershell_scan_error(
        "Convert-CreationDateToFileTime MethodInvocationException local-details",
        1,
    )

    assert diagnostic["classification"] == "process_metadata_conversion_failure"
    assert "local-details" not in repr(diagnostic)


def test_creation_time_conversion_accepts_cim_datetime_and_dmtf_string():
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if powershell is None:
        pytest.skip("Windows PowerShell is not available for the synthetic conversion test.")
    command = migrations._WINDOWS_CREATION_TIME_HELPER + r"""
$date = [datetime]::SpecifyKind([datetime]'2026-11-22T23:06:59', [DateTimeKind]::Utc)
$fromDate = Convert-CreationDateToFileTime $date
$fromDmtf = Convert-CreationDateToFileTime '20261122230659.000000+000'
Write-Output "$fromDate,$fromDmtf"
"""

    result = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, "Synthetic PowerShell date conversion failed."
    values = result.stdout.strip().split(",")
    assert len(values) == 2
    assert values[0] == values[1]
