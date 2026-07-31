# GeneBench-Pro Harbor adapter

This repository converts the ten public [GeneBench-Pro case studies](https://huggingface.co/datasets/ajh-oai/genebench-pro-public-package/tree/main/problems) into the current [Harbor task format](https://www.harborframework.com/docs/datasets/adapters). It pins upstream commit `9bd2c54a6c0beef041e3504aa7eb65fc77783e18` and verifies every copied config and data file against the upstream manifest.

The generated agent workspace contains only the released `data_files/`; the public ground truth and reference grader are placed under Harbor's `tests/` directory, which Harbor uploads only after the agent phase. The adapter preserves each scientific prompt, removes three redundant response-format lines, and replaces only the final response instruction with a request to save the JSON object at `/app/result.json`.

Each task image provides Python 3.13 with NumPy, pandas, SciPy, scikit-learn, statsmodels, lifelines, matplotlib, seaborn, PyArrow, pysnptools, bed-reader, and pysam. It also includes bedtools 2.31.1 and the official PLINK 2 Alpha 7.1 build dated 2026-05-04. Package versions and the PLINK archive checksum are pinned in the task Dockerfile.

## Setup

Python 3.12+, `uv`, and a Daytona account are required.

```bash
uv sync --frozen
uv run genebench-pro-adapter --output-dir datasets/genebench-pro
```

The adapter also accepts `--limit`, `--overwrite`, and `--task-ids`, as required by Harbor's adapter contract. Use `--source-dir` to generate from an existing checkout instead of downloading from Hugging Face.

## Daytona smoke run

Keep credentials in `.env` (it is gitignored):

```dotenv
DAYTONA_API_KEY=...
CLAUDE_CODE_OAUTH_TOKEN=...
```

`job.yaml` selects only `wf_selection`, so one attempt creates exactly one Claude Code trial using `anthropic/claude-opus-4-8`. It does not set an agent turn limit, output-token limit, reasoning-effort override, or Claude Code version pin.

```bash
uv run --env-file .env harbor run -c job.yaml
```

Each trial retains the agent's `/app` workspace except the staged input data. The verifier grades `/app/result.json`; the downloaded workspace, verifier output, rewards, Claude Code logs, and ATIF trajectory remain in the normal trial directory under `jobs/`.

The Daytona smoke run `genebench-pro-opus-4-8-daytona-20260731-minimal` completed one `wf_selection` trial with zero errors and reward `1.0`. Its collected workspace includes `result.json` and the analysis scripts produced by the agent.

## Local checks

```bash
uv run ruff check .
uv run pytest
uv run --env-file .env harbor run -c job.yaml -a oracle
```
