from __future__ import annotations

import csv
import json
import logging
import re
import shutil
import stat
import uuid
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from huggingface_hub import HfApi, hf_hub_download
from huggingface_hub.errors import GatedRepoError
from jinja2 import Environment, StrictUndefined

DEFAULT_MAX_ARCHIVE_BYTES = 1_000_000_000
TEMPLATE_DIR = Path(__file__).parent / "task-template"

HARNESS_ALLOWED_DOMAINS = (
    "bioconductor.org",
    "conda.anaconda.org",
    "cran.r-project.org",
    "ebi.ac.uk",
    "ensembl.org",
    "hgdownload.soe.ucsc.edu",
    "ncbi.nlm.nih.gov",
    "pypi.org",
    "reactome.org",
    "repo.anaconda.com",
    "uniprot.org",
)

# These mirrors duplicate retained package or bioinformatics hosts. Excluding them
# leaves room for job-specific MCP endpoints under Daytona's 20-domain limit.
REDUNDANT_ALLOWED_DOMAINS = frozenset(
    {
        "bioconda.github.io",
        "cran.rstudio.com",
        "ftp.ebi.ac.uk",
        "ftp.ensembl.org",
        "ftp.ncbi.nlm.nih.gov",
    }
)


@dataclass(frozen=True)
class DatasetRelease:
    name: str
    repo_id: str
    revision: str
    expected_problem_count: int
    aggregate_archive: bool


DATASET_RELEASE = DatasetRelease(
    name="full",
    repo_id="Anthropic/BioMysteryBench-full",
    revision="b5a889c4757214ec9a6ade876b734f920a7799db",
    expected_problem_count=90,
    aggregate_archive=False,
)

_REQUIRED_COLUMNS = {
    "id",
    "question",
    "answer_rubric",
    "allowed_domains",
    "human_solvable",
}
_TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_SCORE_RULE_PATTERN = re.compile(r"\s+Score 1\.0 if\b", re.IGNORECASE)
_HOST_PATTERN = re.compile(
    r"^(?:\*\.)?(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$"
)
_DAYTONA_STORAGE_MB = 10_240

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Problem:
    task_id: str
    question: str
    answer_rubric: str
    allowed_domains: tuple[str, ...]
    human_solvable: bool

    @property
    def package_name(self) -> str:
        return f"anthropic/biomystery-bench-{self.task_id.lower()}"


class DatasetSource:
    """Resolve metadata and only the archives needed for selected tasks."""

    def __init__(
        self,
        release: DatasetRelease,
        *,
        revision: str | None = None,
        cache_dir: Path | None = None,
        source_dir: Path | None = None,
        token: str | None = None,
    ) -> None:
        self.release = release
        self.revision = revision or release.revision
        self.cache_dir = cache_dir.expanduser().resolve() if cache_dir else None
        self.source_dir = source_dir.expanduser().resolve() if source_dir else None
        self._token = token
        self._remote_file_sizes: dict[str, int] | None = None

    @property
    def source_revision(self) -> str:
        return "local" if self.source_dir is not None else self.revision

    def metadata_path(self) -> Path:
        return self._resolve("problems.csv")

    def archive_path(self, task_id: str) -> Path:
        return self._resolve(self._archive_name(task_id))

    def archive_size(self, task_id: str) -> int:
        relative_name = self._archive_name(task_id)
        if self.source_dir is not None:
            path = self.source_dir.joinpath(*PurePosixPath(relative_name).parts)
            if not path.is_file():
                raise FileNotFoundError(f"Missing BioMysteryBench source file: {path}")
            return path.stat().st_size

        sizes = self._get_remote_file_sizes()
        try:
            return sizes[relative_name]
        except KeyError as exc:
            raise FileNotFoundError(
                f"{self.release.repo_id}@{self.revision} does not contain "
                f"{relative_name} with known file-size metadata"
            ) from exc

    def _archive_name(self, task_id: str) -> str:
        return "data.zip" if self.release.aggregate_archive else f"data/{task_id}.zip"

    def _get_remote_file_sizes(self) -> dict[str, int]:
        if self._remote_file_sizes is not None:
            return self._remote_file_sizes
        self._require_token()
        try:
            info = HfApi(token=self._token).dataset_info(
                repo_id=self.release.repo_id,
                revision=self.revision,
                files_metadata=True,
            )
        except GatedRepoError as exc:
            raise self._gated_repo_error() from exc

        self._remote_file_sizes = {
            sibling.rfilename: sibling.size
            for sibling in info.siblings or []
            if isinstance(sibling.size, int)
        }
        return self._remote_file_sizes

    def _resolve(self, relative_name: str) -> Path:
        if self.source_dir is not None:
            path = self.source_dir.joinpath(*PurePosixPath(relative_name).parts)
            if not path.is_file():
                raise FileNotFoundError(f"Missing BioMysteryBench source file: {path}")
            return path

        self._require_token()
        try:
            return Path(
                hf_hub_download(
                    repo_id=self.release.repo_id,
                    repo_type="dataset",
                    revision=self.revision,
                    filename=relative_name,
                    cache_dir=str(self.cache_dir) if self.cache_dir is not None else None,
                    token=self._token,
                )
            )
        except GatedRepoError as exc:
            raise self._gated_repo_error() from exc

    def _require_token(self) -> None:
        if not self._token:
            raise RuntimeError(
                f"{self.release.repo_id} requires authentication. Put HF_TOKEN in "
                "the adapter's .env file after accepting the dataset terms."
            )

    def _gated_repo_error(self) -> RuntimeError:
        return RuntimeError(
            f"{self.release.repo_id} is gated. Accept its evaluation-only terms "
            "on Hugging Face and provide HF_TOKEN in the adapter's .env file."
        )


def discover_problems(source: DatasetSource) -> list[Problem]:
    metadata_path = source.metadata_path()
    with metadata_path.open(encoding="utf-8-sig", newline="") as metadata_file:
        reader = csv.DictReader(metadata_file)
        missing = _REQUIRED_COLUMNS - set(reader.fieldnames or ())
        if missing:
            raise ValueError(
                f"Missing required columns in {metadata_path}: {', '.join(sorted(missing))}"
            )
        rows = list(reader)

    expected = source.release.expected_problem_count
    if len(rows) != expected:
        raise ValueError(f"Expected {expected} {source.release.name} problems, found {len(rows)}")

    problems: list[Problem] = []
    seen_ids: set[str] = set()
    for row in rows:
        task_id = row["id"].strip()
        if not _TASK_ID_PATTERN.fullmatch(task_id):
            raise ValueError(f"Invalid BioMysteryBench problem ID: {task_id!r}")
        if task_id in seen_ids:
            raise ValueError(f"Duplicate BioMysteryBench problem ID: {task_id}")
        seen_ids.add(task_id)

        question = row["question"].strip()
        answer_rubric = row["answer_rubric"].strip()
        if not question:
            raise ValueError(f"Problem {task_id} has an empty question")
        if not answer_rubric:
            raise ValueError(f"Problem {task_id} has an empty answer rubric")

        human_solvable_text = row["human_solvable"].strip().lower()
        if human_solvable_text not in {"yes", "no"}:
            raise ValueError(
                f"Problem {task_id} has invalid human_solvable value: {row['human_solvable']!r}"
            )

        metadata_domains = _parse_allowed_domains(row["allowed_domains"], task_id=task_id)
        problems.append(
            Problem(
                task_id=task_id,
                question=question,
                answer_rubric=answer_rubric,
                allowed_domains=tuple(
                    dict.fromkeys(
                        domain
                        for domain in (*metadata_domains, *HARNESS_ALLOWED_DOMAINS)
                        if domain not in REDUNDANT_ALLOWED_DOMAINS
                    )
                ),
                human_solvable=human_solvable_text == "yes",
            )
        )
    return problems


class BioMysteryBenchAdapter:
    def __init__(
        self,
        *,
        source: DatasetSource,
        output_dir: Path,
        limit: int | None = None,
        overwrite: bool = False,
        task_ids: Iterable[str] | None = None,
        max_archive_bytes: int | None = DEFAULT_MAX_ARCHIVE_BYTES,
        template_dir: Path = TEMPLATE_DIR,
    ) -> None:
        if limit is not None and limit < 1:
            raise ValueError("limit must be at least 1")
        if max_archive_bytes is not None and max_archive_bytes < 1:
            raise ValueError("max_archive_bytes must be at least 1")
        self.source = source
        self.output_dir = output_dir.expanduser().resolve()
        self.limit = limit
        self.overwrite = overwrite
        self.task_ids = list(task_ids) if task_ids is not None else None
        self.max_archive_bytes = max_archive_bytes
        self.template_dir = template_dir.expanduser().resolve()
        self._jinja = Environment(
            undefined=StrictUndefined,
            autoescape=False,
            keep_trailing_newline=True,
        )

    def run(self) -> list[Path]:
        problems = discover_problems(self.source)
        if self.task_ids is not None:
            requested = set(self.task_ids)
            available = {problem.task_id for problem in problems}
            unknown = sorted(requested - available)
            if unknown:
                raise ValueError(f"Unknown task ID(s): {', '.join(unknown)}")
            problems = [problem for problem in problems if problem.task_id in requested]

        if self.max_archive_bytes is not None:
            eligible: list[Problem] = []
            skipped: list[tuple[str, int]] = []
            for problem in problems:
                archive_bytes = self.source.archive_size(problem.task_id)
                if archive_bytes > self.max_archive_bytes:
                    skipped.append((problem.task_id, archive_bytes))
                else:
                    eligible.append(problem)
            problems = eligible
            if skipped:
                skipped_text = ", ".join(
                    f"{task_id} ({size / 1_000_000_000:.2f} GB)" for task_id, size in skipped
                )
                LOGGER.warning(
                    "Skipping %d task archive(s) over %.2f GB: %s",
                    len(skipped),
                    self.max_archive_bytes / 1_000_000_000,
                    skipped_text,
                )

        if self.limit is not None:
            problems = problems[: self.limit]
        if not problems:
            raise ValueError("No BioMysteryBench tasks remain after selection and size filtering")

        self.output_dir.mkdir(parents=True, exist_ok=True)
        generated = [self.generate_task(problem) for problem in problems]
        LOGGER.info("Generated %d task(s) in %s", len(generated), self.output_dir)
        return generated

    def generate_task(self, problem: Problem) -> Path:
        task_dir = self.output_dir / problem.task_id
        if task_dir.exists() and not self.overwrite:
            raise FileExistsError(
                f"Task already exists: {task_dir}. Pass --overwrite to replace it."
            )

        temporary_dir = self.output_dir / f".{problem.task_id}.tmp-{uuid.uuid4().hex}"
        environment_dir = temporary_dir / "environment"
        solution_dir = temporary_dir / "solution"
        tests_dir = temporary_dir / "tests"
        task_files_dir = environment_dir / "task_files"
        try:
            for directory in (task_files_dir, solution_dir, tests_dir):
                directory.mkdir(parents=True, exist_ok=True)

            archive_size_bytes = self.source.archive_size(problem.task_id)
            archive_path = self.source.archive_path(problem.task_id)
            staged_files, staged_bytes = _extract_problem_archive(
                archive_path,
                task_files_dir,
                task_id=problem.task_id,
                aggregate=self.source.release.aggregate_archive,
            )
            context = {
                "allowed_domains": problem.allowed_domains,
                "human_solvable": problem.human_solvable,
                "package_name": problem.package_name,
                "question": problem.question,
                "source_repo": self.source.release.repo_id,
                "source_revision": self.source.source_revision,
                "source_split": self.source.release.name,
                "storage_mb": _DAYTONA_STORAGE_MB,
                "task_id": problem.task_id,
            }

            self._render_template("task.toml", temporary_dir / "task.toml", context)
            self._render_template("instruction.md", temporary_dir / "instruction.md", context)
            self._copy_template("environment/Dockerfile", environment_dir / "Dockerfile")
            self._copy_template("tests/test.sh", tests_dir / "test.sh", executable=True)
            self._copy_template("tests/judge.toml", tests_dir / "judge.toml")
            self._copy_template("tests/judge_prompt.md", tests_dir / "judge_prompt.md")
            self._copy_template("solution/solve.sh", solution_dir / "solve.sh", executable=True)

            reference = (
                f"# Question\n\n{problem.question}\n\n"
                f"# Official answer rubric\n\n{problem.answer_rubric}\n\n"
                "# Benchmark lookup rule\n\n"
                "Actively looking up GEO, SRA, ENA, or BioProject accession IDs to "
                "identify the original dataset, source publication, or study metadata "
                "is prohibited. Standard bioinformatics database use, including gene ID "
                "lookup, sequence annotation, reference genome download, and BLAST, is "
                "allowed. Recalling information from model memory is not prohibited.\n"
            )
            (tests_dir / "reference.md").write_text(reference, encoding="utf-8")
            (solution_dir / "oracle_answer.txt").write_text(
                _oracle_answer_from_rubric(problem.answer_rubric) + "\n",
                encoding="utf-8",
            )
            (tests_dir / "source.json").write_text(
                json.dumps(
                    {
                        "archive": archive_path.name,
                        "archive_size_bytes": archive_size_bytes,
                        "files": staged_files,
                        "problem_id": problem.task_id,
                        "repo_id": self.source.release.repo_id,
                        "revision": self.source.source_revision,
                        "split": self.source.release.name,
                        "staged_size_bytes": staged_bytes,
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )

            if task_dir.exists():
                shutil.rmtree(task_dir)
            temporary_dir.rename(task_dir)
            return task_dir
        except Exception:
            shutil.rmtree(temporary_dir, ignore_errors=True)
            raise

    def _render_template(
        self, relative_name: str, destination: Path, context: dict[str, object]
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


def _parse_allowed_domains(value: str, *, task_id: str) -> tuple[str, ...]:
    domains: list[str] = []
    for raw_domain in value.split(","):
        domain = raw_domain.strip().lower()
        if not domain:
            continue
        if not _HOST_PATTERN.fullmatch(domain):
            raise ValueError(f"Problem {task_id} has invalid allowed domain: {domain!r}")
        if domain not in domains:
            domains.append(domain)
    if not domains:
        raise ValueError(f"Problem {task_id} has no allowed domains")
    return tuple(domains)


def _oracle_answer_from_rubric(answer_rubric: str) -> str:
    answer = _SCORE_RULE_PATTERN.split(answer_rubric, maxsplit=1)[0].strip()
    mentions_prefix = "Answer mentions all of the following:"
    if answer.casefold().startswith(mentions_prefix.casefold()):
        answer = f"The answer is: {answer[len(mentions_prefix):].strip()}"
    return answer


def _extract_problem_archive(
    archive_path: Path,
    destination: Path,
    *,
    task_id: str,
    aggregate: bool,
) -> tuple[list[str], int]:
    staged_files: list[str] = []
    staged_bytes = 0
    with zipfile.ZipFile(archive_path) as archive:
        for info in archive.infolist():
            relative_path = _archive_member_path(
                info.filename,
                task_id=task_id,
                aggregate=aggregate,
            )
            if relative_path is None:
                continue

            unix_mode = info.external_attr >> 16
            if stat.S_ISLNK(unix_mode):
                raise ValueError(f"Archive contains a symbolic link: {info.filename!r}")
            if info.is_dir():
                destination.joinpath(*relative_path.parts).mkdir(parents=True, exist_ok=True)
                continue
            file_type = stat.S_IFMT(unix_mode)
            if file_type and not stat.S_ISREG(unix_mode):
                raise ValueError(f"Archive contains a non-regular file: {info.filename!r}")

            output_path = destination.joinpath(*relative_path.parts)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source_file, output_path.open("wb") as output_file:
                shutil.copyfileobj(source_file, output_file)
            output_path.chmod(0o755 if unix_mode & 0o111 else 0o644)
            staged_files.append(relative_path.as_posix())
            staged_bytes += info.file_size

    if not staged_files:
        raise ValueError(f"No files for {task_id} found in {archive_path}")
    return sorted(staged_files), staged_bytes


def _archive_member_path(
    filename: str,
    *,
    task_id: str,
    aggregate: bool,
) -> PurePosixPath | None:
    if not filename or "\\" in filename or "\x00" in filename:
        raise ValueError(f"Unsafe archive member: {filename!r}")
    member = PurePosixPath(filename)
    if member.is_absolute() or ".." in member.parts:
        raise ValueError(f"Unsafe archive member: {filename!r}")

    parts = member.parts
    if aggregate:
        if not parts or parts[0] != task_id:
            return None
        parts = parts[1:]
    if not parts:
        return None
    return PurePosixPath(*parts)
