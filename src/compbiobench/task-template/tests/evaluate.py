#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

METADATA_PATH = Path("/tests/task_metadata.json")
VERIFIER_DIR = Path("/logs/verifier")
ARTIFACT_DIR = Path("/logs/artifacts")
SUBMISSION_CANDIDATES = (
    Path("/app/final_answer.txt"),
    Path("/app/answer.txt"),
    Path("/app/result.txt"),
    ARTIFACT_DIR / "final_answer.txt",
)


def parse_answer(text: str) -> str:
    """Mirror the upstream runner's whitespace-stripping answer parser."""
    answer = str(text).strip()
    if not answer:
        raise ValueError("empty answer")
    return answer


def parse_submission() -> tuple[dict[str, Any], Path | None]:
    metadata = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
    submission_path = next((path for path in SUBMISSION_CANDIDATES if path.is_file()), None)
    result: dict[str, Any] = {
        "answer": None,
        "official_answers_available": False,
        "question_id": metadata["question_id"],
        "reward": 0.0,
        "status": "missing_submission",
    }
    if submission_path is None:
        return result, None

    try:
        raw_answer = submission_path.read_text(encoding="utf-8")
        result.update(
            {
                "answer": parse_answer(raw_answer),
                "raw_line_count": len(raw_answer.splitlines()),
                "status": "parsed",
                "submission_path": str(submission_path),
            }
        )
    except Exception as exc:
        result.update(
            {
                "error": f"{type(exc).__name__}: {exc}",
                "status": "invalid_submission",
                "submission_path": str(submission_path),
            }
        )
    return result, submission_path


def main() -> None:
    VERIFIER_DIR.mkdir(parents=True, exist_ok=True)
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    result, submission_path = parse_submission()
    if submission_path is not None and submission_path != ARTIFACT_DIR / "final_answer.txt":
        shutil.copy2(submission_path, ARTIFACT_DIR / "final_answer.txt")
    if result["answer"] is not None:
        (ARTIFACT_DIR / "parsed_answer.txt").write_text(
            str(result["answer"]) + "\n", encoding="utf-8"
        )

    serialized = json.dumps(result, allow_nan=False, indent=2, sort_keys=True) + "\n"
    (VERIFIER_DIR / "grader_result.json").write_text(serialized, encoding="utf-8")
    (VERIFIER_DIR / "reward.txt").write_text("0\n", encoding="utf-8")
    print(serialized, end="")


if __name__ == "__main__":
    main()
