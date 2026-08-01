from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any, override

from harbor.agents.installed.base import CliFlag
from harbor.agents.installed.claude_code import ClaudeCode
from harbor.environments.base import BaseEnvironment
from harbor.skills import compute_skill_digest

BIO_RESEARCH_PLUGIN_REVISION = "ace462f156540bf75fb8ea9427d343a38c490015"
BIO_RESEARCH_PLUGIN_VERSION = "1.2.0"
BIO_RESEARCH_REMOTE_DIR = "/harbor/plugins/bio-research"
BIO_RESEARCH_ARTIFACT_PATH = "/logs/artifacts/bio-research-plugin.json"

_UPSTREAM_METADATA_FILE = "UPSTREAM.json"
_PLUGIN_MANIFEST_FILE = ".claude-plugin/plugin.json"
_MCP_CONFIG_FILE = ".mcp.json"
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
        plugin_source_dir: str | Path,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        self.plugin_source_dir = Path(plugin_source_dir).expanduser().resolve()
        self.plugin_artifact = _validate_plugin_source(self.plugin_source_dir)
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


def _validate_plugin_source(plugin_dir: Path) -> dict[str, Any]:
    if not plugin_dir.is_dir():
        raise FileNotFoundError(f"Bio Research plugin directory not found: {plugin_dir}")

    manifest_path = plugin_dir / _PLUGIN_MANIFEST_FILE
    mcp_path = plugin_dir / _MCP_CONFIG_FILE
    metadata_path = plugin_dir.parent / _UPSTREAM_METADATA_FILE
    for required_path in (manifest_path, mcp_path, metadata_path):
        if not required_path.is_file():
            raise FileNotFoundError(f"Bio Research plugin file not found: {required_path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    mcp_config = json.loads(mcp_path.read_text(encoding="utf-8"))
    upstream = json.loads(metadata_path.read_text(encoding="utf-8"))

    if manifest.get("name") != "bio-research":
        raise ValueError("Plugin manifest must identify the bio-research plugin")
    if manifest.get("version") != BIO_RESEARCH_PLUGIN_VERSION:
        raise ValueError(
            "Unexpected Bio Research plugin version: "
            f"{manifest.get('version')!r}"
        )
    if upstream.get("commit") != BIO_RESEARCH_PLUGIN_REVISION:
        raise ValueError(
            "Unexpected Bio Research plugin revision: "
            f"{upstream.get('commit')!r}"
        )

    skills = sorted(
        path.parent.name for path in (plugin_dir / "skills").glob("*/SKILL.md")
    )
    if not skills:
        raise ValueError("Bio Research plugin does not contain any skills")

    raw_servers = mcp_config.get("mcpServers")
    if not isinstance(raw_servers, dict) or not raw_servers:
        raise ValueError("Bio Research plugin does not contain MCP server definitions")
    configured_servers = sorted(
        name
        for name, config in raw_servers.items()
        if name not in _UNCONFIGURED_MCP_SERVERS
        and isinstance(config, dict)
        and config.get("url")
    )

    return {
        "digest": compute_skill_digest(plugin_dir),
        "mcp_servers": configured_servers,
        "name": manifest["name"],
        "omitted_mcp_servers": sorted(_UNCONFIGURED_MCP_SERVERS),
        "repository": upstream["repository"],
        "revision": upstream["commit"],
        "skills": skills,
        "version": manifest["version"],
    }
