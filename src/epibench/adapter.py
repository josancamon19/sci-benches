from __future__ import annotations

import errno
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
import uuid
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from jinja2 import Environment, StrictUndefined

UPSTREAM_REPO = "latchbio/epibench"
DEFAULT_REVISION = "6bc01f15125f5a01ab78ce1fd2fc2c51c4ca04f3"
SUPPORTING_RESULTS_REVISION = "309357153eecb72699b054c5a7f16f7dfef28e7f"
LATCH_CLI_VERSION = "2.76.10"
MAX_LATCH_DOWNLOAD_WORKERS = 16
EXPECTED_EVAL_COUNT = 7
DAYTONA_CPUS = 4
DAYTONA_MEMORY_MB = 8 * 1024
DAYTONA_STORAGE_MB = 10 * 1024
LARGE_INPUT_BYTES = 10 * 1024**3
DEFAULT_BUILD_TIMEOUT_SEC = 3600
LARGE_BUILD_TIMEOUT_SEC = 7200
TEMPLATE_DIR = Path(__file__).parent / "task-template"
FALLBACK_REFERENCES_PATH = Path(__file__).parent / "fallback_references.json"
DATASET_README_PATH = Path(__file__).parent / "README.md"

_TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_NODE_PATTERN = re.compile(r"^latch://[0-9]+\.node$")
_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_UPSTREAM_OUTPUT_INSTRUCTION = "Return EXACTLY:"
_HARBOR_OUTPUT_INSTRUCTION = "Save the following JSON object to `/app/result.json`:"
_OPEN_TAG = "<EVAL_ANSWER>"
_CLOSE_TAG = "</EVAL_ANSWER>"

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Evaluation:
    task_id: str
    canary: str
    task: str
    data_nodes: tuple[str, ...]
    metadata: dict[str, Any]
    eval_path: Path

    @property
    def package_name(self) -> str:
        safe_id = re.sub(r"[^a-z0-9]+", "-", self.task_id.lower()).strip("-")
        return f"latchbio/epibench-{safe_id}"


class DataSource(Protocol):
    def stage(self, nodes: Sequence[str], destination: Path) -> tuple[Path, ...]: ...


class LatchDataSource:
    """Stage a task's public Latch nodes with an authenticated, reusable cache."""

    def __init__(
        self,
        *,
        token: str | None,
        cache_dir: Path | None = None,
        latch_cli: str | None = None,
    ) -> None:
        self.token = (token or "").strip()
        self.cache_dir = (
            cache_dir.expanduser().resolve()
            if cache_dir is not None
            else Path.home() / ".cache" / "genebench-pro" / "epibench" / "latch-data"
        )
        self.latch_cli = latch_cli

    def stage(self, nodes: Sequence[str], destination: Path) -> tuple[Path, ...]:
        normalized_nodes = tuple(nodes)
        if not normalized_nodes:
            raise ValueError("cannot stage an EpiBench task with no Latch nodes")
        for node in normalized_nodes:
            if not _NODE_PATTERN.fullmatch(node):
                raise ValueError(f"invalid Latch data node: {node!r}")
        if not self.token:
            raise ValueError(
                "LATCH_BIO_API_KEY is required to stage EpiBench data. "
                "Set it in .env or the process environment."
            )

        fingerprint = hashlib.sha256("\n".join(normalized_nodes).encode()).hexdigest()
        cache_path = self.cache_dir / fingerprint
        manifest_path = cache_path / ".epibench-cache.json"
        active_cache_path = cache_path
        if not _valid_cache(manifest_path, normalized_nodes):
            active_cache_path = self._populate_cache(cache_path, manifest_path, normalized_nodes)

        destination.mkdir(parents=True, exist_ok=True)
        staged: list[Path] = []
        active_manifest_path = active_cache_path / manifest_path.name
        for source in sorted(active_cache_path.rglob("*")):
            if not source.is_file() or source == active_manifest_path:
                continue
            relative_path = source.relative_to(active_cache_path)
            target = destination / relative_path
            if target.exists():
                raise FileExistsError(f"duplicate staged EpiBench data path: {target}")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            staged.append(target)
        if not staged:
            raise RuntimeError(f"Latch cache contains no files: {active_cache_path}")
        return tuple(staged)

    def _populate_cache(
        self,
        cache_path: Path,
        manifest_path: Path,
        nodes: tuple[str, ...],
    ) -> Path:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        temporary_path = self.cache_dir / f".{cache_path.name}.partial"
        temporary_path.mkdir(exist_ok=True)
        temporary_manifest_path = temporary_path / manifest_path.name
        if _valid_cache(temporary_manifest_path, nodes):
            return temporary_path
        try:
            self._download_nodes(nodes, temporary_path)
            files = sorted(path for path in temporary_path.rglob("*") if path.is_file())
            if len(files) != len(nodes):
                raise RuntimeError(
                    "Latch staging returned an unexpected number of files: "
                    f"expected {len(nodes)}, found {len(files)}. "
                    "The public eval may contain duplicate file names or directory nodes."
                )
            manifest = {
                "nodes": list(nodes),
                "files": [
                    {
                        "path": path.relative_to(temporary_path).as_posix(),
                        "size_bytes": path.stat().st_size,
                    }
                    for path in files
                ],
            }
            (temporary_path / manifest_path.name).write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            if cache_path.exists():
                shutil.rmtree(cache_path)
            try:
                temporary_path.replace(cache_path)
            except OSError as error:
                if error.errno not in {
                    errno.EPERM,
                    errno.EXDEV,
                    errno.ENOSYS,
                    errno.EOPNOTSUPP,
                }:
                    raise
                LOGGER.warning("Atomic cache finalization is unavailable; using %s", temporary_path)
                return temporary_path
            return cache_path
        except BaseException:
            LOGGER.warning("Retained partial Latch download at %s", temporary_path)
            raise

    def _download_nodes(self, nodes: tuple[str, ...], destination: Path) -> None:
        command_prefix = self._latch_command_prefix()
        with tempfile.TemporaryDirectory(prefix="epibench-latch-auth-") as auth_root:
            latch_config = Path(auth_root) / ".latch"
            latch_config.mkdir()
            token_path = latch_config / "token"
            token_path.write_text(self.token, encoding="utf-8")
            token_path.chmod(0o600)

            environment = os.environ.copy()
            environment.pop("LATCH_BIO_API_KEY", None)
            environment["HOME"] = auth_root
            environment.setdefault("UV_CACHE_DIR", str(self.cache_dir.parent / "uv-cache"))

            if Path(command_prefix[0]).name == "uvx":
                warmup = subprocess.run(
                    [*command_prefix, "--help"],
                    check=False,
                    capture_output=True,
                    text=True,
                    env=environment,
                )
                if warmup.returncode != 0:
                    output = (warmup.stderr or warmup.stdout).strip()
                    raise RuntimeError(f"Could not prepare pinned Latch CLI: {output}")
                command_prefix = [command_prefix[0], "--offline", *command_prefix[1:]]

            worker_count = min(MAX_LATCH_DOWNLOAD_WORKERS, len(nodes))
            LOGGER.info(
                "Downloading %d Latch node(s) with %d parallel worker(s)",
                len(nodes),
                worker_count,
            )
            batches = [nodes[index::worker_count] for index in range(worker_count)]
            batch_root = destination / ".epibench-download-batches"
            batch_root.mkdir(exist_ok=True)
            for child in destination.iterdir():
                if child == batch_root:
                    continue
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()

            def download(
                item: tuple[int, tuple[str, ...]],
            ) -> subprocess.CompletedProcess[str]:
                batch_index, batch = item
                batch_dir = batch_root / f"{batch_index:03d}"
                marker_path = batch_dir / ".complete.json"
                if _valid_download_batch(marker_path, batch):
                    return subprocess.CompletedProcess([], 0, "", "")
                shutil.rmtree(batch_dir, ignore_errors=True)
                batch_dir.mkdir()
                command = [
                    *command_prefix,
                    "cp",
                    "--progress",
                    "none",
                    "--no-glob",
                    *batch,
                    str(batch_dir),
                ]
                result = subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    text=True,
                    env=environment,
                )
                if result.returncode == 0:
                    files = sorted(path for path in batch_dir.rglob("*") if path.is_file())
                    marker_path.write_text(
                        json.dumps(
                            {
                                "nodes": list(batch),
                                "files": [
                                    {
                                        "path": path.relative_to(batch_dir).as_posix(),
                                        "size_bytes": path.stat().st_size,
                                    }
                                    for path in files
                                ],
                            },
                            sort_keys=True,
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                return result

            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                results = list(executor.map(download, enumerate(batches)))

        failures = [result for result in results if result.returncode != 0]
        if failures:
            output = "\n".join(
                part.strip()
                for result in failures
                for part in (result.stdout, result.stderr)
                if part.strip()
            )
            exit_codes = ", ".join(str(result.returncode) for result in failures)
            raise RuntimeError(f"Latch data download batch failed (exit {exit_codes}): {output}")

        for batch_dir in sorted(batch_root.iterdir()):
            marker_path = batch_dir / ".complete.json"
            for source in sorted(path for path in batch_dir.rglob("*") if path.is_file()):
                if source == marker_path:
                    continue
                target = destination / source.relative_to(batch_dir)
                if target.exists():
                    raise FileExistsError(f"duplicate Latch download path: {target}")
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    os.link(source, target)
                except OSError as error:
                    if error.errno not in {errno.EPERM, errno.EXDEV, errno.EOPNOTSUPP}:
                        raise
                    shutil.copyfile(source, target)
        shutil.rmtree(batch_root, ignore_errors=True)

    def _latch_command_prefix(self) -> list[str]:
        if self.latch_cli is not None:
            if shutil.which(self.latch_cli) is None:
                raise FileNotFoundError(f"Latch CLI executable not found: {self.latch_cli!r}")
            return [self.latch_cli]
        if shutil.which("uvx") is not None:
            return ["uvx", "--from", f"latch=={LATCH_CLI_VERSION}", "latch"]
        if shutil.which("latch") is not None:
            return ["latch"]
        raise FileNotFoundError(
            "Neither uvx nor the Latch CLI is installed. Install uv or pass --latch-cli."
        )


def download_source(
    *,
    revision: str = DEFAULT_REVISION,
    cache_dir: Path | None = None,
) -> Path:
    """Download the seven public eval definitions from a pinned GitHub revision."""
    if not _REVISION_PATTERN.fullmatch(revision):
        raise ValueError("EpiBench revision must be a full 40-character lowercase commit SHA")
    cache_root = (
        cache_dir.expanduser().resolve()
        if cache_dir is not None
        else Path.home() / ".cache" / "genebench-pro" / "epibench" / "source"
    )
    source_path = cache_root / revision
    if len(list((source_path / "evals").glob("*/eval.json"))) == EXPECTED_EVAL_COUNT:
        return source_path

    cache_root.mkdir(parents=True, exist_ok=True)
    temporary_path = cache_root / f".{revision}.tmp-{uuid.uuid4().hex}"
    temporary_path.mkdir()
    archive_path = temporary_path / "source.tar.gz"
    request = urllib.request.Request(
        f"https://codeload.github.com/{UPSTREAM_REPO}/tar.gz/{revision}",
        headers={"User-Agent": "genebench-pro-epibench-adapter"},
    )
    try:
        with (
            urllib.request.urlopen(request, timeout=120) as response,  # noqa: S310
            archive_path.open("wb") as archive_file,
        ):
            shutil.copyfileobj(response, archive_file)
        with tarfile.open(archive_path, mode="r:gz") as archive:
            for member in archive.getmembers():
                if not member.isfile():
                    continue
                parts = PurePosixPath(member.name).parts
                if len(parts) < 2:
                    continue
                relative = PurePosixPath(*parts[1:])
                include = (
                    len(relative.parts) == 3
                    and relative.parts[0] == "evals"
                    and relative.parts[2] == "eval.json"
                ) or relative.as_posix() in {"README.md", "LICENSE"}
                if not include:
                    continue
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise RuntimeError(f"could not read archive member: {member.name}")
                destination = temporary_path.joinpath(*relative.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open("wb") as output_file:
                    shutil.copyfileobj(extracted, output_file)
        archive_path.unlink()
        discover_evaluations(temporary_path)
        if source_path.exists():
            shutil.rmtree(source_path)
        temporary_path.replace(source_path)
        return source_path
    except BaseException:
        shutil.rmtree(temporary_path, ignore_errors=True)
        raise


def discover_evaluations(source_dir: Path) -> list[Evaluation]:
    source_dir = source_dir.expanduser().resolve()
    eval_paths = sorted((source_dir / "evals").glob("*/eval.json"))
    if len(eval_paths) != EXPECTED_EVAL_COUNT:
        raise ValueError(
            f"Expected {EXPECTED_EVAL_COUNT} public EpiBench evals, found {len(eval_paths)}"
        )

    evaluations: list[Evaluation] = []
    seen_ids: set[str] = set()
    for eval_path in eval_paths:
        payload = json.loads(eval_path.read_text(encoding="utf-8"))
        task_id = payload.get("id")
        if not isinstance(task_id, str) or not _TASK_ID_PATTERN.fullmatch(task_id):
            raise ValueError(f"invalid EpiBench eval ID in {eval_path}: {task_id!r}")
        if task_id != eval_path.parent.name:
            raise ValueError(f"EpiBench eval directory and ID differ: {eval_path}")
        if task_id in seen_ids:
            raise ValueError(f"duplicate EpiBench eval ID: {task_id}")
        seen_ids.add(task_id)

        task = payload.get("task")
        canary = payload.get("canary")
        metadata = payload.get("metadata")
        raw_nodes = payload.get("data_node")
        if not isinstance(task, str) or not task.strip():
            raise ValueError(f"EpiBench eval {task_id} has no task")
        if not isinstance(canary, str) or not canary.strip():
            raise ValueError(f"EpiBench eval {task_id} has no canary")
        if not isinstance(metadata, dict):
            raise ValueError(f"EpiBench eval {task_id} has invalid metadata")
        if isinstance(raw_nodes, str):
            nodes = (raw_nodes,)
        elif isinstance(raw_nodes, list) and all(isinstance(node, str) for node in raw_nodes):
            nodes = tuple(raw_nodes)
        else:
            raise ValueError(f"EpiBench eval {task_id} has invalid data_node metadata")
        if not nodes or len(nodes) != len(set(nodes)):
            raise ValueError(f"EpiBench eval {task_id} has empty or duplicate data nodes")
        for node in nodes:
            if not _NODE_PATTERN.fullmatch(node):
                raise ValueError(f"EpiBench eval {task_id} has invalid node: {node!r}")

        evaluations.append(
            Evaluation(
                task_id=task_id,
                canary=canary,
                task=task,
                data_nodes=nodes,
                metadata=metadata,
                eval_path=eval_path,
            )
        )
    return evaluations


class EpiBenchAdapter:
    def __init__(
        self,
        *,
        source_dir: Path,
        data_source: DataSource,
        output_dir: Path,
        source_revision: str = DEFAULT_REVISION,
        limit: int | None = None,
        overwrite: bool = False,
        task_ids: Iterable[str] | None = None,
        template_dir: Path = TEMPLATE_DIR,
        fallback_references_path: Path = FALLBACK_REFERENCES_PATH,
    ) -> None:
        if limit is not None and limit < 1:
            raise ValueError("limit must be at least 1")
        self.source_dir = source_dir.expanduser().resolve()
        self.data_source = data_source
        self.output_dir = output_dir.expanduser().resolve()
        self.source_revision = source_revision
        self.limit = limit
        self.overwrite = overwrite
        self.task_ids = list(task_ids) if task_ids is not None else None
        self.template_dir = template_dir.expanduser().resolve()
        self.fallback_references_path = fallback_references_path.expanduser().resolve()
        self._jinja = Environment(
            undefined=StrictUndefined,
            autoescape=False,
            keep_trailing_newline=True,
        )
        self._references = _load_fallback_references(self.fallback_references_path)
        self._atomic_directory_replace_supported: bool | None = None

    def run(self) -> list[Path]:
        evaluations = discover_evaluations(self.source_dir)
        reference_ids = set(self._references["tasks"])
        available_ids = {evaluation.task_id for evaluation in evaluations}
        if reference_ids != available_ids:
            raise ValueError("fallback reference task IDs do not match the public EpiBench evals")
        if self.task_ids is not None:
            requested = set(self.task_ids)
            unknown = sorted(requested - available_ids)
            if unknown:
                raise ValueError(f"Unknown task ID(s): {', '.join(unknown)}")
            evaluations = [
                evaluation for evaluation in evaluations if evaluation.task_id in requested
            ]
        if self.limit is not None:
            evaluations = evaluations[: self.limit]
        if not evaluations:
            raise ValueError("No EpiBench tasks selected")

        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._atomic_directory_replace_supported = _supports_atomic_directory_replace(
            self.output_dir
        )
        if not self._atomic_directory_replace_supported:
            LOGGER.warning("Atomic directory replacement is unavailable; generating tasks in place")
        shutil.copyfile(DATASET_README_PATH, self.output_dir / "README.md")
        generated = []
        for evaluation in evaluations:
            LOGGER.info("Staging EpiBench task %s", evaluation.task_id)
            generated.append(self.generate_task(evaluation))
            LOGGER.info("Generated %s", generated[-1])
        LOGGER.info("Generated %d EpiBench task(s) in %s", len(generated), self.output_dir)
        return generated

    def generate_task(self, evaluation: Evaluation) -> Path:
        task_dir = self.output_dir / evaluation.task_id
        if task_dir.exists() and not self.overwrite:
            raise FileExistsError(
                f"Task already exists: {task_dir}. Pass --overwrite to replace it."
            )
        atomic_replace = self._atomic_directory_replace_supported
        if atomic_replace is None:
            atomic_replace = _supports_atomic_directory_replace(self.output_dir)
        if task_dir.exists() and not atomic_replace:
            shutil.rmtree(task_dir, ignore_errors=True)
        working_dir = (
            self.output_dir / f".{evaluation.task_id}.tmp-{uuid.uuid4().hex}"
            if atomic_replace
            else task_dir
        )
        environment_dir = working_dir / "environment"
        data_dir = environment_dir / "data"
        solution_dir = working_dir / "solution"
        tests_dir = working_dir / "tests"
        for directory in (data_dir, solution_dir, tests_dir):
            directory.mkdir(parents=True, exist_ok=True)

        try:
            staged_files = self.data_source.stage(evaluation.data_nodes, data_dir)
            input_size_bytes = sum(path.stat().st_size for path in staged_files)
            input_files = [path.relative_to(data_dir).as_posix() for path in staged_files]
            storage_mb = DAYTONA_STORAGE_MB
            build_timeout_sec = _build_timeout_sec_for_input(input_size_bytes)
            timeout_sec = max(3600, int(evaluation.metadata.get("timeout_s", 0)))
            memory_mb = DAYTONA_MEMORY_MB

            context: dict[str, Any] = {
                "cpus": DAYTONA_CPUS,
                "input_file_count": len(input_files),
                "input_size_bytes": input_size_bytes,
                "build_timeout_sec": build_timeout_sec,
                "kit": str(evaluation.metadata.get("kit", "unknown")),
                "memory_mb": memory_mb,
                "package_name": evaluation.package_name,
                "source_repo": UPSTREAM_REPO,
                "source_revision": self.source_revision,
                "storage_mb": storage_mb,
                "task": _adapt_task_prompt(evaluation.task),
                "task_family": str(evaluation.metadata.get("task", "unknown")),
                "task_id": evaluation.task_id,
                "time_horizon": str(evaluation.metadata.get("time_horizon", "unknown")),
                "timeout_sec": timeout_sec,
            }
            self._render_template("task.toml", working_dir / "task.toml", context)
            self._render_template("instruction.md", working_dir / "instruction.md", context)
            self._copy_template("environment/Dockerfile", environment_dir / "Dockerfile")
            self._copy_template("tests/evaluate.py", tests_dir / "evaluate.py")
            self._copy_template("tests/test.sh", tests_dir / "test.sh", executable=True)
            self._copy_template("solution/solve.sh", solution_dir / "solve.sh", executable=True)
            shutil.copyfile(Path(__file__).parent / "grading.py", tests_dir / "grading.py")
            shutil.copyfile(evaluation.eval_path, tests_dir / "upstream_eval.json")

            task_reference = self._references["tasks"][evaluation.task_id]
            grader_config = {
                "schema_version": self._references["schema_version"],
                "task_id": evaluation.task_id,
                "notice": self._references["notice"],
                "official_verifier_available": False,
                "upstream_revision": self._references["upstream_revision"],
                "supporting_results_revision": self._references["supporting_results_revision"],
                **task_reference,
            }
            (tests_dir / "fallback_grader.json").write_text(
                json.dumps(grader_config, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            source_metadata = {
                "canary": evaluation.canary,
                "data_nodes": list(evaluation.data_nodes),
                "input_files": input_files,
                "input_size_bytes": input_size_bytes,
                "metadata": evaluation.metadata,
                "official_verifier_available": False,
                "source_repo": UPSTREAM_REPO,
                "source_revision": self.source_revision,
                "task_id": evaluation.task_id,
            }
            (tests_dir / "source_metadata.json").write_text(
                json.dumps(source_metadata, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            oracle_answer = task_reference["accepted_variants"][0]["answer"]
            (solution_dir / "oracle_answer.json").write_text(
                json.dumps(oracle_answer, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            if working_dir != task_dir:
                if task_dir.exists():
                    shutil.rmtree(task_dir)
                working_dir.replace(task_dir)
            return task_dir
        except BaseException:
            shutil.rmtree(working_dir, ignore_errors=True)
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
        shutil.copyfile(self.template_dir / relative_name, destination)
        if executable:
            try:
                destination.chmod(destination.stat().st_mode | 0o111)
            except PermissionError:
                LOGGER.warning(
                    "Could not set executable mode on managed volume file %s", destination
                )


def _adapt_task_prompt(task: str) -> str:
    adapted = task.rstrip()
    for marker in (_UPSTREAM_OUTPUT_INSTRUCTION, _OPEN_TAG, _CLOSE_TAG):
        if adapted.count(marker) != 1:
            raise ValueError(f"expected exactly one EpiBench output marker {marker!r}")
    adapted = adapted.replace(_UPSTREAM_OUTPUT_INSTRUCTION, _HARBOR_OUTPUT_INSTRUCTION)
    adapted = adapted.replace(f"{_OPEN_TAG}\n", "")
    adapted = adapted.replace(f"\n{_CLOSE_TAG}", "")
    return adapted + "\n"


def _load_fallback_references(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("unsupported EpiBench fallback reference schema")
    if payload.get("upstream_revision") != DEFAULT_REVISION:
        raise ValueError("fallback references do not match the pinned EpiBench revision")
    if payload.get("supporting_results_revision") != SUPPORTING_RESULTS_REVISION:
        raise ValueError("fallback references do not match the pinned supporting results")
    if not isinstance(payload.get("tasks"), dict):
        raise ValueError("fallback references have no tasks")
    return payload


def _valid_cache(manifest_path: Path, nodes: tuple[str, ...]) -> bool:
    if not manifest_path.is_file():
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("nodes") != list(nodes) or not isinstance(manifest.get("files"), list):
            return False
        for file_entry in manifest["files"]:
            if not isinstance(file_entry, dict) or not isinstance(file_entry.get("path"), str):
                return False
            relative = PurePosixPath(file_entry["path"])
            if relative.is_absolute() or ".." in relative.parts:
                return False
            path = manifest_path.parent.joinpath(*relative.parts)
            if not path.is_file() or path.stat().st_size != file_entry.get("size_bytes"):
                return False
        return len(manifest["files"]) == len(nodes)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return False


def _valid_download_batch(marker_path: Path, nodes: tuple[str, ...]) -> bool:
    if not marker_path.is_file():
        return False
    try:
        manifest = json.loads(marker_path.read_text(encoding="utf-8"))
        if manifest.get("nodes") != list(nodes) or not isinstance(manifest.get("files"), list):
            return False
        for file_entry in manifest["files"]:
            if not isinstance(file_entry, dict) or not isinstance(file_entry.get("path"), str):
                return False
            relative = PurePosixPath(file_entry["path"])
            if relative.is_absolute() or ".." in relative.parts:
                return False
            path = marker_path.parent.joinpath(*relative.parts)
            if not path.is_file() or path.stat().st_size != file_entry.get("size_bytes"):
                return False
        return bool(manifest["files"])
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return False


def _supports_atomic_directory_replace(root: Path) -> bool:
    token = uuid.uuid4().hex
    source = root / f".epibench-rename-source-{token}"
    target = root / f".epibench-rename-target-{token}"
    source.mkdir()
    try:
        source.replace(target)
        return True
    except OSError as error:
        if error.errno in {
            errno.EPERM,
            errno.EXDEV,
            errno.ENOSYS,
            errno.EOPNOTSUPP,
        }:
            return False
        raise
    finally:
        shutil.rmtree(source, ignore_errors=True)
        shutil.rmtree(target, ignore_errors=True)


def _build_timeout_sec_for_input(input_size_bytes: int) -> int:
    if input_size_bytes > LARGE_INPUT_BYTES:
        return LARGE_BUILD_TIMEOUT_SEC
    return DEFAULT_BUILD_TIMEOUT_SEC
