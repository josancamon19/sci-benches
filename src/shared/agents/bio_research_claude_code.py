from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import tarfile
import tempfile
from pathlib import Path
from typing import Any, override
from urllib.request import urlopen

from harbor.agents.installed.base import CliFlag
from harbor.agents.installed.claude_code import ClaudeCode
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.skills import compute_skill_digest

BIO_RESEARCH_PLUGIN_REVISION = "ace462f156540bf75fb8ea9427d343a38c490015"
BIO_RESEARCH_PLUGIN_VERSION = "1.2.0"
BIO_RESEARCH_PLUGIN_REPOSITORY = "https://github.com/anthropics/knowledge-work-plugins"
BIO_RESEARCH_ARCHIVE_URL = (
    "https://codeload.github.com/anthropics/knowledge-work-plugins/tar.gz/"
    f"{BIO_RESEARCH_PLUGIN_REVISION}"
)
BIO_RESEARCH_ARCHIVE_SHA256 = "7dd036eec336c6dbc95a86c5026987eec6d23f0b6755fb3a177463a74a0ab7c5"
BIO_RESEARCH_REMOTE_DIR = "/harbor/plugins/bio-research"
BIO_RESEARCH_ARTIFACT_PATH = "/logs/artifacts/bio-research-plugin.json"
BIO_RESEARCH_MCP_HEALTH_ARTIFACT_PATH = "/logs/artifacts/bio-research-mcp-health.txt"

_PLUGIN_MANIFEST_FILE = ".claude-plugin/plugin.json"
_MCP_CONFIG_FILE = ".mcp.json"
_ARCHIVE_CHECKSUM_FILE = ".archive-sha256"
_UNCONFIGURED_MCP_SERVERS = frozenset({"benchling"})


class BioResearchClaudeCode(ClaudeCode):
    """Claude Code with Anthropic's Bio Research plugin staged per trial."""

    CLI_FLAGS = [
        *ClaudeCode.CLI_FLAGS,
        CliFlag("plugin_dir", cli="--plugin-dir", type="str"),
    ]

    @staticmethod
    @override
    def name() -> str:
        return "claude-code-bio-research"

    def __init__(
        self,
        logs_dir: Path,
        plugin_cache_dir: str | Path | None = None,
        mcp_healthcheck: bool = False,
        instruction_prefix: str | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        cache_dir = (
            Path(plugin_cache_dir).expanduser().resolve()
            if plugin_cache_dir is not None
            else _default_plugin_cache_dir()
        )
        self.plugin_source_dir = _ensure_plugin_source(cache_dir)
        self.plugin_artifact = _validate_plugin_source(self.plugin_source_dir)
        self.mcp_healthcheck = mcp_healthcheck
        self.instruction_prefix = instruction_prefix.strip() if instruction_prefix else None
        kwargs["plugin_dir"] = BIO_RESEARCH_REMOTE_DIR
        super().__init__(*args, logs_dir=logs_dir, **kwargs)

    @override
    async def setup(self, environment: BaseEnvironment) -> None:
        await super().setup(environment)
        await environment.empty_dirs([BIO_RESEARCH_REMOTE_DIR], chmod=False)
        await environment.upload_dir(
            source_dir=self.plugin_source_dir,
            target_dir=BIO_RESEARCH_REMOTE_DIR,
        )

        artifact_json = json.dumps(self.plugin_artifact, indent=2, sort_keys=True) + "\n"
        await self.exec_as_root(
            environment,
            command=(
                f"jq 'del(.mcpServers.benchling)' "
                f"{BIO_RESEARCH_REMOTE_DIR}/{_MCP_CONFIG_FILE} > "
                f"{BIO_RESEARCH_REMOTE_DIR}/{_MCP_CONFIG_FILE}.tmp && "
                f"mv {BIO_RESEARCH_REMOTE_DIR}/{_MCP_CONFIG_FILE}.tmp "
                f"{BIO_RESEARCH_REMOTE_DIR}/{_MCP_CONFIG_FILE} && "
                f"chmod -R a+rX {BIO_RESEARCH_REMOTE_DIR} && "
                f"mkdir -p /logs/artifacts && "
                f"printf %s {shlex.quote(artifact_json)} > "
                f"{BIO_RESEARCH_ARTIFACT_PATH}"
            ),
        )

    @override
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        if self.mcp_healthcheck:
            await self._capture_mcp_health(environment)
        if self.instruction_prefix:
            instruction = f"{self.instruction_prefix}\n\n{instruction}"
        await super().run(instruction, environment, context)

    async def _capture_mcp_health(self, environment: BaseEnvironment) -> None:
        await self.exec_as_agent(
            environment,
            command=(
                'export PATH="$HOME/.local/bin:$PATH"; '
                "mkdir -p /tmp/claude-mcp-health /logs/artifacts; "
                f"claude --plugin-dir {BIO_RESEARCH_REMOTE_DIR} mcp list "
                f"2>&1 | tee {BIO_RESEARCH_MCP_HEALTH_ARTIFACT_PATH}"
            ),
            env={
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                "CLAUDE_CONFIG_DIR": "/tmp/claude-mcp-health",
                "IS_SANDBOX": "1",
            },
            cwd="/app",
            timeout_sec=60,
        )


def _default_plugin_cache_dir() -> Path:
    cache_home = os.environ.get("XDG_CACHE_HOME")
    root = Path(cache_home).expanduser() if cache_home else Path.home() / ".cache"
    return (root / "harbor" / "plugins").resolve()


def _ensure_plugin_source(cache_dir: Path) -> Path:
    revision_dir = cache_dir / "knowledge-work-plugins" / BIO_RESEARCH_PLUGIN_REVISION
    plugin_dir = revision_dir / "bio-research"
    if _is_valid_cached_plugin(revision_dir):
        return plugin_dir
    if revision_dir.exists():
        raise RuntimeError(
            "Cached Bio Research plugin is incomplete or invalid: "
            f"{revision_dir}. Remove that revision directory and retry."
        )

    revision_dir.parent.mkdir(parents=True, exist_ok=True)
    prefix = f".{BIO_RESEARCH_PLUGIN_REVISION[:12]}-"
    with tempfile.TemporaryDirectory(prefix=prefix, dir=revision_dir.parent) as raw_temp:
        temp_dir = Path(raw_temp)
        archive_path = temp_dir / "upstream.tar.gz"
        _download_archive(archive_path)
        actual_sha256 = _sha256(archive_path)
        if actual_sha256 != BIO_RESEARCH_ARCHIVE_SHA256:
            raise RuntimeError(
                "Bio Research plugin archive checksum mismatch: "
                f"expected {BIO_RESEARCH_ARCHIVE_SHA256}, got {actual_sha256}"
            )

        extracted_dir = temp_dir / "extracted"
        archive_root = f"knowledge-work-plugins-{BIO_RESEARCH_PLUGIN_REVISION}"
        plugin_prefix = f"{archive_root}/bio-research/"
        with tarfile.open(archive_path, mode="r:gz") as archive:
            members = [
                member
                for member in archive.getmembers()
                if member.name == plugin_prefix.rstrip("/") or member.name.startswith(plugin_prefix)
            ]
            if not members:
                raise RuntimeError("Bio Research plugin is missing from upstream archive")
            archive.extractall(extracted_dir, members=members, filter="data")

        extracted_plugin = extracted_dir / archive_root / "bio-research"
        _validate_plugin_source(extracted_plugin)

        publish_dir = temp_dir / "publish"
        shutil.copytree(extracted_plugin, publish_dir / "bio-research")
        (publish_dir / _ARCHIVE_CHECKSUM_FILE).write_text(
            f"{BIO_RESEARCH_ARCHIVE_SHA256}\n",
            encoding="ascii",
        )
        try:
            publish_dir.rename(revision_dir)
        except OSError:
            # Another concurrent trial may have populated this immutable revision.
            if not _is_valid_cached_plugin(revision_dir):
                raise

    if not _is_valid_cached_plugin(revision_dir):
        raise RuntimeError(f"Failed to populate Bio Research plugin cache: {revision_dir}")
    return plugin_dir


def _is_valid_cached_plugin(revision_dir: Path) -> bool:
    checksum_path = revision_dir / _ARCHIVE_CHECKSUM_FILE
    if not checksum_path.is_file():
        return False
    if checksum_path.read_text(encoding="ascii").strip() != BIO_RESEARCH_ARCHIVE_SHA256:
        return False
    try:
        _validate_plugin_source(revision_dir / "bio-research")
    except (FileNotFoundError, ValueError, json.JSONDecodeError):
        return False
    return True


def _download_archive(destination: Path) -> None:
    with (
        urlopen(BIO_RESEARCH_ARCHIVE_URL, timeout=60) as response,
        destination.open("wb") as output,
    ):
        shutil.copyfileobj(response, output)


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _validate_plugin_source(plugin_dir: Path) -> dict[str, Any]:
    if not plugin_dir.is_dir():
        raise FileNotFoundError(f"Bio Research plugin directory not found: {plugin_dir}")

    manifest_path = plugin_dir / _PLUGIN_MANIFEST_FILE
    mcp_path = plugin_dir / _MCP_CONFIG_FILE
    for required_path in (manifest_path, mcp_path):
        if not required_path.is_file():
            raise FileNotFoundError(f"Bio Research plugin file not found: {required_path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    mcp_config = json.loads(mcp_path.read_text(encoding="utf-8"))

    if manifest.get("name") != "bio-research":
        raise ValueError("Plugin manifest must identify the bio-research plugin")
    if manifest.get("version") != BIO_RESEARCH_PLUGIN_VERSION:
        raise ValueError(f"Unexpected Bio Research plugin version: {manifest.get('version')!r}")

    skills = sorted(path.parent.name for path in (plugin_dir / "skills").glob("*/SKILL.md"))
    if not skills:
        raise ValueError("Bio Research plugin does not contain any skills")

    raw_servers = mcp_config.get("mcpServers")
    if not isinstance(raw_servers, dict) or not raw_servers:
        raise ValueError("Bio Research plugin does not contain MCP server definitions")
    configured_servers = sorted(
        name
        for name, config in raw_servers.items()
        if name not in _UNCONFIGURED_MCP_SERVERS and isinstance(config, dict) and config.get("url")
    )

    return {
        "archive_sha256": BIO_RESEARCH_ARCHIVE_SHA256,
        "archive_url": BIO_RESEARCH_ARCHIVE_URL,
        "digest": compute_skill_digest(plugin_dir),
        "mcp_servers": configured_servers,
        "name": manifest["name"],
        "omitted_mcp_servers": sorted(_UNCONFIGURED_MCP_SERVERS),
        "repository": BIO_RESEARCH_PLUGIN_REPOSITORY,
        "revision": BIO_RESEARCH_PLUGIN_REVISION,
        "skills": skills,
        "version": manifest["version"],
    }
