"""Capture a sanitized process-only snapshot for authentication preflight diagnosis.

This tool does not import the auth CLI, open SQLite, create backups, or migrate.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT_TEXT = str(PROJECT_ROOT)
if PROJECT_ROOT_TEXT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT_TEXT)

from backend.migrations import MigrationSafetyError, _application_process_snapshot


DATABASE_PATH_FOR_MATCHING_ONLY = (
    PROJECT_ROOT / "backend" / "data" / "travel_planner.sqlite3"
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--observe",
        action="store_true",
        help="Capture three bounded process snapshots (initial, recheck, followup).",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        snapshot = _application_process_snapshot(
            DATABASE_PATH_FOR_MATCHING_ONLY,
            observation_mode=args.observe,
        )
    except MigrationSafetyError as exc:
        print(f"Process snapshot failed closed: {exc}")
        return 2

    print(
        "Process snapshot complete: "
        f"current_cli_pid={snapshot.current_cli_pid} "
        f"excluded_pids={','.join(map(str, snapshot.excluded_pids))} "
        f"candidate_count={len(snapshot.candidates)} "
        f"observation_mode={snapshot.observation_mode}"
    )
    for candidate in snapshot.candidates:
        first_observation = candidate.observations[0] if candidate.observations else None
        print(
            "Candidate: "
            f"pid={candidate.pid} parent_pid={candidate.parent_pid or 'unknown'} "
            f"executable={candidate.executable} creation_time={candidate.creation_time or 'unknown'} "
            f"executable_path="
            f"{first_observation.executable_path if first_observation else 'unknown'} "
            f"classification={candidate.classification} "
            f"match_reasons={','.join(candidate.match_reasons)} "
            f"recheck={candidate.recheck_status} "
            f"parent_recheck={candidate.parent_recheck_status}"
        )
        for observation in candidate.observations:
            print(
                "Observation: "
                f"candidate_pid={candidate.pid} snapshot={observation.snapshot} "
                f"exists={observation.exists} creation_time={observation.creation_time or 'unknown'} "
                f"parent_pid={observation.parent_pid or 'unknown'} "
                f"parent_exists={observation.parent_exists} "
                f"parent_executable={observation.parent_executable or 'unknown'} "
                f"parent_executable_path={observation.parent_executable_path or 'unknown'} "
                f"parent_creation_time={observation.parent_creation_time or 'unknown'}"
            )
    return 1 if snapshot.candidates else 0


if __name__ == "__main__":
    raise SystemExit(main())
