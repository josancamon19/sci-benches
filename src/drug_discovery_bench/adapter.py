from __future__ import annotations

import json
import logging
import shutil
import tarfile
import tempfile
import urllib.request
from collections.abc import Iterable
from pathlib import Path
from typing import Any

UPSTREAM_REPO = "scaleapi/DrugDiscoveryBench"
DEFAULT_REVISION = "d58c703841abbad0ba1cc439488e15fbbeae3bd2"
HF_REPO_ID = "ScaleAI/DrugDiscoveryBench"
DEFAULT_HF_REVISION = "10cbbbb5da6f0fa46a6567c7cae0cbb3baa6c7cc"

_BIOMNI_IMAGE = "ghcr.io/scaleapi/drugdiscoverybench:1.0.0-lightweight"
_UPSTREAM_HEALTHCHECK = (
    'test "$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 http://scale.com)" = "403"'
)
_DAYTONA_HEALTHCHECK = (
    'if test "$(curl -s -o /dev/null -w "%{http_code}" --max-time 2 '
    'http://scale.com)" != "403"; then '
    "nohup python3 /usr/local/bin/egress_proxy.py "
    ">>/tmp/egress-proxy.log 2>&1 & fi; "
    "for _ in $(seq 1 50); do "
    'test "$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 '
    'http://scale.com)" = "403" && exit 0; '
    "sleep 0.2; done; exit 1"ma
)

LOGGER = logging.getLogger(__name__)


class RubricSource:
    def __init__(
        self,
        *,
        revision: str = DEFAULT_HF_REVISION,
        cache_dir: Path | None = None,
        source_file: Path | None = None,
        token: str | None = None,
    ) -> None:
        self.revision = revision
        self.cache_dir = cache_dir
        self.source_file = source_file
        self.token = token

    @property
    def source_revision(self) -> str:
        return "local" if self.source_file else self.revision

    def load(self) -> dict[str, dict[str, Any]]:
        path = self.source_file or self._download()
        rows = (
            json.loads(line)
            for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
        return {row["task_id"]: row for row in rows}

    def _download(self) -> Path:
        from huggingface_hub import hf_hub_download

        return Path(
            hf_hub_download(
                repo_id=HF_REPO_ID,
                repo_type="dataset",
                revision=self.revision,
                filename="tasks.jsonl",
                cache_dir=str(self.cache_dir) if self.cache_dir else None,
                token=self.token,
            )
        )


def download_source(
    *,
    revision: str = DEFAULT_REVISION,
    cache_dir: Path | None = None,
) -> Path:
    """Download the pinned upstream repository containing the Harbor tasks."""
    cache_root = cache_dir or (Path.home() / ".cache/genebench-pro/drug-discovery-bench/source")
    checkout = Path(cache_root).expanduser().resolve() / revision
    if checkout.is_dir():
        return checkout

    checkout.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=checkout.parent) as temporary:
        temporary_dir = Path(temporary)
        archive_path = temporary_dir / "source.tar.gz"
        request = urllib.request.Request(
            f"https://codeload.github.com/{UPSTREAM_REPO}/tar.gz/{revision}",
            headers={"User-Agent": "genebench-pro"},
        )
        with (
            urllib.request.urlopen(request, timeout=120) as response,  # noqa: S310
            archive_path.open("wb") as archive_file,
        ):
            shutil.copyfileobj(response, archive_file)
        with tarfile.open(archive_path, mode="r:gz") as archive:
            archive.extractall(temporary_dir, filter="data")

        extracted = next(
            path
            for path in temporary_dir.iterdir()
            if path.is_dir() and (path / "benchmark" / "tasks").is_dir()
        )
        shutil.move(extracted, checkout)
    return checkout


def discover_tasks(source_dir: Path) -> list[Path]:
    tasks_dir = Path(source_dir).expanduser().resolve() / "benchmark" / "tasks"
    return sorted(path for path in tasks_dir.iterdir() if path.is_dir())


def import_tasks(
    *,
    source_dir: Path,
    rubric_source: RubricSource,
    output_dir: Path,
    source_revision: str = DEFAULT_REVISION,
    limit: int | None = None,
    overwrite: bool = False,
    task_ids: Iterable[str] | None = None,
) -> list[Path]:
    """Copy the official Harbor tasks, populate rubrics, and patch Daytona startup."""
    tasks = discover_tasks(source_dir)
    if task_ids:
        selected = set(task_ids)
        tasks = [task for task in tasks if task.name in selected]
    if limit is not None:
        tasks = tasks[:limit]

    output_dir = Path(output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    rubrics = rubric_source.load()
    imported: list[Path] = []

    for source_task in tasks:
        destination = output_dir / source_task.name
        if destination.exists():
            if not overwrite:
                raise FileExistsError(f"Task already exists: {destination}")
            shutil.rmtree(destination)
        shutil.copytree(source_task, destination, copy_function=shutil.copy2)
        _populate_rubric(destination, rubrics[source_task.name])
        _patch_for_daytona(destination)
        imported.append(destination)

    (output_dir / "README.md").write_text(
        _dataset_readme(source_revision, rubric_source.source_revision),
        encoding="utf-8",
    )
    LOGGER.info("Imported %d task(s) into %s", len(imported), output_dir)
    return imported


def _populate_rubric(task_dir: Path, row: dict[str, Any]) -> None:
    rubric_path = task_dir / "tests" / "rubrics.json"
    rubric = json.loads(rubric_path.read_text(encoding="utf-8"))
    rubric["ground_truth"] = row.get("ground_truth", "")
    rubric["outcome_rubrics"] = [_rubric_item(item) for item in row.get("outcome_rubrics", [])]
    rubric["process_rubrics"] = [_rubric_item(item) for item in row.get("process_rubrics", [])]
    rubric_path.write_text(
        json.dumps(rubric, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _patch_for_daytona(task_dir: Path) -> None:
    dockerfile_path = task_dir / "environment" / "Dockerfile"
    dockerfile_path.write_text(
        dockerfile_path.read_text(encoding="utf-8").rstrip()
        + "\n\n"
        + "# Daytona starts the image without its entrypoint. Harbor needs root to prepare /logs;\n"
        + "# task.toml switches the solver back to the upstream biomni user.\n"
        + "USER root\n",
        encoding="utf-8",
    )

    config_path = task_dir / "task.toml"
    config = config_path.read_text(encoding="utf-8")
    config = config.replace(f'docker_image = "{_BIOMNI_IMAGE}"\n', "", 1)
    config = config.replace(
        f"command = '{_UPSTREAM_HEALTHCHECK}'",
        f"command = '{_DAYTONA_HEALTHCHECK}'",
        1,
    )
    config = config.replace("[agent]\n", '[agent]\nuser = "biomni"\n', 1)
    config_path.write_text(config, encoding="utf-8")


def _rubric_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": item.get("title", ""),
        "weight": item.get("weight", ""),
        "justification": item.get("justification", ""),
        "category": item.get("category", ""),
    }


def _dataset_readme(source_revision: str, rubric_revision: str) -> str:
    return (
        "# DrugDiscoveryBench\n\n"
        "Imported from the official Harbor release, with gated rubrics populated and "
        "the minimal Daytona startup patch applied.\n\n"
        f"- Task source: https://github.com/{UPSTREAM_REPO}/tree/{source_revision}\n"
        f"- Rubric source: https://huggingface.co/datasets/{HF_REPO_ID}/tree/"
        f"{rubric_revision}\n"
    )
