# EpiBench Harbor Adapter

This adapter ports the seven public example evaluations from
[`latchbio/epibench`](https://github.com/latchbio/epibench) into runnable Harbor
tasks. The paper describes 106 evaluations, but the other 99 task definitions and
their data have not been released.

## Grader provenance

LatchBio has publicly released the reusable verifier engine in
[`latchbio/latch-eval-tools`](https://github.com/latchbio/latch-eval-tools). It
contains implementations such as `numeric_tolerance`, `numeric_range`,
`multiple_choice`, and `all_of`. It does **not** contain the EpiBench task IDs or
their task-specific grader configs, ground truths, reference intervals, and
tolerances. The seven public EpiBench `eval.json` files likewise omit both the
`grader` and `graders` fields.

The fallback grader in this adapter is therefore **not an official or
bit-for-bit reproduction** of the private EpiBench grading configuration. The
missing task-specific configuration, rather than the generic grader machinery,
is the unreleased component.

Fallback references are reconstructed by joining:

- final JSON objects in the public representative trajectories; and
- the pass/fail rows in the public `paper-supporting-info` results branch.

Where more than one materially different public answer was marked passing, the
grader retains multiple accepted variants. Task-specific numeric tolerances are
declared in `fallback_references.json`; they are compatibility choices, not
claims about LatchBio's private thresholds.

One compatibility exception is labeled directly in that file. For
`zebrafish_014g_h2az_bivalent_promoters`, interpretation options 3 and 7 both
describe the measured enrichment at active and bivalent promoters. The fallback
accepts either while retaining the public numeric checks; the private grader
accepted only option 7.

## Export

The eval definitions come from pinned GitHub commit
`6bc01f15125f5a01ab78ce1fd2fc2c51c4ca04f3`. Input files are Latch Data nodes,
so exporting requires a Latch SDK token:

```dotenv
LATCH_BIO_API_KEY=...
```

Generate one task:

```bash
uv run epibench-adapter \
  --task-ids gse149608_c3_enhancer_methylation \
  --output-dir datasets/epibench
```

The adapter runs the pinned Latch 2.76.10 CLI through an isolated `uvx`
environment, authenticates through a temporary CLI home, stages the data into
the generated Docker build context, and caches the downloaded node set. Latch is
isolated because its SDK dependency range conflicts with Harbor's Daytona client.
The token is not copied into the task. Generated agent phases use public network
access, matching the released trajectories' use of resources such as Ensembl and
UCSC; verifier phases remain in `no-network` mode.

Some tasks are large: `atac_1a` contains 48 nodes and `chipseq_K2` contains 443.
Use `--task-ids` while developing and set `--data-cache-dir` to a volume with
enough free space.

Daytona runs use Harbor's standard environment:

```yaml
environment:
  type: daytona
  force_build: false
  delete: true
```

The generated Docker image declares ephemeral work volumes at `/app/work`,
`/tmp/w`, and `/root/w`. Daytona mounts those paths outside its quota-limited
writable image layer, which gives storage-heavy ATAC-seq workflows room for
insertion BEDs and peak-calling intermediates without a custom Harbor
environment class.

The public representative EpiBench trajectories report 32 CPUs and about 123
GiB usable RAM. This Daytona account permits at most 4 CPUs, 8 GiB RAM, and 10
GiB disk per sandbox, so generated tasks request those account maxima. Agents
must stream or chunk the largest matrices rather than loading every column at
once.
