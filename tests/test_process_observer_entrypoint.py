import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OBSERVER_SCRIPT = PROJECT_ROOT / "scripts" / "observe_auth_processes.py"


def test_observer_script_entrypoint_imports_from_outside_project_root(tmp_path):
    result = subprocess.run(
        [sys.executable, str(OBSERVER_SCRIPT), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Capture a sanitized process-only snapshot" in result.stdout
    assert "--observe" in result.stdout


def test_observer_module_entrypoint_imports_from_project_root():
    result = subprocess.run(
        [sys.executable, "-m", "scripts.observe_auth_processes", "--help"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Capture a sanitized process-only snapshot" in result.stdout
    assert "--observe" in result.stdout
