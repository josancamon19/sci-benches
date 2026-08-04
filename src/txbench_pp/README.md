# TxBench-PP Harbor adapter

This adapter exports the 12 evaluations listed by the public
`latchbio/txbench-pp` manifest at commit
`a4e0913d76c3dbc433ecf39903afbe7711a6a807`. Each generated Harbor task contains
the unchanged scientific prompt, except that its existing output contract is
rewritten to save the requested JSON object at `/app/result.json`.

Input files are downloaded on the host from each eval's released `latch://`
nodes. Set `LATCH_BIO_API_KEY` in `.env`; the credential is used only by the
adapter and is never copied into a task, image, prompt, or metadata file.

```bash
uv run txbench-pp-adapter --output-dir datasets/txbench-pp
```

The verifier uses the eval's published grader configuration with
`latch-eval-tools==0.4.18`, including fail-closed nested `all_of`, numeric tolerance,
label-set Jaccard, marker precision/recall, and multiple-choice graders. The
package is pinned to the public grader source revision
`bd91247ab62352970c720d03ac74ce032f0f50c9`.

Generated tasks run with no solver or verifier network access. Standard Harbor
Daytona provisions the image, while Docker work volumes at `/app/work`,
`/tmp/w`, and `/root/w` keep large intermediates outside Daytona's writable
image quota.
