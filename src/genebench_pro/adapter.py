from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from jinja2 import Environment, StrictUndefined
from pypdf import PdfReader

HF_REPO_ID = "ajh-oai/genebench-pro-public-package"
DEFAULT_REVISION = "9bd2c54a6c0beef041e3504aa7eb65fc77783e18"
EXPECTED_PROBLEM_COUNT = 10
TEMPLATE_DIR = Path(__file__).parent / "task-template"

_TASK_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_]*$")
_SNAPSHOT_PATTERNS = (
    "manifest.json",
    "reference_grader.py",
    "problems/*/eval_config.json",
    "problems/*/data_files/*",
    "problems/*/report_public.pdf",
)
_UPSTREAM_OUTPUT_INSTRUCTION = "Return the JSON object in your final answer:"
_HARBOR_OUTPUT_INSTRUCTION = "Save the JSON object to `/app/result.json`:"
_REDUNDANT_OUTPUT_INSTRUCTIONS = (
    "Do not wrap the JSON in markdown.",
    "Do not add prose before or after the JSON.",
    "Do not omit any keys shown in the example.",
)

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Problem:
    task_id: str
    title: str
    domain: str
    eval_uuid: str
    config: dict[str, Any]
    source_dir: Path
    report_path: Path
    manifest_entry: dict[str, Any]

    @property
    def package_name(self) -> str:
        return f"ajh-oai/genebench-pro-{self.task_id.replace('_', '-')}"


def download_source(*, revision: str = DEFAULT_REVISION, cache_dir: Path | None = None) -> Path:
    """Download only the files needed to generate tasks from Hugging Face."""
    from huggingface_hub import snapshot_download

    snapshot_path = snapshot_download(
        repo_id=HF_REPO_ID,
        repo_type="dataset",
        revision=revision,
        allow_patterns=list(_SNAPSHOT_PATTERNS),
        cache_dir=str(cache_dir) if cache_dir is not None else None,
    )
    return Path(snapshot_path)


def discover_problems(source_dir: Path) -> list[Problem]:
    source_dir = source_dir.expanduser().resolve()
    manifest_path = source_dir / "manifest.json"
    grader_path = source_dir / "reference_grader.py"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Missing upstream manifest: {manifest_path}")
    if not grader_path.is_file():
        raise FileNotFoundError(f"Missing upstream reference grader: {grader_path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = manifest.get("problems")
    if not isinstance(entries, list):
        raise ValueError("Upstream manifest does not contain a problems list")
    if len(entries) != EXPECTED_PROBLEM_COUNT:
        raise ValueError(
            f"Expected {EXPECTED_PROBLEM_COUNT} upstream problems, found {len(entries)}"
        )

    problems: list[Problem] = []
    seen_ids: set[str] = set()
    for entry in sorted(entries, key=lambda item: item["release_order"]):
        task_id = entry.get("eval_id")
        if not isinstance(task_id, str) or not _TASK_ID_PATTERN.fullmatch(task_id):
            raise ValueError(f"Invalid upstream eval ID: {task_id!r}")
        if task_id in seen_ids:
            raise ValueError(f"Duplicate upstream eval ID: {task_id}")
        seen_ids.add(task_id)

        problem_dir = _safe_source_path(source_dir, entry["problem_dir"])
        config_path = _safe_source_path(source_dir, entry["eval_config"])
        config = json.loads(config_path.read_text(encoding="utf-8"))
        if config.get("id") != task_id:
            raise ValueError(
                f"Eval ID mismatch for {config_path}: {config.get('id')!r} != {task_id!r}"
            )
        if config.get("eval_uuid") != entry.get("eval_uuid"):
            raise ValueError(f"Eval UUID mismatch for {task_id}")
        if not isinstance(config.get("task"), str) or not config["task"].strip():
            raise ValueError(f"Problem {task_id} has no task text")
        if not isinstance(config.get("ground_truth"), dict):
            raise ValueError(f"Problem {task_id} has no ground truth object")
        if not isinstance(config.get("grader"), dict):
            raise ValueError(f"Problem {task_id} has no grader contract")

        expected_hashes = {
            file_entry["path"]: file_entry["sha256"]
            for file_entry in entry.get("files", [])
            if isinstance(file_entry, dict)
            and isinstance(file_entry.get("path"), str)
            and isinstance(file_entry.get("sha256"), str)
        }
        _verify_file(config_path, expected_hashes.get(entry["eval_config"]))

        report_path = problem_dir / "report_public.pdf"
        report_name = report_path.relative_to(source_dir).as_posix()
        _verify_file(report_path, expected_hashes.get(report_name))

        data_files = config.get("data_files")
        if not isinstance(data_files, list) or not data_files:
            raise ValueError(f"Problem {task_id} has no staged data files")
        for relative_name in data_files:
            data_path = _safe_problem_path(problem_dir, relative_name)
            manifest_name = data_path.relative_to(source_dir).as_posix()
            _verify_file(data_path, expected_hashes.get(manifest_name))

        problems.append(
            Problem(
                task_id=task_id,
                title=str(entry.get("title", task_id)),
                domain=str(entry.get("domain", "Genomics")),
                eval_uuid=str(entry["eval_uuid"]),
                config=config,
                source_dir=problem_dir,
                report_path=report_path,
                manifest_entry=entry,
            )
        )
    return problems


class GeneBenchProAdapter:
    def __init__(
        self,
        *,
        source_dir: Path,
        output_dir: Path,
        source_revision: str = DEFAULT_REVISION,
        limit: int | None = None,
        overwrite: bool = False,
        task_ids: Iterable[str] | None = None,
        template_dir: Path = TEMPLATE_DIR,
    ) -> None:
        if limit is not None and limit < 1:
            raise ValueError("limit must be at least 1")
        self.source_dir = source_dir.expanduser().resolve()
        self.output_dir = output_dir.expanduser().resolve()
        self.source_revision = source_revision
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
        problems = discover_problems(self.source_dir)
        if self.task_ids is not None:
            requested = set(self.task_ids)
            available = {problem.task_id for problem in problems}
            unknown = sorted(requested - available)
            if unknown:
                raise ValueError(f"Unknown task ID(s): {', '.join(unknown)}")
            problems = [problem for problem in problems if problem.task_id in requested]
        if self.limit is not None:
            problems = problems[: self.limit]

        self.output_dir.mkdir(parents=True, exist_ok=True)
        generated = [self.generate_task(problem) for problem in problems]
        LOGGER.info("Generated %d task(s) in %s", len(generated), self.output_dir)
        return generated

    def generate_task(self, problem: Problem) -> Path:
        task_dir = self.output_dir / problem.task_id
        if task_dir.exists():
            if not self.overwrite:
                raise FileExistsError(
                    f"Task already exists: {task_dir}. Pass --overwrite to replace it."
                )
            shutil.rmtree(task_dir)

        environment_dir = task_dir / "environment"
        solution_dir = task_dir / "solution"
        tests_dir = task_dir / "tests"
        for directory in (environment_dir, solution_dir, tests_dir):
            directory.mkdir(parents=True, exist_ok=True)

        context = {
            "domain": problem.domain,
            "eval_uuid": problem.eval_uuid,
            "package_name": problem.package_name,
            "source_revision": self.source_revision,
            "task": _adapt_task_prompt(problem.config["task"]),
            "task_id": problem.task_id,
            "title": problem.title,
        }
        self._render_template("task.toml", task_dir / "task.toml", context)
        self._render_template("instruction.md", task_dir / "instruction.md", context)
        self._copy_template("environment/Dockerfile", environment_dir / "Dockerfile")
        self._copy_template("tests/test.sh", tests_dir / "test.sh", executable=True)
        self._copy_template("tests/evaluate.py", tests_dir / "evaluate.py")
        self._copy_template("solution/solve.sh", solution_dir / "solve.sh", executable=True)

        data_dir = environment_dir / "data_files"
        data_dir.mkdir()
        for relative_name in problem.config["data_files"]:
            source_path = _safe_problem_path(problem.source_dir, relative_name)
            relative_path = PurePosixPath(relative_name)
            destination = data_dir / relative_path.relative_to("data_files")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, destination)

        config_text = json.dumps(problem.config, indent=2, sort_keys=True) + "\n"
        (tests_dir / "eval_config.json").write_text(config_text, encoding="utf-8")
        shutil.copy2(self.source_dir / "reference_grader.py", tests_dir / "reference_grader.py")
        shutil.copy2(problem.report_path, tests_dir / "report_public.pdf")
        (tests_dir / "report_public.txt").write_text(
            _extract_pdf_text(problem.report_path),
            encoding="utf-8",
        )

        oracle_submission = {
            "answer": problem.config["ground_truth"],
            "reasoning": "Public GeneBench-Pro reference answer used by the Harbor oracle.",
        }
        (solution_dir / "oracle_answer.json").write_text(
            json.dumps(oracle_submission, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return task_dir

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


def _safe_source_path(source_dir: Path, relative_name: str) -> Path:
    relative_path = PurePosixPath(relative_name)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise ValueError(f"Unsafe upstream path: {relative_name!r}")
    # Hugging Face snapshots symlink files into a sibling blob cache. Keep the
    # checked lexical path so valid cache symlinks do not look like traversal.
    return source_dir.joinpath(*relative_path.parts)


def _adapt_task_prompt(task: str) -> str:
    adapted = task.rstrip()
    for instruction in _REDUNDANT_OUTPUT_INSTRUCTIONS:
        count = adapted.count(instruction)
        if count != 1:
            raise ValueError(
                f"Expected exactly one upstream instruction {instruction!r}, found {count}"
            )
        adapted = adapted.replace(f"{instruction}\n", "")

    count = adapted.count(_UPSTREAM_OUTPUT_INSTRUCTION)
    if count != 1:
        raise ValueError(
            "Expected exactly one upstream output instruction "
            f"{_UPSTREAM_OUTPUT_INSTRUCTION!r}, found {count}"
        )
    return adapted.replace(_UPSTREAM_OUTPUT_INSTRUCTION, _HARBOR_OUTPUT_INSTRUCTION)


def _safe_problem_path(problem_dir: Path, relative_name: str) -> Path:
    relative_path = PurePosixPath(relative_name)
    if (
        relative_path.is_absolute()
        or ".." in relative_path.parts
        or not relative_path.parts
        or relative_path.parts[0] != "data_files"
    ):
        raise ValueError(f"Unsafe staged data path: {relative_name!r}")
    return problem_dir.joinpath(*relative_path.parts)


def _verify_file(path: Path, expected_sha256: str | None) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"Missing upstream file: {path}")
    if expected_sha256 is None:
        raise ValueError(f"No upstream checksum for required file: {path}")
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected_sha256:
        raise ValueError(
            f"Checksum mismatch for {path}: expected {expected_sha256}, found {actual}"
        )


def _extract_pdf_text(path: Path) -> str:
    pages = [page.extract_text() or "" for page in PdfReader(path).pages]
    text = "\n\n".join(page.strip() for page in pages if page.strip())
    return f"{text}\n" if text else "No extractable text in the public case-study report.\n"
