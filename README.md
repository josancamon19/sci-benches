# GeneBench-Pro Harbor adapter

This repository converts the ten public [GeneBench-Pro case studies](https://huggingface.co/datasets/ajh-oai/genebench-pro-public-package/tree/main/problems) into the current [Harbor task format](https://www.harborframework.com/docs/datasets/adapters). It pins upstream commit `9bd2c54a6c0beef041e3504aa7eb65fc77783e18` and verifies every copied config and data file against the upstream manifest.

The generated agent workspace contains only the released `data_files/`; the public ground truth, reference grader, and public problem report are placed under Harbor's `tests/` directory, which Harbor uploads only after the agent phase. The adapter also extracts the report text for trajectory analysis. It preserves each scientific prompt, removes three redundant response-format lines, and replaces only the final response instruction with a request to save the JSON object at `/app/result.json`.

Each task image mirrors the complete analysis environment named in the paper's Methods section. It provides Python 3.13 with the general scientific stack; PLINK 2.0, BEDTools, Tabix, pysam, cyvcf2, pybedtools, pyBigWig, pyfaidx, pysnptools, bed-reader, and PyRanges; Scanpy, AnnData, Scrublet, and PyDESeq2; and R 4.5 with Seurat, DESeq2, edgeR, limma, tximport, SingleCellExperiment, zellkonverter, and scDblFinder. Direct package versions and downloaded archive checksums are pinned in the task Dockerfile.

## Setup

Python 3.12+, `uv`, and a Daytona account are required.

```bash
uv sync --frozen
uv run genebench-pro-adapter --output-dir datasets/genebench-pro
```

The adapter also accepts `--limit`, `--overwrite`, and `--task-ids`, as required by Harbor's adapter contract. Use `--source-dir` to generate from an existing checkout instead of downloading from Hugging Face.

## Daytona run

Keep credentials in `.env` (it is gitignored):

```dotenv
DAYTONA_API_KEY=...
CLAUDE_CODE_OAUTH_TOKEN=...
FIREWORKS_API_KEY=...
HF_TOKEN=...
```

The checked-in `job.yaml` runs the BioMysteryBench plugin comparison described
below. Agents receive a 3,600-second wall-clock timeout. Transient setup
`NetworkConnectionError` failures are retried twice, while scientific failures
are not retried. The job does not set an agent turn limit, output-token limit,
reasoning-effort override, or agent version pin.

```bash
uv run --env-file .env harbor run -c job.yaml -y -q
```

No job name is specified, so Harbor uses its default timestamp. Each trial retains
the agent's `/app` workspace except the staged input data. The downloaded
workspace, verifier output, rewards, agent logs, and ATIF trajectory remain in the
normal trial directory under `jobs/`.

The earlier GeneBench-Pro Daytona job `2026-07-31__16-30-32` completed all 40
trials across the oracle, Claude Opus 4.8, Codex GPT-5.6, and Fireworks GLM-5p2
with zero errors or retries. That retained run is not the current `job.yaml`.

## Review analysis

[`rubrics/genebench-pro-review.toml`](rubrics/genebench-pro-review.toml) implements a benchmark-specific trajectory review derived from the external-review criteria and failure modes in the [GeneBench-Pro paper](https://cdn.openai.com/pdf/21938268-21af-442f-af93-3b2249afb241/genebench-pro.pdf). It checks target recoverability and identifiability, defensible alternate estimators, implementation parity, evidence consistency, QC and ablations, simulation realism and leakage, multistage fidelity, and prompt/grader alignment. The analyzer receives the solver instruction, hidden evaluation materials, extracted public report, reference grader, and trajectory.

Run it against a completed trial with:

```bash
uv run --env-file .env harbor analyze jobs/<job>/<trial> \
  --rubric rubrics/genebench-pro-review.toml \
  --env daytona \
  --agent claude-code \
  --model anthropic/claude-opus-4-8 \
  --agent-env 'CLAUDE_CODE_OAUTH_TOKEN=${CLAUDE_CODE_OAUTH_TOKEN}' \
  --agent-env CLAUDE_FORCE_OAUTH=YES \
  --n-concurrent 1 \
  --n-attempts 1 \
  --quiet
```

The retained analysis on `2026-07-31__16-30-32/wf_selection__2wtDMy9` completed with zero errors and passed all nine custom criteria. Its `analysis.json` is attached directly to the source trial for Harbor View.

## BioMysteryBench

The `biomystery-bench-adapter` command exports the gated 90-task
[`Anthropic/BioMysteryBench-full`](https://huggingface.co/datasets/Anthropic/BioMysteryBench-full)
v11 release. The full release is the default and is pinned to commit
`b5a889c4757214ec9a6ade876b734f920a7799db`. Accept the dataset's evaluation-only
terms and put the resulting Hugging Face access token in `.env` as `HF_TOKEN`.
The token is used only by the host-side adapter and is never written to generated
tasks or exposed to the solving agent.

Before downloading any task data, the adapter reads authenticated Hugging Face
file metadata and skips archives larger than 1 decimal GB. At the pinned release,
69 archives are eligible and 21 are excluded; the eligible archives still total
about 9.8 GB, so select task IDs rather than exporting all of them on a laptop.

```bash
uv run biomystery-bench-adapter --task-ids hb002 --overwrite
uv run --env-file .env harbor run -c job.yaml -y -q
```

The BioMystery image is based on Bioconductor 3.18 with R 4.3. It includes BWA,
Bowtie2, BLAST, samtools, bcftools, bedtools, seqtk, FastQC, Nextflow, Java 21,
Node.js, the stated R/Bioconductor packages, a conda Python 3.11 scientific
environment, and a uv-managed Python 3.12 environment. Agent network access is
restricted to the benchmark's package and bioinformatics-domain allowlist.

### Reusable Bio Research plugin harness

`shared.agents.bio_research_claude_code:BioResearchClaudeCode` is independent of
both dataset adapters and can be attached to any Harbor benchmark. The treatment
in `job.yaml` shows the required import path, MCP allowlist, and OAuth environment
for an Opus 4.8 run. It labels plugin trials separately and does not change task
files. The treatment uses the optional `instruction_prefix` argument to ask Claude
to inspect the plugin before solving the original task. This makes it a nudged
plugin treatment rather than a plugin-availability-only comparison.

The harness downloads `anthropics/knowledge-work-plugins` directly from upstream
at commit `ace462f156540bf75fb8ea9427d343a38c490015`, verifies the pinned archive
SHA-256, and caches only the Bio Research plugin under
`~/.cache/harbor/plugins`. No upstream plugin source is committed to this
repository. The harness stages the cached plugin in each trial, removes only the
upstream Benchling placeholder with an empty URL, and starts Claude Code with
`--plugin-dir`. Set `XDG_CACHE_HOME` to relocate the host cache, or pass
`plugin_cache_dir` in the treatment agent's `kwargs`.

The plugin's ten configured MCP endpoint hosts are added only to the treatment's
agent-phase allowlist. Five redundant FTP, documentation, and alternate package
mirrors are omitted from generated task allowlists so the merged treatment policy
fits Daytona's 20-domain limit while retaining the corresponding primary hosts.
The model's
`CLAUDE_CODE_OAUTH_TOKEN` does not authenticate those separate MCP services;
connector-specific OAuth state must be supplied independently when it becomes
available. Unauthenticated connectors may appear disconnected without preventing
the skills and remaining connectors from loading.

Harbor reports the treatment under `claude-code-bio-research`, so its official
task rewards, runtime, token use, and tool calls remain separate from the
`claude-code` control. Each treatment trial also retains
`/logs/artifacts/bio-research-plugin.json` with the plugin revision, digest,
skills, and MCP server names.

Set `mcp_healthcheck: true` in the treatment agent's `kwargs` to run Claude's MCP
health command inside the agent-phase Daytona network and retain
`/logs/artifacts/bio-research-mcp-health.txt`. A one-time Daytona diagnostic
confirmed that PubMed, bioRxiv, Consensus, Clinical Trials, ChEMBL, and Open
Targets connect; BioRender, Synapse, Wiley, and Owkin require separate
authentication. The unmodified retry made no plugin call. After enabling the
instruction prefix, the treatment invoked `bio-research:start`, loaded the
upstream skill successfully, and scored `1.0`. It made no MCP call because none
was relevant to the local genome-identification workflow.

The post-refactor Daytona job `2026-08-01__13-18-45` completed all four trials in
8 minutes 23 seconds with zero errors or retries. Control Opus and plugin Opus both
scored `1.0` on `hb002` and `hb010`, so the observed score lift was zero. Both
treatment artifacts confirm that the pinned plugin loaded, although neither
trajectory invoked a Bio Research skill or MCP tool. Across the two trials, the
control used 528 summed agent-seconds and reported $2.17 of model cost; the
treatment used 621 seconds and reported $2.32. This is a one-attempt smoke
comparison on two ceiling-scoring tasks, not an estimate of broader benchmark
impact.

Harbor 0.20 does not provide a credential-isolated Daytona setup hook that runs
before an agent without exposing its environment. Consequently, downloading
gated inputs inside the task sandbox would reveal `HF_TOKEN` to the solver. Large
archives should remain disabled until they can be staged through a provider secret
mount or a short-lived signed URL that is revoked before agent execution.

## Local checks

```bash
uv run ruff check .
uv run pytest
uv run --env-file .env harbor run -c job.yaml -y -q
```
