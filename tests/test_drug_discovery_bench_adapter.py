from __future__ import annotations

import json
from pathlib import Path

import pytest
from harbor.models.task.config import TaskConfig

from drug_discovery_bench.adapter import (
    DEFAULT_HF_REVISION,
    RubricSource,
    discover_tasks,
    import_tasks,
)
from drug_discovery_bench.main import build_parser


@pytest.fixture
def source_checkout(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    for index in range(3):
        task_id = f"{index:024x}"
        task_dir = source / "benchmark" / "tasks" / task_id
        environment_dir = task_dir / "environment"
        tests_dir = task_dir / "tests"
        environment_dir.mkdir(parents=True)
        tests_dir.mkdir()
        prompt = f"Solve official DrugDiscoveryBench task {task_id}."
        healthcheck = (
            'test "$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 '
            'http://scale.com)" = "403"'
        )
        docker_image_line = (
            ""
            if index == 0
            else 'docker_image = "ghcr.io/scaleapi/drugdiscoverybench:1.0.0-lightweight"\n'
        )
        (task_dir / "instruction.md").write_text(
            f"{prompt}\n\n---\n\nWrite only the answer to /workspace/answer.md.\n",
            encoding="utf-8",
        )
        (task_dir / "task.toml").write_text(
            f"""schema_version = "1.1"

[task]
name = "biomni/{task_id}"
description = "biomedical research"
keywords = ["biomni", "scale-data"]

[environment]
{docker_image_line}os = "linux"
cpus = 2
memory_mb = 8192
storage_mb = 4096
allow_internet = true

[environment.env]
BIOMNI_DATA_PATH = "/workspace/data"

[environment.healthcheck]
command = '{healthcheck}'
start_period_sec = 30.0
start_interval_sec = 2.0
interval_sec = 3.0
retries = 5

[agent]
timeout_sec = 7200.0

[verifier]
timeout_sec = 600.0

[verifier.env]
JUDGE_BASE_URL = "${{JUDGE_BASE_URL}}"
JUDGE_API_KEY = "${{JUDGE_API_KEY}}"
JUDGE_MODEL = "${{JUDGE_MODEL:-gpt-4o}}"
""",
            encoding="utf-8",
        )
        (environment_dir / "Dockerfile").write_text(
            "FROM ghcr.io/scaleapi/drugdiscoverybench:1.0.0-lightweight\nWORKDIR /workspace\n",
            encoding="utf-8",
        )
        (tests_dir / "judge.py").write_text("print('official judge')\n", encoding="utf-8")
        test_script = tests_dir / "test.sh"
        test_script.write_text("#!/bin/bash\npython3 /tests/judge.py\n", encoding="utf-8")
        test_script.chmod(0o755)
        (tests_dir / "rubrics.json").write_text(
            json.dumps(
                {
                    "task_id": task_id,
                    "prompt": prompt,
                    "ground_truth": "",
                    "outcome_rubrics": [],
                    "process_rubrics": [],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return source


@pytest.fixture
def rubrics_file(tmp_path: Path) -> Path:
    path = tmp_path / "tasks.jsonl"
    rows = []
    for index in range(3):
        task_id = f"{index:024x}"
        rows.append(
            {
                "task_id": task_id,
                "ground_truth": f"reference answer {index}",
                "outcome_rubrics": [
                    {
                        "title": f"Reports reference answer {index}",
                        "weight": "+10",
                    }
                ],
                "process_rubrics": [
                    {
                        "title": "Uses a scientifically valid method",
                        "weight": "+2",
                        "justification": "Required for process parity.",
                        "category": "methodology",
                    }
                ],
            }
        )
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return path


def test_discover_tasks_finds_upstream_release(source_checkout: Path) -> None:
    tasks = discover_tasks(source_checkout)

    assert [task.name for task in tasks] == [
        "000000000000000000000000",
        "000000000000000000000001",
        "000000000000000000000002",
    ]


def test_importer_preserves_tasks_and_populates_official_rubrics(
    source_checkout: Path,
    rubrics_file: Path,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "dataset"
    selected = ["000000000000000000000000", "000000000000000000000002"]
    generated = import_tasks(
        source_dir=source_checkout,
        rubric_source=RubricSource(source_file=rubrics_file),
        output_dir=output_dir,
        source_revision="test-revision",
        task_ids=selected,
    )

    assert [path.name for path in generated] == selected
    for task_dir in generated:
        source_task = source_checkout / "benchmark" / "tasks" / task_dir.name
        for relative in (
            "instruction.md",
            "tests/judge.py",
            "tests/test.sh",
        ):
            assert (task_dir / relative).read_bytes() == (source_task / relative).read_bytes()

        dockerfile = (task_dir / "environment" / "Dockerfile").read_text()
        assert dockerfile.startswith(
            (source_task / "environment" / "Dockerfile").read_text().rstrip()
        )
        assert dockerfile.endswith("USER root\n")

        bundle = json.loads((task_dir / "tests" / "rubrics.json").read_text())
        index = int(task_dir.name, 16)
        assert bundle["ground_truth"] == f"reference answer {index}"
        assert bundle["outcome_rubrics"] == [
            {
                "title": f"Reports reference answer {index}",
                "weight": "+10",
                "justification": "",
                "category": "",
            }
        ]
        assert bundle["process_rubrics"][0]["category"] == "methodology"
        assert (task_dir / "tests" / "test.sh").stat().st_mode & 0o111

        config = TaskConfig.model_validate_toml(
            (task_dir / "task.toml").read_text(encoding="utf-8")
        )
        assert config.agent.timeout_sec == 7200.0
        assert config.verifier.timeout_sec == 600.0
        assert config.environment.memory_mb == 8192
        assert config.environment.storage_mb == 4096
        assert config.environment.network_mode.value == "public"
        assert config.environment.docker_image is None
        assert config.agent.user == "biomni"
        assert "egress_proxy.py" in config.environment.healthcheck.command


def test_cli_defaults_to_pinned_full_release() -> None:
    args = build_parser().parse_args([])

    assert args.output_dir == Path("datasets/drug-discovery-bench")
    assert args.limit is None
    assert args.hf_revision == DEFAULT_HF_REVISION
