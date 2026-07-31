#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from reference_grader import evaluate

CONFIG_PATH = Path("/tests/eval_config.json")
VERIFIER_DIR = Path("/logs/verifier")
ARTIFACT_DIR = Path("/logs/artifacts")
SUBMISSION_CANDIDATES = (
    Path("/app/result.json"),
    ARTIFACT_DIR / "result.json",
)


def grade() -> tuple[dict[str, Any], Path | None]:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    submission_path = next((path for path in SUBMISSION_CANDIDATES if path.is_file()), None)
    if submission_path is None:
        return (
            {
                "eval_id": config["id"],
                "passed": False,
                "score": 0.0,
                "details": [{"status": "missing_submission", "expected": "/app/result.json"}],
            },
            None,
        )

    try:
        submission = json.loads(submission_path.read_text(encoding="utf-8"))
        return evaluate(config, submission), submission_path
    except Exception as exc:
        return (
            {
                "eval_id": config["id"],
                "passed": False,
                "score": 0.0,
                "details": [
                    {
                        "status": "invalid_submission_or_grader_error",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                ],
            },
            submission_path,
        )


def main() -> None:
    VERIFIER_DIR.mkdir(parents=True, exist_ok=True)
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    result, submission_path = grade()
    if submission_path is not None and submission_path != ARTIFACT_DIR / "result.json":
        shutil.copy2(submission_path, ARTIFACT_DIR / "result.json")

    serialized = json.dumps(result, allow_nan=False, indent=2, sort_keys=True) + "\n"
    (VERIFIER_DIR / "grader_result.json").write_text(serialized, encoding="utf-8")
    (VERIFIER_DIR / "reward.txt").write_text(
        "1\n" if result.get("passed") is True else "0\n",
        encoding="utf-8",
    )
    print(serialized, end="")


if __name__ == "__main__":
    main()
