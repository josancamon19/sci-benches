# GeneBench-Pro Harbor adapter

This repository converts the ten public [GeneBench-Pro case studies](https://huggingface.co/datasets/ajh-oai/genebench-pro-public-package/tree/main/problems) into the current [Harbor task format](https://www.harborframework.com/docs/datasets/adapters). It pins upstream commit `9bd2c54a6c0beef041e3504aa7eb65fc77783e18` and verifies every copied config and data file against the upstream manifest.

The generated agent workspace contains only the released `data_files/`; the public ground truth, reference grader, and public problem report are placed under Harbor's `tests/` directory, which Harbor uploads only after the agent phase. The adapter also extracts the report text for trajectory analysis. It preserves each scientific prompt, removes three redundant response-format lines, and replaces only the final response instruction with a request to save the JSON object at `/app/result.json`.

Each task image provides Python 3.13 with NumPy, pandas, SciPy, scikit-learn, statsmodels, lifelines, matplotlib, seaborn, PyArrow, pysnptools, bed-reader, and pysam. It also includes bedtools 2.31.1 and the official PLINK 2 Alpha 7.1 build dated 2026-05-04. Package versions and the PLINK archive checksum are pinned in the task Dockerfile.

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
```

Codex uses the local Codex login when `CODEX_FORCE_AUTH_JSON=YES` (the default in `job.yaml`). The checked-in job runs all ten tasks once with the oracle plus these three models:

- Claude Code with `anthropic/claude-opus-4-8`
- Codex with `openai/gpt-5.6-sol`
- OpenCode with Fireworks `openai/accounts/fireworks/models/glm-5p2`

The task and verifier policies are `no-network`. Harbor merges only each agent's model-provider hostname into the agent-phase allowlist; arbitrary internet access remains blocked, and the verifier stays fully offline. Agents receive a 3,600-second wall-clock timeout, which is an operational setting rather than a timeout specified by the paper. Transient setup `NetworkConnectionError` failures are retried twice, while scientific failures are not retried. The job does not set an agent turn limit, output-token limit, reasoning-effort override, or agent version pin.

```bash
uv run --env-file .env harbor run -c job.yaml -y -q
```

No job name is specified, so Harbor uses its default timestamp. Each trial retains the agent's `/app` workspace except the staged input data. The verifier grades `/app/result.json`; the downloaded workspace, verifier output, rewards, agent logs, and ATIF trajectory remain in the normal trial directory under `jobs/`.

The verified Daytona job `2026-07-31__16-30-32` completed all 40 trials (ten tasks across the oracle and three model agents) in 49 minutes with zero errors or retries. All 40 workspaces retained `result.json`, all 40 artifact manifests completed successfully, and all 30 model trials retained ATIF trajectories. Claude Opus 4.8, Codex GPT-5.6, and Fireworks GLM-5p2 each passed `wf_selection`.

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

## Local checks

```bash
uv run ruff check .
uv run pytest
uv run --env-file .env harbor run -c job.yaml -a oracle -y -q
```
