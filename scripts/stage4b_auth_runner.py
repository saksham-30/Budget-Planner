"""Interactive, observable launcher for the local authentication CLI.

The child inherits the console for getpass input. Its output is streamed to the
same console and a timestamped log; password input is never read by this runner.
"""

from __future__ import annotations

import argparse
import os
import queue
import re
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT_TEXT = str(PROJECT_ROOT)
if PROJECT_ROOT_TEXT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT_TEXT)

from backend.process_identity import current_process_creation_filetime


LOG_DIRECTORY = PROJECT_ROOT / "evaluation" / "phase2e_auth_ownership" / "runner_logs"
_REDACTIONS = (
    (re.compile(r"(?i)(password|passwd|token|secret|authorization)\s*([=:])\s*[^\s,;]+"), r"\1\2[REDACTED]"),
    (re.compile(r"(?i)\$argon2(?:id|i|d)\$[^\s]+"), "[REDACTED_PASSWORD_HASH]"),
)


def _redact(line: str) -> str:
    for pattern, replacement in _REDACTIONS:
        line = pattern.sub(replacement, line)
    return line


def _python_executable(project_root: Path) -> Path:
    candidate = project_root / "venv" / "Scripts" / "python.exe"
    if not candidate.is_file():
        raise FileNotFoundError("Project virtual-environment Python was not found.")
    return candidate


def _auth_cli_environment(
    base_environment: dict[str, str], runner_pid: int, runner_creation_filetime: int
) -> dict[str, str]:
    child_environment = dict(base_environment)
    child_environment["PHASE2E_AUTH_RUNNER_PID"] = str(runner_pid)
    child_environment["PHASE2E_AUTH_RUNNER_CREATION_FILETIME"] = str(runner_creation_filetime)
    return child_environment


def _classify(output: str, return_code: int) -> str:
    if "Traceback (most recent call last):" in output:
        return "python_exception"
    if "CLI_RESULT: backup_failure" in output:
        return "backup_stage_failure"
    if "CLI_RESULT: preflight_failure" in output:
        return "cli_preflight_failure"
    if "CLI_RESULT: migration_failure" in output:
        return "cli_migration_failure"
    if return_code == 0 and "CLI_RESULT: normal_exit" in output:
        return "normal_cli_exit"
    if return_code == 0:
        return "normal_cli_exit"
    return "cli_nonzero_exit_unclassified"


def run_command(
    command: Sequence[str],
    log_directory: Path,
    *,
    cwd: Path = PROJECT_ROOT,
    env: dict[str, str] | None = None,
) -> int:
    """Run a console-interactive child, mirror output, log it, and return its code."""
    log_directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    log_path = log_directory / f"stage4b_auth_runner_{timestamp}.log"
    header = (
        f"Started UTC: {datetime.now(timezone.utc).isoformat()}\n"
        f"Executable: {Path(command[0]).name}\n"
        "Invocation: child arguments omitted\n"
        "Password input: inherited directly by the child console; never captured by runner\n"
    )
    events: queue.Queue[tuple[str, str | None]] = queue.Queue()
    collected: list[str] = []
    try:
        child = subprocess.Popen(
            list(command), cwd=cwd, stdin=None, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
            bufsize=1, env=env,
        )
    except OSError as exc:
        safe_reason = type(exc).__name__
        log_path.write_text(
            header + f"Runner result: python_launch_failure\nLaunch error type: {safe_reason}\n",
            encoding="utf-8",
        )
        print(f"Runner could not launch Python ({safe_reason}). Diagnostic log: {log_path}", file=sys.stderr)
        return 127

    def pump(stream, label: str) -> None:
        try:
            for line in iter(stream.readline, ""):
                events.put((label, line))
        finally:
            stream.close()
            events.put((label, None))

    threads = [
        threading.Thread(target=pump, args=(child.stdout, "stdout"), daemon=True),
        threading.Thread(target=pump, args=(child.stderr, "stderr"), daemon=True),
    ]
    def capture_exit_code() -> None:
        events.put(("exit", str(child.wait())))

    exit_thread = threading.Thread(target=capture_exit_code, daemon=True)
    threads.append(exit_thread)
    for thread in threads:
        thread.start()

    ended_streams = 0
    return_code: int | None = None
    with log_path.open("w", encoding="utf-8", newline="") as log:
        log.write(header)
        while ended_streams < 2 or return_code is None:
            label, line = events.get()
            if label == "exit":
                return_code = int(line)
                log.write(f"Python CLI exit code captured immediately: {return_code}\n")
                log.flush()
                continue
            if line is None:
                ended_streams += 1
                continue
            safe_line = _redact(line)
            collected.append(safe_line)
            log.write(f"[{label}] {safe_line}")
            log.flush()
            target = sys.stderr if label == "stderr" else sys.stdout
            target.write(safe_line)
            target.flush()
        outcome = _classify("".join(collected), return_code)
        log.write(f"\nRunner result: {outcome}\nPython CLI exit code: {return_code}\n")
        log.write(f"Finished UTC: {datetime.now(timezone.utc).isoformat()}\n")
    for thread in threads:
        thread.join()
    print(f"Diagnostic log: {log_path}")
    return return_code


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root", type=Path, default=PROJECT_ROOT,
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--log-directory", type=Path, default=LOG_DIRECTORY)
    args = parser.parse_args(argv)
    try:
        python = _python_executable(args.project_root)
    except OSError as exc:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
        args.log_directory.mkdir(parents=True, exist_ok=True)
        log_path = args.log_directory / f"stage4b_auth_runner_{timestamp}.log"
        log_path.write_text(
            f"Runner result: python_resolution_failure\nError type: {type(exc).__name__}\n",
            encoding="utf-8",
        )
        print(f"Runner could not resolve project Python. Diagnostic log: {log_path}", file=sys.stderr)
        return 127
    child_environment = os.environ.copy()
    if os.name == "nt":
        try:
            child_environment = _auth_cli_environment(
                child_environment,
                os.getpid(),
                current_process_creation_filetime(),
            )
        except OSError as exc:
            print(
                f"Runner identity verification unavailable ({type(exc).__name__}); CLI not launched.",
                file=sys.stderr,
            )
            return 125
    return run_command(
        [str(python), "-m", "backend.manage_auth"],
        args.log_directory,
        cwd=args.project_root,
        env=child_environment,
    )


if __name__ == "__main__":
    raise SystemExit(main())
