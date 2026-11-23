import sys

from scripts import stage4b_auth_runner as runner


def test_runner_captures_output_and_propagates_child_exit_code(tmp_path, capsys):
    code = runner.run_command(
        [
            sys.executable,
            "-c",
            "import sys; print('CLI_STAGE: preflight'); print('safe diagnostic', file=sys.stderr); sys.exit(23)",
        ],
        tmp_path / "logs",
    )

    assert code == 23
    captured = capsys.readouterr()
    assert "CLI_STAGE: preflight" in captured.out
    assert "safe diagnostic" in captured.err
    logs = list((tmp_path / "logs").glob("stage4b_auth_runner_*.log"))
    assert len(logs) == 1
    contents = logs[0].read_text(encoding="utf-8")
    assert "Runner result: cli_nonzero_exit_unclassified" in contents
    assert "Python CLI exit code: 23" in contents
    assert "safe diagnostic" in contents


def test_runner_logs_python_launch_failure_without_losing_status(tmp_path, capsys):
    code = runner.run_command(
        [str(tmp_path / "missing-python.exe"), "-m", "backend.manage_auth"],
        tmp_path / "logs",
    )

    assert code == 127
    assert "could not launch Python" in capsys.readouterr().err
    logs = list((tmp_path / "logs").glob("stage4b_auth_runner_*.log"))
    assert len(logs) == 1
    assert "Runner result: python_launch_failure" in logs[0].read_text(encoding="utf-8")


def test_runner_redacts_sensitive_diagnostic_values(tmp_path):
    code = runner.run_command(
        [sys.executable, "-c", "print('password=hunter2 $argon2id$v=19$secret')", "arg-secret"],
        tmp_path / "logs",
    )

    assert code == 0
    log = next((tmp_path / "logs").glob("*.log")).read_text(encoding="utf-8")
    assert "hunter2" not in log
    assert "$argon2id$v=19$secret" not in log
    assert "[REDACTED]" in log
    assert "[REDACTED_PASSWORD_HASH]" in log
    assert "arg-secret" not in log


def test_runner_classifies_preflight_backup_exception_and_normal_exit():
    assert runner._classify("CLI_RESULT: preflight_failure", 1) == "cli_preflight_failure"
    assert runner._classify("CLI_RESULT: backup_failure", 1) == "backup_stage_failure"
    assert runner._classify(
        "CLI_RESULT: backup_failure\nTraceback (most recent call last):", 1
    ) == "python_exception"
    assert runner._classify("CLI_RESULT: normal_exit", 0) == "normal_cli_exit"


def test_runner_passes_only_its_pid_and_creation_identity_to_cli():
    child_environment = runner._auth_cli_environment(
        {"SAFE_EXISTING_SETTING": "preserved"}, runner_pid=4616, runner_creation_filetime=1234567890
    )

    assert child_environment["SAFE_EXISTING_SETTING"] == "preserved"
    assert child_environment["PHASE2E_AUTH_RUNNER_PID"] == "4616"
    assert child_environment["PHASE2E_AUTH_RUNNER_CREATION_FILETIME"] == "1234567890"
    assert "CommandLine" not in repr(child_environment)
