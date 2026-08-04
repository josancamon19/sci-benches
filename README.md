# Bioinformatics benchmarks

This repository packages released bioinformatics benchmarks as self-contained
[Harbor](https://harborframework.com/) tasks that run locally or on Daytona. It
contains dataset adapters, pinned task environments, verifier templates, a
reusable Claude Code agent with Anthropic's Bio Research plugin, and a shared
Daytona job configuration. Generated datasets and job results stay local under
`datasets/` and `jobs/`.

## Included benchmarks

| Adapter | Upstream dataset | Scope and grading |
| --- | --- | --- |
| `genebench-pro-adapter` | [GeneBench-Pro public package](https://huggingface.co/datasets/ajh-oai/genebench-pro-public-package) | All 10 released problems, isolated inputs, deterministic reference verifiers, and the scientific Python, R, and genomics toolchain described by the benchmark. |
| `biomystery-bench-adapter` | [Anthropic BioMysteryBench-full](https://huggingface.co/datasets/Anthropic/BioMysteryBench-full) | The gated 90-task release with official answer rubrics and the benchmark anti-cheating rule. Archives larger than 1 GB are skipped by default. |
| `compbiobench-adapter` | [Genentech CompBioBench v1](https://huggingface.co/datasets/Genentech/compbiobench-data-v1) | All 100 public prompts and input files. Official answers are not released, so these tasks capture answers but intentionally do not claim an official benchmark score. |
| `epibench-adapter` | [LatchBio EpiBench](https://github.com/latchbio/epibench) | All seven public evaluations with authenticated Latch inputs and reconstructed public-reference verifiers. |
| `txbench-pp-adapter` | [LatchBio TxBench-PP](https://github.com/latchbio/txbench-pp) | All 12 manifest-listed preclinical pharmacology evaluations with authenticated Latch inputs and the published `latch-eval-tools` graders. |

Adapters pin their upstream revisions and stage downloaded inputs on the host.
Credentials are not copied into generated task images or exposed to solving
agents. Solver inputs and verifier-only references remain separated by Harbor's
task layout.

## Setup

Python 3.12+, [`uv`](https://docs.astral.sh/uv/), and a Daytona account are
required for the checked-in job.

```bash
uv sync --frozen
```

Keep credentials in a gitignored `.env` file:

```dotenv
DAYTONA_API_KEY=...
CLAUDE_CODE_OAUTH_TOKEN=...
HF_TOKEN=... # required only for gated BioMysteryBench exports
LATCH_BIO_API_KEY=... # required for EpiBench and TxBench-PP exports
```

## Export tasks

```bash
# All 10 GeneBench-Pro tasks
uv run genebench-pro-adapter --output-dir datasets/genebench-pro

# Two BioMysteryBench tasks
uv run biomystery-bench-adapter --task-ids hb002 hb010 --overwrite

# One CompBioBench task
uv run compbiobench-adapter --task-ids bam-infer-read-length-q1 --overwrite

# All public EpiBench tasks
uv run epibench-adapter --output-dir datasets/epibench

# All public TxBench-PP tasks
uv run txbench-pp-adapter --output-dir datasets/txbench-pp
```

Each adapter also supports `--limit`, `--task-ids`, `--overwrite`, and
`--source-dir`. Run `<adapter> --help` for dataset-specific options.

## Run the benchmark

The current [`job.yaml`](job.yaml) runs the exported EpiBench dataset once on
Daytona with plain Claude Code, Bio Research Claude Code, Codex, and OpenCode
agents. It uses the default timestamped job name and retains solver outputs,
workspace artifacts, verifier output, rewards, logs, and trajectories under
`jobs/`.

```bash
uv run --env-file .env harbor run -c job.yaml -y -q
```

The reusable treatment is implemented in
[`src/shared/agents/bio_research_claude_code.py`](src/shared/agents/bio_research_claude_code.py).
Its plugin revision and archive checksum are pinned, and each treatment trial
writes an audit manifest to `/logs/artifacts/bio-research-plugin.json`.

## Validate

```bash
uv run ruff check .
uv run pytest
```
