#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import shutil
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

CONFIG_PATH = Path("/tests/upstream_eval.json")
VERIFIER_DIR = Path("/logs/verifier")
ARTIFACT_DIR = Path("/logs/artifacts")
SUBMISSION_CANDIDATES = (
    Path("/app/result.json"),
    ARTIFACT_DIR / "result.json",
)


def _safe_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _safe_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_json(item) for item in value]
    if isinstance(value, set):
        return sorted((_safe_json(item) for item in value), key=str)
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _failure(eval_id: str, grader_type: str, status: str, detail: str) -> dict[str, Any]:
    return {
        "eval_id": eval_id,
        "official_verifier_available": True,
        "grader_package": "latch-eval-tools",
        "grader_type": grader_type,
        "passed": False,
        "score": 0.0,
        "status": status,
        "error": detail,
    }


def grade() -> tuple[dict[str, Any], Path | None]:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    eval_id = str(config.get("id", "unknown"))
    grader_spec = config.get("grader")
    if not isinstance(grader_spec, dict):
        return _failure(eval_id, "unknown", "invalid_grader_config", "missing grader"), None
    grader_type = str(grader_spec.get("type", "unknown"))
    grader_config = grader_spec.get("config")
    if not isinstance(grader_config, dict):
        return (
            _failure(eval_id, grader_type, "invalid_grader_config", "missing grader config"),
            None,
        )

    submission_path = next((path for path in SUBMISSION_CANDIDATES if path.is_file()), None)
    if submission_path is None:
        return (
            _failure(
                eval_id,
                grader_type,
                "missing_submission",
                "expected /app/result.json",
            ),
            None,
        )

    try:
        submission = json.loads(submission_path.read_text(encoding="utf-8"))
        if not isinstance(submission, dict):
            raise TypeError("submission root must be a JSON object")
        from latch_eval_tools.graders import get_grader

        grader_result = get_grader(grader_type).evaluate_answer(submission, grader_config)
        package_version = version("latch-eval-tools")
        score = float(grader_result.score)
        if not math.isfinite(score):
            raise ValueError("grader returned a non-finite score")
        score = min(1.0, max(0.0, score))
        return (
            {
                "eval_id": eval_id,
                "official_verifier_available": True,
                "grader_package": "latch-eval-tools",
                "grader_package_version": package_version,
                "grader_type": grader_type,
                "passed": grader_result.passed is True,
                "score": score,
                "status": "passed" if grader_result.passed is True else "failed",
                "field_scores": _safe_json(grader_result.field_scores),
                "metrics": _safe_json(grader_result.metrics),
                "reasoning": grader_result.reasoning,
                "agent_answer": _safe_json(grader_result.agent_answer),
            },
            submission_path,
        )
    except Exception as exc:
        try:
            package_version = version("latch-eval-tools")
        except PackageNotFoundError:
            package_version = "not-installed"
        result = _failure(
            eval_id,
            grader_type,
            "invalid_submission_or_grader_error",
            f"{type(exc).__name__}: {exc}",
        )
        result["grader_package_version"] = package_version
        return result, submission_path


def main() -> None:
    VERIFIER_DIR.mkdir(parents=True, exist_ok=True)
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    result, submission_path = grade()
    if submission_path is not None and submission_path != ARTIFACT_DIR / "result.json":
        shutil.copy2(submission_path, ARTIFACT_DIR / "result.json")

    serialized = json.dumps(result, allow_nan=False, indent=2, sort_keys=True) + "\n"
    (VERIFIER_DIR / "grader_result.json").write_text(serialized, encoding="utf-8")
    score = float(result.get("score", 0.0))
    (VERIFIER_DIR / "reward.txt").write_text(f"{score:.12g}\n", encoding="utf-8")
    print(serialized, end="")


if __name__ == "__main__":
    main()
