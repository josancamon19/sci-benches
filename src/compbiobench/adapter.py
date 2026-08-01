from __future__ import annotations

import csv
import json
import logging
import re
import shutil
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from jinja2 import Environment, StrictUndefined

HF_REPO_ID = "Genentech/compbiobench-data-v1"
DEFAULT_REVISION = "86ee0a22e036fef2a98f382f8fd528d5d390dde3"
RUNNER_REPO = "Genentech/compbiobench-runner"
RUNNER_REVISION = "dc350ed37ccd7d7ce96347d139f06dc4bf283f26"
EXPECTED_TASK_COUNT = 100
METADATA_FILE = "compbiobench.v1.tsv"
DATASET_README = "Official CompBioBench answers are not publicly shared.\n"
TEMPLATE_DIR = Path(__file__).parent / "task-template"

_TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_REQUIRED_COLUMNS = (
    "question_id",
    "curator_name",
    "domain",
    "question_style",
    "skills_tested",
    "question",
    "internet_required",
    "gpu_preferred",
    "file_paths",
)

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Task:
    task_id: str
    curator_name: str
    domain: str
    question_style: str
    skills_tested: str
    question: str
    internet_required: bool
    gpu_preferred: bool
    file_names: tuple[str, ...]

    @property
    def package_name(self) -> str:
        safe_id = re.sub(r"[^a-z0-9]+", "-", self.task_id.lower()).strip("-")
        return f"Genentech/compbiobench-{safe_id}"


class DatasetSource:
    """Resolve metadata and selected task files without downloading the full dataset."""

    def __init__(
        self,
        *,
        revision: str = DEFAULT_REVISION,
        cache_dir: Path | None = None,
        source_dir: Path | None = None,
        token: str | None = None,
    ) -> None:
        self.revision = revision
        self.cache_dir = cache_dir.expanduser().resolve() if cache_dir is not None else None
        self.source_dir = source_dir.expanduser().resolve() if source_dir is not None else None
        self.token = token

    @property
    def source_revision(self) -> str:
        return "local" if self.source_dir is not None else self.revision

    def metadata_path(self) -> Path:
        if self.source_dir is not None:
            path = self.source_dir / METADATA_FILE
        else:
            path = self._download(METADATA_FILE)
        if not path.is_file():
            raise FileNotFoundError(f"Missing CompBioBench metadata: {path}")
        return path

    def data_path(self, file_name: str) -> Path:
        _validate_file_name(file_name)
        if self.source_dir is not None:
            path = self.source_dir / "data" / file_name
        else:
            path = self._download(f"data/{file_name}")
        if not path.is_file():
            raise FileNotFoundError(f"Missing CompBioBench input file: {path}")
        return path

    def _download(self, file_name: str) -> Path:
        from huggingface_hub import hf_hub_download

        return Path(
            hf_hub_download(
                repo_id=HF_REPO_ID,
                repo_type="dataset",
                revision=self.revision,
                filename=file_name,
                cache_dir=str(self.cache_dir) if self.cache_dir is not None else None,
                token=self.token,
            )
        )


def discover_tasks(source: DatasetSource) -> list[Task]:
    metadata_path = source.metadata_path()
    with metadata_path.open(encoding="utf-8", newline="") as metadata_file:
        reader = csv.DictReader(metadata_file, delimiter="\t")
        missing_columns = [
            column for column in _REQUIRED_COLUMNS if column not in (reader.fieldnames or [])
        ]
        if missing_columns:
            raise ValueError(
                f"CompBioBench metadata is missing columns: {', '.join(missing_columns)}"
            )
        rows = list(reader)

    if len(rows) != EXPECTED_TASK_COUNT:
        raise ValueError(f"Expected {EXPECTED_TASK_COUNT} CompBioBench tasks, found {len(rows)}")

    tasks: list[Task] = []
    seen_ids: set[str] = set()
    for row in rows:
        task_id = row["question_id"].strip()
        if not _TASK_ID_PATTERN.fullmatch(task_id):
            raise ValueError(f"Invalid CompBioBench question ID: {task_id!r}")
        if task_id in seen_ids:
            raise ValueError(f"Duplicate CompBioBench question ID: {task_id}")
        seen_ids.add(task_id)

        question = row["question"].strip()
        if not question:
            raise ValueError(f"CompBioBench task {task_id} has an empty question")

        file_names = tuple(
            file_name.strip() for file_name in row["file_paths"].split(",") if file_name.strip()
        )
        if len(file_names) != len(set(file_names)):
            raise ValueError(f"CompBioBench task {task_id} contains duplicate input files")
        for file_name in file_names:
            _validate_file_name(file_name)

        tasks.append(
            Task(
                task_id=task_id,
                curator_name=row["curator_name"].strip(),
                domain=row["domain"].strip(),
                question_style=row["question_style"].strip(),
                skills_tested=row["skills_tested"].strip(),
                question=question,
                internet_required=_parse_bool(row["internet_required"], task_id=task_id),
                gpu_preferred=_parse_bool(row["gpu_preferred"], task_id=task_id),
                file_names=file_names,
            )
        )
    return tasks


class CompBioBenchAdapter:
    def __init__(
        self,
        *,
        source: DatasetSource,
        output_dir: Path,
        limit: int | None = None,
        overwrite: bool = False,
        task_ids: Iterable[str] | None = None,
        template_dir: Path = TEMPLATE_DIR,
    ) -> None:
        if limit is not None and limit < 1:
            raise ValueError("limit must be at least 1")
        self.source = source
        self.output_dir = output_dir.expanduser().resolve()
        self.limit = limit
        self.overwrite = overwrite
        self.task_ids = list(task_ids) if task_ids is not None else None
        self.template_dir = template_dir.expanduser().resolve()
        self._jinja = Environment(
            undefined=StrictUndefined,
            autoescape=False,
            keep_trailing_newline=True,
        )

    def run(self) -> list[Path]:
        tasks = discover_tasks(self.source)
        if self.task_ids is not None:
            requested = set(self.task_ids)
            available = {task.task_id for task in tasks}
            unknown = sorted(requested - available)
            if unknown:
                raise ValueError(f"Unknown task ID(s): {', '.join(unknown)}")
            tasks = [task for task in tasks if task.task_id in requested]
        if self.limit is not None:
            tasks = tasks[: self.limit]
        if not tasks:
            raise ValueError("No CompBioBench tasks selected")

        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / "README.md").write_text(DATASET_README, encoding="utf-8")
        generated = [self.generate_task(task) for task in tasks]
        LOGGER.info("Generated %d task(s) in %s", len(generated), self.output_dir)
        return generated

    def generate_task(self, task: Task) -> Path:
        task_dir = self.output_dir / task.task_id
        if task_dir.exists() and not self.overwrite:
            raise FileExistsError(
                f"Task already exists: {task_dir}. Pass --overwrite to replace it."
            )

        temporary_dir = self.output_dir / f".{task.task_id}.tmp-{uuid.uuid4().hex}"
        environment_dir = temporary_dir / "environment"
        task_files_dir = environment_dir / "task_files"
        solution_dir = temporary_dir / "solution"
        tests_dir = temporary_dir / "tests"
        try:
            for directory in (task_files_dir, solution_dir, tests_dir):
                directory.mkdir(parents=True, exist_ok=True)

            context: dict[str, Any] = {
                "curator_name": task.curator_name,
                "domain": task.domain,
                "file_names": list(task.file_names),
                "gpu_preferred": task.gpu_preferred,
                "internet_required": task.internet_required,
                "package_name": task.package_name,
                "question": task.question,
                "question_style": task.question_style,
                "runner_repo": RUNNER_REPO,
                "runner_revision": RUNNER_REVISION,
                "skills_tested": task.skills_tested,
                "source_repo": HF_REPO_ID,
                "source_revision": self.source.source_revision,
                "task_id": task.task_id,
            }
            self._render_template("task.toml", temporary_dir / "task.toml", context)
            self._render_template("instruction.md", temporary_dir / "instruction.md", context)
            self._copy_template("environment/Dockerfile", environment_dir / "Dockerfile")
            self._copy_template("tests/evaluate.py", tests_dir / "evaluate.py")
            self._copy_template("tests/test.sh", tests_dir / "test.sh", executable=True)
            self._copy_template("solution/solve.sh", solution_dir / "solve.sh", executable=True)

            for file_name in task.file_names:
                shutil.copy2(self.source.data_path(file_name), task_files_dir / file_name)

            public_metadata = {
                "curator_name": task.curator_name,
                "domain": task.domain,
                "file_names": list(task.file_names),
                "gpu_preferred": task.gpu_preferred,
                "internet_required": task.internet_required,
                "question_id": task.task_id,
                "question_style": task.question_style,
                "skills_tested": task.skills_tested,
                "source_repo": HF_REPO_ID,
                "source_revision": self.source.source_revision,
            }
            (tests_dir / "task_metadata.json").write_text(
                json.dumps(public_metadata, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            if task_dir.exists():
                shutil.rmtree(task_dir)
            temporary_dir.replace(task_dir)
            return task_dir
        except BaseException:
            shutil.rmtree(temporary_dir, ignore_errors=True)
            raise

    def _render_template(
        self, relative_name: str, destination: Path, context: dict[str, Any]
    ) -> None:
        source = self.template_dir / relative_name
        template = self._jinja.from_string(source.read_text(encoding="utf-8"))
        destination.write_text(template.render(**context), encoding="utf-8")

    def _copy_template(
        self,
        relative_name: str,
        destination: Path,
        *,
        executable: bool = False,
    ) -> None:
        shutil.copy2(self.template_dir / relative_name, destination)
        if executable:
            destination.chmod(destination.stat().st_mode | 0o111)


def _validate_file_name(file_name: str) -> None:
    path = PurePosixPath(file_name)
    if path.is_absolute() or len(path.parts) != 1 or path.name in {"", ".", ".."}:
        raise ValueError(f"Unsafe CompBioBench input file name: {file_name!r}")


def _parse_bool(value: str, *, task_id: str) -> bool:
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ValueError(f"Invalid boolean metadata for CompBioBench task {task_id}: {value!r}")
