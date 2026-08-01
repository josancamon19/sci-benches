from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path

import pytest
from harbor.models.task.config import TaskConfig

from compbiobench.adapter import (
    DATASET_README,
    EXPECTED_TASK_COUNT,
    CompBioBenchAdapter,
    DatasetSource,
)
from compbiobench.main import build_parser


@pytest.fixture
def source_dataset(tmp_path: Path) -> Path:
    source_dir = tmp_path / "source"
    data_dir = source_dir / "data"
    data_dir.mkdir(parents=True)
    metadata_path = source_dir / "compbiobench.v1.tsv"
    fieldnames = [
        "question_id",
        "curator_name",
        "domain",
        "question_style",
        "skills_tested",
        "question",
        "internet_required",
        "gpu_preferred",
        "file_paths",
    ]
    with metadata_path.open("w", encoding="utf-8", newline="") as metadata_file:
        writer = csv.DictWriter(metadata_file, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        for index in range(EXPECTED_TASK_COUNT):
            task_id = "bam-infer-read-length-q1" if index == 0 else f"synthetic-task-{index:03d}"
            file_name = "mt.sorted.bam" if index == 0 else f"input-{index:03d}.txt"
            writer.writerow(
                {
                    "question_id": task_id,
                    "curator_name": "SN",
                    "domain": "Genomics",
                    "question_style": "Metadata Recovery",
                    "skills_tested": "Reasoning, Bioinformatics Tools",
                    "question": f"Analyze {file_name} and report answer {index}.",
                    "internet_required": "True" if index % 2 else "False",
                    "gpu_preferred": "True" if index == 99 else "False",
                    "file_paths": file_name,
                }
            )
            (data_dir / file_name).write_text(f"value-{index}\n", encoding="utf-8")
    return source_dir


def test_adapter_generates_isolated_ungraded_tasks(source_dataset: Path, tmp_path: Path) -> None:
    output_dir = tmp_path / "dataset"
    task_ids = ["bam-infer-read-length-q1", "synthetic-task-001"]
    generated = CompBioBenchAdapter(
        source=DatasetSource(source_dir=source_dataset),
        output_dir=output_dir,
        task_ids=task_ids,
    ).run()

    assert [path.name for path in generated] == task_ids
    assert (output_dir / "README.md").read_text(encoding="utf-8") == DATASET_README
    assert DATASET_README.count("\n") == 1

    for task_dir in generated:
        config = TaskConfig.model_validate_toml(
            (task_dir / "task.toml").read_text(encoding="utf-8")
        )
        assert config.agent.network_mode.value == "public"
        assert config.agent.timeout_sec == 7200.0
        assert config.verifier.network_mode.value == "no-network"
        assert config.environment.storage_mb == 10_240

        metadata = json.loads(
            (task_dir / "tests" / "task_metadata.json").read_text(encoding="utf-8")
        )
        assert set(metadata) == {
            "curator_name",
            "domain",
            "file_names",
            "gpu_preferred",
            "internet_required",
            "question_id",
            "question_style",
            "skills_tested",
            "source_repo",
            "source_revision",
        }
        assert "answer" not in metadata
        for file_name in metadata["file_names"]:
            assert (task_dir / "environment" / "task_files" / file_name).is_file()

        instruction = (task_dir / "instruction.md").read_text(encoding="utf-8")
        assert instruction.count("/app/final_answer.txt") == 2
        assert "Get any files or tools you need from the internet." in instruction
        assert (task_dir / "tests" / "test.sh").stat().st_mode & 0o111
        assert (task_dir / "solution" / "solve.sh").stat().st_mode & 0o111


def test_verifier_parses_and_retains_answer_but_always_scores_zero(tmp_path: Path) -> None:
    evaluator_path = Path(__file__).parents[1] / "src/compbiobench/task-template/tests/evaluate.py"
    spec = importlib.util.spec_from_file_location("compbiobench_evaluator", evaluator_path)
    assert spec is not None and spec.loader is not None
    evaluator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(evaluator)

    metadata_path = tmp_path / "task_metadata.json"
    metadata_path.write_text('{"question_id": "q1"}\n', encoding="utf-8")
    answer_path = tmp_path / "final_answer.txt"
    answer_path.write_text("\n1x57 \n", encoding="utf-8")
    verifier_dir = tmp_path / "verifier"
    artifact_dir = tmp_path / "artifacts"
    evaluator.METADATA_PATH = metadata_path
    evaluator.VERIFIER_DIR = verifier_dir
    evaluator.ARTIFACT_DIR = artifact_dir
    evaluator.SUBMISSION_CANDIDATES = (answer_path,)

    evaluator.main()

    result = json.loads((verifier_dir / "grader_result.json").read_text(encoding="utf-8"))
    assert result["answer"] == "1x57"
    assert result["status"] == "parsed"
    assert result["official_answers_available"] is False
    assert result["reward"] == 0.0
    assert (verifier_dir / "reward.txt").read_text(encoding="utf-8") == "0\n"
    assert (artifact_dir / "parsed_answer.txt").read_text(encoding="utf-8") == "1x57\n"


def test_adapter_rejects_unsafe_input_paths(source_dataset: Path, tmp_path: Path) -> None:
    metadata_path = source_dataset / "compbiobench.v1.tsv"
    text = metadata_path.read_text(encoding="utf-8")
    metadata_path.write_text(text.replace("mt.sorted.bam", "../mt.sorted.bam"), encoding="utf-8")

    with pytest.raises(ValueError, match="Unsafe CompBioBench input file name"):
        CompBioBenchAdapter(
            source=DatasetSource(source_dir=source_dataset),
            output_dir=tmp_path / "dataset",
            task_ids=["bam-infer-read-length-q1"],
        ).run()


def test_cli_defaults_to_public_compbiobench_dataset() -> None:
    args = build_parser().parse_args([])
    assert args.output_dir == Path("datasets/compbiobench")
    assert args.limit is None


def test_dockerfile_matches_published_base_environment() -> None:
    dockerfile = (
        Path(__file__).parents[1] / "src/compbiobench/task-template/environment/Dockerfile"
    ).read_text(encoding="utf-8")
    for expected in (
        "python=3.11",
        "biopython",
        "pysam",
        "pybedtools",
        "samtools",
        "bcftools",
        "bedtools",
        "blast",
        "minimap2",
        "bwa",
        "cyvcf2",
        "vcftools",
        "gffutils",
        "htseq",
        "subread",
        "cooler",
        "cooltools",
        "pyBigWig",
        "pyliftover",
        "nodejs",
    ):
        assert expected in dockerfile
