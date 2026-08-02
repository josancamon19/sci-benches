from __future__ import annotations

import copy
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

UPSTREAM_REPO = "latchbio/txbench-pp"
DEFAULT_REVISION = "a4e0913d76c3dbc433ecf39903afbe7711a6a807"
LATCH_EVAL_TOOLS_REVISION = "5e58bee341c9602f6a7b866a4f4e4701f0339de1"
LATCH_EVAL_TOOLS_VERSION = "0.4.17"
LATCH_CLI_VERSION = "2.76.10"
MAX_LATCH_DOWNLOAD_WORKERS = 16
EXPECTED_EVAL_COUNT = 12
DAYTONA_CPUS = 4
DAYTONA_MEMORY_MB = 8 * 1024
DAYTONA_STORAGE_MB = 10 * 1024
DEFAULT_AGENT_TIMEOUT_SEC = 3600
LONG_AGENT_TIMEOUT_SEC = 7200
BUILD_TIMEOUT_SEC = 7200
TEMPLATE_DIR = Path(__file__).parent / "task-template"
DATASET_README_PATH = Path(__file__).parent / "README.md"

_TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_NODE_PATTERN = re.compile(r"^latch://[0-9]+\.node$")
_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_OUTPUT_INSTRUCTION_PATTERN = re.compile(r"(?m)^Return EXACTLY(?:[^\n]*):[ \t]*$")
_HARBOR_OUTPUT_INSTRUCTION = "Save the following JSON object to `/app/result.json`:"
_OPEN_TAG = "<EVAL_ANSWER>"
_CLOSE_TAG = "</EVAL_ANSWER>"
_SUPPORTED_GRADERS = {
    "all_of",
    "label_set_jaccard",
    "marker_gene_precision_recall",
    "multiple_choice",
    "numeric_tolerance",
}

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Evaluation:
    task_id: str
    canary: str
    task: str
    data_nodes: tuple[str, ...]
    metadata: dict[str, Any]
    grader: dict[str, Any]
    eval_path: Path

    @property
    def package_name(self) -> str:
        safe_id = re.sub(r"[^a-z0-9]+", "-", self.task_id.lower()).strip("-")
        return f"latchbio/txbench-pp-{safe_id}"


class DataSource(Protocol):
    def stage(self, nodes: Sequence[str], destination: Path) -> tuple[Path, ...]: ...


class LatchDataSource:
    """Stage a task's Latch nodes through an authenticated, reusable cache."""

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
            else Path.home() / ".cache" / "genebench-pro" / "txbench-pp" / "latch-data"
        )
        self.latch_cli = latch_cli

    def stage(self, nodes: Sequence[str], destination: Path) -> tuple[Path, ...]:
        normalized_nodes = tuple(nodes)
        if not normalized_nodes:
            raise ValueError("cannot stage a TxBench-PP task with no Latch nodes")
        for node in normalized_nodes:
            if not _NODE_PATTERN.fullmatch(node):
                raise ValueError(f"invalid Latch data node: {node!r}")
        if not self.token:
            raise ValueError(
                "LATCH_BIO_API_KEY is required to stage TxBench-PP data. "
                "Set it in .env or the process environment."
            )

        fingerprint = hashlib.sha256("\n".join(normalized_nodes).encode()).hexdigest()
        cache_path = self.cache_dir / fingerprint
        manifest_path = cache_path / ".txbench-pp-cache.json"
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
                raise FileExistsError(f"duplicate staged TxBench-PP data path: {target}")
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
            if not files:
                raise RuntimeError("Latch staging returned no files")
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
            temporary_manifest_path.write_text(
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
        with tempfile.TemporaryDirectory(prefix="txbench-pp-latch-auth-") as auth_root:
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
            batch_root = destination / ".txbench-pp-download-batches"
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
    """Download the public eval definitions from a pinned GitHub revision."""
    if not _REVISION_PATTERN.fullmatch(revision):
        raise ValueError("TxBench-PP revision must be a full 40-character lowercase commit SHA")
    cache_root = (
        cache_dir.expanduser().resolve()
        if cache_dir is not None
        else Path.home() / ".cache" / "genebench-pro" / "txbench-pp" / "source"
    )
    source_path = cache_root / revision
    try:
        discover_evaluations(source_path)
        return source_path
    except (FileNotFoundError, ValueError, json.JSONDecodeError):
        pass

    cache_root.mkdir(parents=True, exist_ok=True)
    temporary_path = cache_root / f".{revision}.tmp-{uuid.uuid4().hex}"
    temporary_path.mkdir()
    archive_path = temporary_path / "source.tar.gz"
    request = urllib.request.Request(
        f"https://codeload.github.com/{UPSTREAM_REPO}/tar.gz/{revision}",
        headers={"User-Agent": "genebench-pro-txbench-pp-adapter"},
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
                ) or relative.as_posix() in {"README.md", "evals/manifest.json"}
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
    manifest_path = source_dir / "evals" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = manifest.get("evals")
    if not isinstance(entries, list) or len(entries) != EXPECTED_EVAL_COUNT:
        found = len(entries) if isinstance(entries, list) else 0
        raise ValueError(f"Expected {EXPECTED_EVAL_COUNT} public TxBench-PP evals, found {found}")

    all_eval_paths = set((source_dir / "evals").glob("*/eval.json"))
    evaluations: list[Evaluation] = []
    seen_ids: set[str] = set()
    manifest_paths: set[Path] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("TxBench-PP manifest contains a non-object eval entry")
        task_id = entry.get("id")
        relative_path = entry.get("path")
        if not isinstance(task_id, str) or not _TASK_ID_PATTERN.fullmatch(task_id):
            raise ValueError(f"invalid TxBench-PP eval ID: {task_id!r}")
        if task_id in seen_ids:
            raise ValueError(f"duplicate TxBench-PP eval ID: {task_id}")
        seen_ids.add(task_id)
        expected_relative = f"evals/{task_id}/eval.json"
        if relative_path != expected_relative:
            raise ValueError(f"invalid manifest path for TxBench-PP eval {task_id}")
        eval_path = source_dir / expected_relative
        manifest_paths.add(eval_path)

        payload = json.loads(eval_path.read_text(encoding="utf-8"))
        if payload.get("id") != task_id:
            raise ValueError(f"TxBench-PP eval directory and ID differ: {eval_path}")
        task = payload.get("task")
        canary = payload.get("canary")
        metadata = payload.get("metadata")
        grader = payload.get("grader")
        raw_nodes = payload.get("data_node")
        if not isinstance(task, str) or not task.strip():
            raise ValueError(f"TxBench-PP eval {task_id} has no task")
        if not isinstance(canary, str) or not canary.strip():
            raise ValueError(f"TxBench-PP eval {task_id} has no canary")
        if not isinstance(metadata, dict):
            raise ValueError(f"TxBench-PP eval {task_id} has invalid metadata")
        if not isinstance(grader, dict):
            raise ValueError(f"TxBench-PP eval {task_id} has invalid grader")
        _validate_grader_spec(grader, task_id)
        if entry.get("grader") != grader.get("type"):
            raise ValueError(f"TxBench-PP manifest grader differs for {task_id}")

        if isinstance(raw_nodes, str):
            nodes = (raw_nodes,)
        elif isinstance(raw_nodes, list) and all(isinstance(node, str) for node in raw_nodes):
            nodes = tuple(raw_nodes)
        else:
            raise ValueError(f"TxBench-PP eval {task_id} has invalid data_node metadata")
        if not nodes or len(nodes) != len(set(nodes)):
            raise ValueError(f"TxBench-PP eval {task_id} has empty or duplicate data nodes")
        for node in nodes:
            if not _NODE_PATTERN.fullmatch(node):
                raise ValueError(f"TxBench-PP eval {task_id} has invalid node: {node!r}")

        _adapt_task_prompt(task)
        evaluations.append(
            Evaluation(
                task_id=task_id,
                canary=canary,
                task=task,
                data_nodes=nodes,
                metadata=metadata,
                grader=grader,
                eval_path=eval_path,
            )
        )

    if manifest_paths != all_eval_paths:
        raise ValueError("TxBench-PP manifest does not match the released eval files")
    return evaluations


class TxBenchPPAdapter:
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
        self._jinja = Environment(
            undefined=StrictUndefined,
            autoescape=False,
            keep_trailing_newline=True,
        )
        self._atomic_directory_replace_supported: bool | None = None

    def run(self) -> list[Path]:
        evaluations = discover_evaluations(self.source_dir)
        available_ids = {evaluation.task_id for evaluation in evaluations}
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
            raise ValueError("No TxBench-PP tasks selected")

        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._atomic_directory_replace_supported = _supports_atomic_directory_replace(
            self.output_dir
        )
        if not self._atomic_directory_replace_supported:
            LOGGER.warning("Atomic directory replacement is unavailable; generating tasks in place")
        shutil.copyfile(DATASET_README_PATH, self.output_dir / "README.md")
        generated = []
        for evaluation in evaluations:
            LOGGER.info("Staging TxBench-PP task %s", evaluation.task_id)
            generated.append(self.generate_task(evaluation))
            LOGGER.info("Generated %s", generated[-1])
        LOGGER.info("Generated %d TxBench-PP task(s) in %s", len(generated), self.output_dir)
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
            time_horizon = str(evaluation.metadata.get("time_horizon", "unknown"))
            default_timeout = (
                LONG_AGENT_TIMEOUT_SEC
                if time_horizon.lower() == "long"
                else DEFAULT_AGENT_TIMEOUT_SEC
            )
            timeout_sec = max(default_timeout, int(evaluation.metadata.get("timeout_s", 0)))

            context: dict[str, Any] = {
                "build_timeout_sec": BUILD_TIMEOUT_SEC,
                "cpus": DAYTONA_CPUS,
                "grader_type": str(evaluation.grader["type"]),
                "input_file_count": len(input_files),
                "input_size_bytes": input_size_bytes,
                "kit": str(evaluation.metadata.get("kit", "unknown")),
                "memory_mb": DAYTONA_MEMORY_MB,
                "package_name": evaluation.package_name,
                "source_repo": UPSTREAM_REPO,
                "source_revision": self.source_revision,
                "storage_mb": DAYTONA_STORAGE_MB,
                "task": _adapt_task_prompt(evaluation.task),
                "task_family": str(evaluation.metadata.get("task", "unknown")),
                "task_id": evaluation.task_id,
                "time_horizon": time_horizon,
                "timeout_sec": timeout_sec,
            }
            self._render_template("task.toml", working_dir / "task.toml", context)
            self._render_template("instruction.md", working_dir / "instruction.md", context)
            self._copy_template("environment/Dockerfile", environment_dir / "Dockerfile")
            self._copy_template("tests/evaluate.py", tests_dir / "evaluate.py")
            self._copy_template("tests/test.sh", tests_dir / "test.sh", executable=True)
            self._copy_template("solution/solve.sh", solution_dir / "solve.sh", executable=True)
            shutil.copyfile(evaluation.eval_path, tests_dir / "upstream_eval.json")

            source_metadata = {
                "canary": evaluation.canary,
                "data_nodes": list(evaluation.data_nodes),
                "grader_type": evaluation.grader["type"],
                "input_files": input_files,
                "input_size_bytes": input_size_bytes,
                "latch_eval_tools_revision": LATCH_EVAL_TOOLS_REVISION,
                "latch_eval_tools_version": LATCH_EVAL_TOOLS_VERSION,
                "metadata": evaluation.metadata,
                "official_verifier_available": True,
                "source_repo": UPSTREAM_REPO,
                "source_revision": self.source_revision,
                "task_id": evaluation.task_id,
            }
            (tests_dir / "source_metadata.json").write_text(
                json.dumps(source_metadata, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            (solution_dir / "oracle_answer.json").write_text(
                json.dumps(_oracle_answer(evaluation.grader), indent=2, sort_keys=True) + "\n",
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
    matches = list(_OUTPUT_INSTRUCTION_PATTERN.finditer(adapted))
    if len(matches) != 1:
        raise ValueError("expected exactly one TxBench-PP output instruction")
    for marker in (_OPEN_TAG, _CLOSE_TAG):
        if adapted.count(marker) != 1:
            raise ValueError(f"expected exactly one TxBench-PP output marker {marker!r}")
    adapted = _OUTPUT_INSTRUCTION_PATTERN.sub(_HARBOR_OUTPUT_INSTRUCTION, adapted)
    adapted = adapted.replace(f"{_OPEN_TAG}\n", "")
    adapted = adapted.replace(f"\n{_CLOSE_TAG}", "")
    return adapted + "\n"


def _validate_grader_spec(spec: dict[str, Any], task_id: str) -> None:
    grader_type = spec.get("type")
    config = spec.get("config")
    if grader_type not in _SUPPORTED_GRADERS or not isinstance(config, dict):
        raise ValueError(f"TxBench-PP eval {task_id} has unsupported grader {grader_type!r}")
    if grader_type == "all_of":
        children = config.get("children")
        if not isinstance(children, list) or not children:
            raise ValueError(f"TxBench-PP eval {task_id} has an empty all_of grader")
        for child in children:
            if not isinstance(child, dict):
                raise ValueError(f"TxBench-PP eval {task_id} has an invalid grader child")
            _validate_grader_spec(child, task_id)


def _oracle_answer(spec: dict[str, Any]) -> dict[str, Any]:
    grader_type = spec["type"]
    config = spec["config"]
    if grader_type == "numeric_tolerance":
        ground_truth = config.get("ground_truth")
        if not isinstance(ground_truth, dict) or not ground_truth:
            raise ValueError("numeric_tolerance grader has no ground truth")
        answer: dict[str, Any] = {}
        for field, value in ground_truth.items():
            _set_answer_field(answer, field, copy.deepcopy(value))
        return answer
    if grader_type == "label_set_jaccard":
        answer_field = config.get("answer_field", "cell_types_predicted")
        labels = config.get("ground_truth_labels")
        if not isinstance(answer_field, str) or not isinstance(labels, list):
            raise ValueError("label_set_jaccard grader has an invalid answer contract")
        return {answer_field: copy.deepcopy(labels)}
    if grader_type == "marker_gene_precision_recall":
        answer_field = config.get("answer_field", "top_marker_genes")
        markers = config.get("canonical_markers", config.get("ground_truth_labels"))
        if not isinstance(answer_field, str) or not isinstance(markers, (dict, list)):
            raise ValueError("marker_gene_precision_recall grader has an invalid answer contract")
        return {answer_field: copy.deepcopy(markers)}
    if grader_type == "multiple_choice":
        if isinstance(config.get("correct_answer"), str):
            return {"answer": config["correct_answer"]}
        correct_answers = config.get("correct_answers")
        if isinstance(correct_answers, list) and correct_answers:
            return {"answer": correct_answers[0]}
        raise ValueError("multiple_choice grader has no correct answer")
    if grader_type == "all_of":
        answer: dict[str, Any] = {}
        for child in config["children"]:
            _merge_answers(answer, _oracle_answer(child))
        return answer
    raise ValueError(f"unsupported oracle grader type: {grader_type!r}")


def _set_answer_field(answer: dict[str, Any], field: Any, value: Any) -> None:
    if not isinstance(field, str) or not field:
        raise ValueError("grader ground-truth field must be a non-empty string")
    current = answer
    parts = field.split(".")
    for part in parts[:-1]:
        nested = current.setdefault(part, {})
        if not isinstance(nested, dict):
            raise ValueError(f"conflicting nested oracle field: {field}")
        current = nested
    final = parts[-1]
    if final in current and current[final] != value:
        raise ValueError(f"conflicting oracle field: {field}")
    current[final] = value


def _merge_answers(target: dict[str, Any], addition: dict[str, Any], prefix: str = "") -> None:
    for key, value in addition.items():
        field = f"{prefix}.{key}" if prefix else key
        if key not in target:
            target[key] = copy.deepcopy(value)
        elif isinstance(target[key], dict) and isinstance(value, dict):
            _merge_answers(target[key], value, field)
        elif target[key] != value:
            raise ValueError(f"conflicting oracle field: {field}")


def _valid_cache(manifest_path: Path, nodes: tuple[str, ...]) -> bool:
    if not manifest_path.is_file():
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("nodes") != list(nodes) or not isinstance(manifest.get("files"), list):
            return False
        if not manifest["files"]:
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
        return True
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
    source = root / f".txbench-pp-rename-source-{token}"
    target = root / f".txbench-pp-rename-target-{token}"
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
