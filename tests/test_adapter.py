from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from harbor.models.task.config import TaskConfig

from genebench_pro.adapter import EXPECTED_PROBLEM_COUNT, GeneBenchProAdapter


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def source_package(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    source.mkdir()
    (source / "reference_grader.py").write_text("def evaluate(config, submission): return {}\n")

    manifest_problems = []
    for index in range(EXPECTED_PROBLEM_COUNT):
        task_id = f"problem_{index}"
        problem_dir = source / "problems" / task_id
        data_dir = problem_dir / "data_files"
        data_dir.mkdir(parents=True)
        data_path = data_dir / "input.tsv"
        data_path.write_text("value\n1\n", encoding="utf-8")

        config = {
            "id": task_id,
            "eval_uuid": f"00000000-0000-4000-8000-{index:012d}",
            "task": (
                f"Analyze synthetic problem {index}.\n"
                "Return your final answer as exactly one JSON object.\n"
                "Do not wrap the JSON in markdown.\n"
                "Do not add prose before or after the JSON.\n"
                "Do not omit any keys shown in the example.\n"
                "Return the JSON object in your final answer:\n"
                '{"answer": {"value": <int>}, "reasoning": "<method>"}'
            ),
            "data_files": ["data_files/input.tsv"],
            "ground_truth": {"value": index},
            "grader": {
                "type": "numeric_tolerance",
                "config": {"key": "value", "absolute_tolerance": 0.0},
            },
        }
        config_path = problem_dir / "eval_config.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")

        config_name = config_path.relative_to(source).as_posix()
        data_name = data_path.relative_to(source).as_posix()
        manifest_problems.append(
            {
                "release_order": index,
                "eval_id": task_id,
                "title": f"Problem {index}",
                "domain": "Test genomics",
                "eval_uuid": config["eval_uuid"],
                "problem_dir": problem_dir.relative_to(source).as_posix(),
                "eval_config": config_name,
                "files": [
                    {"path": config_name, "sha256": _sha256(config_path)},
                    {"path": data_name, "sha256": _sha256(data_path)},
                ],
            }
        )

    (source / "manifest.json").write_text(
        json.dumps({"problem_count": EXPECTED_PROBLEM_COUNT, "problems": manifest_problems}),
        encoding="utf-8",
    )
    return source


def test_adapter_generates_valid_isolated_tasks(source_package: Path, tmp_path: Path) -> None:
    output_dir = tmp_path / "dataset"
    generated = GeneBenchProAdapter(
        source_dir=source_package,
        output_dir=output_dir,
        source_revision="test-revision",
    ).run()

    assert len(generated) == EXPECTED_PROBLEM_COUNT
    for task_dir in generated:
        TaskConfig.model_validate_toml((task_dir / "task.toml").read_text(encoding="utf-8"))
        instruction = (task_dir / "instruction.md").read_text(encoding="utf-8")
        assert instruction.startswith(f"Analyze synthetic {task_dir.name.replace('_', ' ')}.")
        assert instruction.count("/app/result.json") == 1
        assert "Return the JSON object in your final answer:" not in instruction
        assert "Return your final answer as exactly one JSON object." in instruction
        assert "Do not wrap the JSON in markdown." not in instruction
        assert "Do not add prose before or after the JSON." not in instruction
        assert "Do not omit any keys shown in the example." not in instruction
        assert "Harbor execution requirements" not in instruction
        assert "/logs/" not in instruction
        assert (task_dir / "environment" / "Dockerfile").is_file()
        assert (task_dir / "environment" / "data_files" / "input.tsv").is_file()
        assert not (task_dir / "environment" / "eval_config.json").exists()
        assert (task_dir / "tests" / "eval_config.json").is_file()
        assert (task_dir / "tests" / "reference_grader.py").is_file()
        assert (task_dir / "solution" / "oracle_answer.json").is_file()
        assert (task_dir / "solution" / "solve.sh").stat().st_mode & 0o111
        assert (task_dir / "tests" / "test.sh").stat().st_mode & 0o111


def test_adapter_filters_task_ids_and_rejects_unknown_ids(
    source_package: Path, tmp_path: Path
) -> None:
    selected_dir = tmp_path / "selected"
    generated = GeneBenchProAdapter(
        source_dir=source_package,
        output_dir=selected_dir,
        task_ids=["problem_7", "problem_3"],
        limit=1,
    ).run()
    assert [path.name for path in generated] == ["problem_3"]

    with pytest.raises(ValueError, match="Unknown task ID"):
        GeneBenchProAdapter(
            source_dir=source_package,
            output_dir=tmp_path / "unknown",
            task_ids=["not_a_problem"],
        ).run()
