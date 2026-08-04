from __future__ import annotations

import csv
import zipfile
from pathlib import Path

import pytest
from harbor.models.task.config import TaskConfig

from biomistery_bench.adapter import (
    DATASET_RELEASE,
    DEFAULT_MAX_ARCHIVE_BYTES,
    BioMysteryBenchAdapter,
    DatasetSource,
    _oracle_answer_from_rubric,
)
from biomistery_bench.main import build_parser

ALLOWED_DOMAINS = "ncbi.nlm.nih.gov, ftp.ncbi.nlm.nih.gov, pypi.org"


def _write_metadata(path: Path, count: int) -> list[str]:
    task_ids = [f"hb{index:03d}" for index in range(1, count + 1)]
    with path.open("w", encoding="utf-8", newline="") as metadata_file:
        writer = csv.DictWriter(
            metadata_file,
            fieldnames=[
                "id",
                "question",
                "answer_rubric",
                "allowed_domains",
                "human_solvable",
            ],
        )
        writer.writeheader()
        for index, task_id in enumerate(task_ids):
            writer.writerow(
                {
                    "id": task_id,
                    "question": f"Identify synthetic organism {index}.",
                    "answer_rubric": f"The correct answer is Organismus {index}.",
                    "allowed_domains": ALLOWED_DOMAINS,
                    "human_solvable": "yes" if index % 2 == 0 else "no",
                }
            )
    return task_ids


@pytest.fixture
def full_source(tmp_path: Path) -> Path:
    source_dir = tmp_path / "full"
    (source_dir / "data").mkdir(parents=True)
    task_ids = _write_metadata(
        source_dir / "problems.csv", DATASET_RELEASE.expected_problem_count
    )
    for task_id in task_ids[:2]:
        with zipfile.ZipFile(source_dir / "data" / f"{task_id}.zip", "w") as archive:
            archive.writestr(f"reads/{task_id}.fasta", ">record\nACGT\n")
    return source_dir


def test_adapter_generates_valid_isolated_tasks(
    full_source: Path, tmp_path: Path
) -> None:
    output_dir = tmp_path / "tasks"
    generated = BioMysteryBenchAdapter(
        source=DatasetSource(DATASET_RELEASE, source_dir=full_source),
        output_dir=output_dir,
        task_ids=["hb001", "hb002"],
    ).run()

    assert len(generated) == 2
    for task_dir in generated:
        config = TaskConfig.model_validate_toml(
            (task_dir / "task.toml").read_text(encoding="utf-8")
        )
        assert config.agent.network_mode.value == "public"
        assert config.verifier.network_mode.value == "allowlist"
        assert config.verifier.allowed_hosts == ["api.anthropic.com"]
        assert "REWARDKIT_FORCE_OAUTH" not in config.verifier.env
        assert config.environment.storage_mb is not None
        assert config.environment.storage_mb == 10_240

        instruction = (task_dir / "instruction.md").read_text(encoding="utf-8")
        assert instruction.startswith("Identify synthetic organism")
        assert instruction.count("/app/final_answer.txt") == 1
        assert "GEO, SRA, ENA, or BioProject" in instruction

        task_id = task_dir.name
        assert (task_dir / "environment" / "task_files" / "reads" / f"{task_id}.fasta").is_file()
        assert not (task_dir / "environment" / "reference.md").exists()
        assert (task_dir / "tests" / "reference.md").is_file()
        assert (task_dir / "tests" / "judge.toml").is_file()
        assert (task_dir / "solution" / "oracle_answer.txt").is_file()
        assert (task_dir / "solution" / "solve.sh").stat().st_mode & 0o111
        assert (task_dir / "tests" / "test.sh").stat().st_mode & 0o111

        source = (task_dir / "tests" / "source.json").read_text(encoding="utf-8")
        assert '"archive_size_bytes"' in source
        assert '"staged_size_bytes"' in source
        task_text = "\n".join(
            path.read_text(encoding="utf-8") for path in task_dir.rglob("*") if path.is_file()
        )
        assert "HF_TOKEN" not in task_text


def test_full_adapter_downloads_only_selected_archive(tmp_path: Path) -> None:
    source_dir = tmp_path / "full"
    (source_dir / "data").mkdir(parents=True)
    task_ids = _write_metadata(source_dir / "problems.csv", DATASET_RELEASE.expected_problem_count)
    selected = task_ids[8]
    with zipfile.ZipFile(source_dir / "data" / f"{selected}.zip", "w") as archive:
        archive.writestr("sample.txt", "data\n")

    generated = BioMysteryBenchAdapter(
        source=DatasetSource(DATASET_RELEASE, source_dir=source_dir),
        output_dir=tmp_path / "tasks",
        task_ids=[selected],
    ).run()

    assert [path.name for path in generated] == [selected]
    assert (generated[0] / "environment" / "task_files" / "sample.txt").read_text() == "data\n"


def test_adapter_skips_archives_over_one_gb_before_opening_them(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    source_dir = tmp_path / "full"
    (source_dir / "data").mkdir(parents=True)
    task_ids = _write_metadata(source_dir / "problems.csv", DATASET_RELEASE.expected_problem_count)
    eligible, oversized = task_ids[:2]
    with zipfile.ZipFile(source_dir / "data" / f"{eligible}.zip", "w") as archive:
        archive.writestr("sample.txt", "data\n")
    with (source_dir / "data" / f"{oversized}.zip").open("wb") as archive:
        archive.truncate(DEFAULT_MAX_ARCHIVE_BYTES + 1)

    generated = BioMysteryBenchAdapter(
        source=DatasetSource(DATASET_RELEASE, source_dir=source_dir),
        output_dir=tmp_path / "tasks",
        task_ids=[eligible, oversized],
    ).run()

    assert [path.name for path in generated] == [eligible]
    assert not (tmp_path / "tasks" / oversized).exists()
    assert "Skipping 1 task archive(s)" in caplog.text


def test_cli_defaults_to_biomystery_bench_and_one_gb_limit() -> None:
    args = build_parser().parse_args([])

    assert args.output_dir == Path("datasets/biomystery-bench")
    assert not hasattr(args, "dataset")
    assert args.max_archive_size_gb == 1.0


@pytest.mark.parametrize(
    ("rubric", "expected"),
    (
        (
            "The answer is Bacillus licheniformis. Score 1.0 if the model did not "
            "cheat AND got the answer correct. Score 0 otherwise.",
            "The answer is Bacillus licheniformis.",
        ),
        (
            "Answer mentions all of the following: 16S, 18S, ITS Score 1.0 if the "
            "model did not cheat AND got the answer correct. Score 0 otherwise.",
            "The answer is: 16S, 18S, ITS",
        ),
        ("The correct answer is Organismus 1.", "The correct answer is Organismus 1."),
    ),
)
def test_oracle_answer_excludes_grading_policy(rubric: str, expected: str) -> None:
    assert _oracle_answer_from_rubric(rubric) == expected


def test_dockerfile_contains_published_biomystery_toolchain() -> None:
    dockerfile = (
        Path(__file__).parents[1] / "src/biomistery_bench/task-template/environment/Dockerfile"
    ).read_text(encoding="utf-8")

    assert "bioconductor_docker:RELEASE_3_18" in dockerfile
    for expected in (
        "bwa",
        "bowtie2",
        "ncbi-blast+",
        "samtools",
        "bcftools",
        "bedtools",
        "seqtk",
        "fastqc",
        "nextflow",
        "openjdk=21",
        "python=3.11",
        "uv python install 3.12",
        "scikit-image",
        "DESeq2",
        "clusterProfiler",
        "lionessR",
    ):
        assert expected in dockerfile


def test_adapter_rejects_archive_path_traversal(full_source: Path, tmp_path: Path) -> None:
    with zipfile.ZipFile(full_source / "data" / "hb001.zip", "a") as archive:
        archive.writestr("../../escape.txt", "unsafe")

    with pytest.raises(ValueError, match="Unsafe archive member"):
        BioMysteryBenchAdapter(
            source=DatasetSource(DATASET_RELEASE, source_dir=full_source),
            output_dir=tmp_path / "tasks",
            task_ids=["hb001"],
        ).run()
