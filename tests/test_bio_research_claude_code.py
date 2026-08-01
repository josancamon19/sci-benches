from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import tarfile
from pathlib import Path
from typing import Any

import pytest
import yaml
from harbor.agents.installed.claude_code import ClaudeCode
from harbor.environments.base import ExecResult
from harbor.models.job.config import JobConfig

import shared.agents.bio_research_claude_code as harness
from biomistery_bench.adapter import HARNESS_ALLOWED_DOMAINS
from shared.agents.bio_research_claude_code import (
    BIO_RESEARCH_ARTIFACT_PATH,
    BIO_RESEARCH_MCP_HEALTH_ARTIFACT_PATH,
    BIO_RESEARCH_PLUGIN_REVISION,
    BIO_RESEARCH_REMOTE_DIR,
    BioResearchClaudeCode,
)

ROOT = Path(__file__).parents[1]
EXPECTED_SKILLS = [
    "instrument-data-to-allotrope",
    "nextflow-development",
    "scientific-problem-selection",
    "scvi-tools",
    "single-cell-rna-qc",
    "start",
]
EXPECTED_MCP_SERVERS = [
    "biorender",
    "biorxiv",
    "c-trials",
    "chembl",
    "consensus",
    "ot",
    "owkin",
    "pubmed",
    "synapse",
    "wiley",
]


class RecordingEnvironment:
    def __init__(self) -> None:
        self.empty_calls: list[tuple[list[str], bool]] = []
        self.upload_calls: list[tuple[Path, str]] = []
        self.exec_calls: list[dict[str, Any]] = []

    async def empty_dirs(self, dirs: list[str], *, chmod: bool = True) -> ExecResult:
        self.empty_calls.append((dirs, chmod))
        return ExecResult(return_code=0)

    async def upload_dir(self, source_dir: Path, target_dir: str) -> None:
        self.upload_calls.append((source_dir, target_dir))

    async def exec(self, command: str, **kwargs: Any) -> ExecResult:
        self.exec_calls.append({"command": command, **kwargs})
        return ExecResult(return_code=0)


def _write_plugin(plugin_dir: Path) -> Path:
    manifest_dir = plugin_dir / ".claude-plugin"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "plugin.json").write_text(
        json.dumps({"name": "bio-research", "version": "1.2.0"}),
        encoding="utf-8",
    )
    mcp_servers = {
        "benchling": {"url": ""},
        **{name: {"url": f"https://{name}.example.test/mcp"} for name in EXPECTED_MCP_SERVERS},
    }
    (plugin_dir / ".mcp.json").write_text(
        json.dumps({"mcpServers": mcp_servers}),
        encoding="utf-8",
    )
    for skill in EXPECTED_SKILLS:
        skill_dir = plugin_dir / "skills" / skill
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(f"# {skill}\n", encoding="utf-8")
    return plugin_dir


def _build_plugin_archive(tmp_path: Path) -> Path:
    archive_root = tmp_path / "source" / f"knowledge-work-plugins-{BIO_RESEARCH_PLUGIN_REVISION}"
    _write_plugin(archive_root / "bio-research")
    archive_path = tmp_path / "upstream.tar.gz"
    with tarfile.open(archive_path, mode="w:gz") as archive:
        archive.add(archive_root, arcname=archive_root.name)
    return archive_path


def test_plugin_is_downloaded_from_pinned_upstream_and_cached(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    archive_path = _build_plugin_archive(tmp_path)
    with archive_path.open("rb") as stream:
        archive_sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
    download_calls: list[Path] = []

    def copy_archive(destination: Path) -> None:
        download_calls.append(destination)
        shutil.copyfile(archive_path, destination)

    monkeypatch.setattr(harness, "BIO_RESEARCH_ARCHIVE_SHA256", archive_sha256)
    monkeypatch.setattr(harness, "_download_archive", copy_archive)

    cache_dir = tmp_path / "cache"
    plugin_dir = harness._ensure_plugin_source(cache_dir)
    cached_again = harness._ensure_plugin_source(cache_dir)
    artifact = harness._validate_plugin_source(plugin_dir)

    assert plugin_dir == cached_again
    assert len(download_calls) == 1
    assert artifact["archive_sha256"] == archive_sha256
    assert artifact["revision"] == BIO_RESEARCH_PLUGIN_REVISION
    assert artifact["skills"] == EXPECTED_SKILLS
    assert artifact["mcp_servers"] == EXPECTED_MCP_SERVERS


def test_agent_uses_labeled_plugin_treatment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plugin_dir = _write_plugin(tmp_path / "plugin")
    monkeypatch.setattr(harness, "_ensure_plugin_source", lambda _cache: plugin_dir)
    agent = BioResearchClaudeCode(logs_dir=tmp_path / "logs")

    assert agent.name() == "claude-code-bio-research"
    assert f"--plugin-dir {BIO_RESEARCH_REMOTE_DIR}" in agent.build_cli_flags()
    assert agent.plugin_artifact["revision"] == BIO_RESEARCH_PLUGIN_REVISION
    assert len(agent.plugin_artifact["mcp_servers"]) == 10
    assert agent.plugin_artifact["omitted_mcp_servers"] == ["benchling"]


def test_validator_rejects_missing_plugin_source(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="plugin directory not found"):
        harness._validate_plugin_source(tmp_path / "missing")


def test_agent_stages_plugin_and_manifest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    async def skip_claude_setup(_agent: ClaudeCode, _environment: RecordingEnvironment) -> None:
        return None

    monkeypatch.setattr(ClaudeCode, "setup", skip_claude_setup)
    plugin_dir = _write_plugin(tmp_path / "plugin")
    monkeypatch.setattr(harness, "_ensure_plugin_source", lambda _cache: plugin_dir)
    environment = RecordingEnvironment()
    agent = BioResearchClaudeCode(logs_dir=tmp_path / "logs")

    asyncio.run(agent.setup(environment))  # type: ignore[arg-type]

    assert environment.empty_calls == [([BIO_RESEARCH_REMOTE_DIR], False)]
    assert environment.upload_calls == [(plugin_dir, BIO_RESEARCH_REMOTE_DIR)]
    setup_command = environment.exec_calls[-1]["command"]
    assert "del(.mcpServers.benchling)" in setup_command
    assert BIO_RESEARCH_ARTIFACT_PATH in setup_command


def test_agent_can_healthcheck_mcps_and_prefix_instruction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    forwarded_instructions: list[str] = []

    async def record_claude_run(
        _agent: ClaudeCode,
        instruction: str,
        _environment: RecordingEnvironment,
        _context: object,
    ) -> None:
        forwarded_instructions.append(instruction)

    plugin_dir = _write_plugin(tmp_path / "plugin")
    monkeypatch.setattr(harness, "_ensure_plugin_source", lambda _cache: plugin_dir)
    monkeypatch.setattr(ClaudeCode, "run", record_claude_run)
    environment = RecordingEnvironment()
    agent = BioResearchClaudeCode(
        logs_dir=tmp_path / "logs",
        mcp_healthcheck=True,
        instruction_prefix="Explore the Bio Research plugin first.",
    )

    asyncio.run(agent.run("Solve the task.", environment, object()))  # type: ignore[arg-type]

    assert forwarded_instructions == ["Explore the Bio Research plugin first.\n\nSolve the task."]
    health_call = environment.exec_calls[-1]
    assert "mcp list" in health_call["command"]
    assert BIO_RESEARCH_MCP_HEALTH_ARTIFACT_PATH in health_call["command"]
    assert health_call["env"]["CLAUDE_CONFIG_DIR"] == "/tmp/claude-mcp-health"
    assert health_call["cwd"] == "/app"
    assert health_call["timeout_sec"] == 60


def test_job_is_valid_and_does_not_reference_vendored_plugin() -> None:
    job_text = (ROOT / "job.yaml").read_text(encoding="utf-8")
    config = JobConfig.model_validate(yaml.safe_load(job_text))

    assert "vendor/anthropics/knowledge-work-plugins" not in job_text
    assert "plugin_source_dir" not in job_text
    assert "biomistery_bench.harness" not in job_text
    assert "shared.harness" not in job_text
    assert config.n_attempts == 1
    assert config.n_concurrent_trials == 4
    assert len(config.agents) == 2
    assert config.agents[0].name == "claude-code"
    assert config.agents[1].import_path == (
        "shared.agents.bio_research_claude_code:BioResearchClaudeCode"
    )
    assert config.agents[0].model_name == config.agents[1].model_name
    assert config.agents[0].env == config.agents[1].env
    assert (
        config.agents[1]
        .kwargs["instruction_prefix"]
        .startswith("Before solving the task, explore the available Bio Research plugin")
    )
    assert "mcp_healthcheck" not in config.agents[1].kwargs
    assert config.datasets[0].task_names == ["hb002", "hb010"]
    assert len(set(HARNESS_ALLOWED_DOMAINS) | set(config.agents[1].extra_allowed_hosts)) == 20
