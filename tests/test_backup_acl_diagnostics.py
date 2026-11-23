import subprocess

import pytest

from backend import migrations


def test_acl_subprocess_failure_logs_sanitized_exit_category_and_hresult(
    monkeypatch, tmp_path, caplog
):
    raw_stderr = (
        "Set-Acl failed. CategoryInfo : PermissionDenied: C:\\private\\account "
        "HRESULT 0x80070005 CommandLine=private-argument password=secret-value"
    )
    monkeypatch.setattr(migrations.shutil, "which", lambda _: "powershell.exe")
    monkeypatch.setattr(
        migrations.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 5, stdout="", stderr=raw_stderr
        ),
    )

    with pytest.raises(migrations.MigrationSafetyError, match="permissions could not be verified"):
        migrations._apply_windows_backup_file_acl(
            tmp_path / "temporary-directory", tmp_path / "temporary-file"
        )

    assert "return_code=5" in caplog.text
    assert "classification=acl_permission_denied" in caplog.text
    assert "hresult=0X80070005" in caplog.text
    assert "category=PermissionDenied" in caplog.text
    assert "C:\\private\\account" not in caplog.text
    assert "private-argument" not in caplog.text
    assert "secret-value" not in caplog.text


@pytest.mark.parametrize(
    ("stderr", "classification"),
    [
        ("Backup ACL verification failed.", "acl_rule_verification_failure"),
        ("ParserError in temporary ACL script", "powershell_execution_failure"),
        ("Set-Acl failed", "acl_application_failure"),
        ("Get-Acl failed", "acl_inspection_failure"),
        ("New-Object FileSystemAccessRule failed", "acl_rule_construction_failure"),
    ],
)
def test_acl_failure_classifier_returns_only_safe_metadata(stderr, classification):
    result = migrations._classify_backup_acl_error(stderr, 1)

    assert result["classification"] == classification
    assert result["return_code"] == 1
    assert set(result) == {"classification", "return_code", "hresult", "category"}
