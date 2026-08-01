from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from harbor.agents.installed.claude_code import ClaudeCode
from harbor.environments.base import ExecResult
from harbor.models.job.config import JobConfig

from biomistery_bench.adapter import HARNESS_ALLOWED_DOMAINS
from biomistery_bench.harness import (
    BIO_RESEARCH_ARTIFACT_PATH,
    BIO_RESEARCH_PLUGIN_REVISION,
    BIO_RESEARCH_REMOTE_DIR,
    BioResearchClaudeCode,
)

ROOT = Path(__file__).parents[1]
PLUGIN_DIR = ROOT / "vendor/anthropics/knowledge-work-plugins/bio-research"


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


def test_vendored_plugin_matches_pinned_upstream() -> None:
    manifest = json.loads(
        (PLUGIN_DIR / ".claude-plugin/plugin.json").read_text(encoding="utf-8")
    )
    mcp_config = json.loads((PLUGIN_DIR / ".mcp.json").read_text(encoding="utf-8"))
    skills = sorted(path.parent.name for path in (PLUGIN_DIR / "skills").glob("*/SKILL.md"))

    assert manifest["name"] == "bio-research"
    assert manifest["version"] == "1.2.0"
    assert skills == [
        "instrument-data-to-allotrope",
        "nextflow-development",
        "scientific-problem-selection",
        "scvi-tools",
        "single-cell-rna-qc",
        "start",
    ]
    assert len(mcp_config["mcpServers"]) == 11
    assert mcp_config["mcpServers"]["benchling"]["url"] == ""


def test_agent_uses_labeled_plugin_treatment(tmp_path: Path) -> None:
    agent = BioResearchClaudeCode(
        logs_dir=tmp_path / "logs",
        plugin_source_dir=PLUGIN_DIR,
    )

    assert agent.name() == "claude-code-bio-research"
    assert f"--plugin-dir {BIO_RESEARCH_REMOTE_DIR}" in agent.build_cli_flags()
    assert agent.plugin_artifact["revision"] == BIO_RESEARCH_PLUGIN_REVISION
    assert len(agent.plugin_artifact["mcp_servers"]) == 10
    assert agent.plugin_artifact["omitted_mcp_servers"] == ["benchling"]


def test_agent_rejects_missing_plugin_source(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="plugin directory not found"):
        BioResearchClaudeCode(
            logs_dir=tmp_path / "logs",
            plugin_source_dir=tmp_path / "missing",
        )


def test_agent_stages_plugin_and_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def skip_claude_setup(
        _agent: ClaudeCode, _environment: RecordingEnvironment
    ) -> None:
        return None

    monkeypatch.setattr(ClaudeCode, "setup", skip_claude_setup)
    environment = RecordingEnvironment()
    agent = BioResearchClaudeCode(
        logs_dir=tmp_path / "logs",
        plugin_source_dir=PLUGIN_DIR,
    )

    asyncio.run(agent.setup(environment))  # type: ignore[arg-type]

    assert environment.empty_calls == [([BIO_RESEARCH_REMOTE_DIR], False)]
    assert environment.upload_calls == [(PLUGIN_DIR.resolve(), BIO_RESEARCH_REMOTE_DIR)]
    setup_command = environment.exec_calls[-1]["command"]
    assert "del(.mcpServers.benchling)" in setup_command
    assert BIO_RESEARCH_ARTIFACT_PATH in setup_command


def test_job_compares_identical_opus_agents_with_and_without_plugin() -> None:
    config = JobConfig.model_validate(yaml.safe_load((ROOT / "job.yaml").read_text()))

    assert config.n_attempts == 1
    assert config.job_name.startswith("2026-")
    assert len(config.datasets) == 1
    assert config.datasets[0].task_names == ["hb002", "hb010"]
    assert len(config.agents) == 3

    baseline = config.agents[1]
    treatment = config.agents[2]
    assert baseline.name == "claude-code"
    assert treatment.import_path == (
        "biomistery_bench.harness:BioResearchClaudeCode"
    )
    assert treatment.name is None
    assert baseline.model_name == treatment.model_name == "anthropic/claude-opus-4-8"
    assert baseline.env == treatment.env
    assert baseline.kwargs == {}
    assert treatment.kwargs["plugin_source_dir"] == str(PLUGIN_DIR.relative_to(ROOT))
    assert baseline.extra_allowed_hosts == ["api.anthropic.com"]
    assert "mcp.platform.opentargets.org" in treatment.extra_allowed_hosts
    assert "github.com" not in treatment.extra_allowed_hosts
    assert len(set(HARNESS_ALLOWED_DOMAINS) | set(treatment.extra_allowed_hosts)) == 20
