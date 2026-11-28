# Local synthetic Ollama smoke record — 2026-11-28

The user continued with the previously announced local synthetic trial after PR #8
was merged. This record distinguishes observed outcomes from broader model quality.
No model was downloaded, no candidate profile was loaded or transmitted, and no
existing application database was used. Each invocation made one provider attempt,
with no automatic retries. Failed outputs remained rejected.

## Environment and input

- Windows, project CPython 3.12.14; Ollama CLI reported 0.40.19.
- Intel Core Ultra 9 275HX, 24 logical processors, approximately 31.4 GiB RAM.
- NVIDIA GeForce RTX 5070 Ti Laptop GPU, 12227 MiB total memory reported by nvidia-smi.
- Installed models: `qwen3:8b` (displayed ID prefix `500a1f067a9f`) and
  `qwen2.5-coder:7b` (prefix `dae161e27b0e`). These are observed inventory prefixes,
  not full model-digest pinning.
- Fixed fixture: [smoke-posting.json](../tests/fixtures/extraction/smoke-posting.json),
  with title `Research Engineer`, location null and description `Python required.`.
  No compensation, dates, experience, rights or seniority are disclosed.
- Context 4096, generated-token request cap 1024, temperature 0, seed 0,
  native JSON schema steering, `think: false`, `keep_alive: 0`.
- The existing provider checks local completion-model metadata before sending job
  text, uses only the two allowed loopback routes and disables tracing. The daemon's
  global cloud-disable setting was not independently audited by this smoke check.

## Observed attempts

| Model | Prompt | Deadline | Result | Total duration | Grounded facts | SQLite unchanged |
| --- | --- | --- | --- | --- | --- | --- |
| qwen3:8b | job-extraction-v1 | 90 s | timeout | 90.08 s | 0 accepted | yes |
| qwen2.5-coder:7b | job-extraction-v1 | 120 s | ungrounded_evidence | 32.88 s | 0 accepted | yes |
| qwen2.5-coder:7b | job-extraction-v2 | 120 s | complete, smoke passed | 13.47 s | 1 | yes |

The second attempt returned HTTP 200 for both preflight and chat, but at least one
quote/field/span did not match the source. The whole response was discarded. The
precise rejected quote from that attempt was not retained; do not infer its exact
error. Ollama reported that model at 100% GPU placement during that attempt.

Prompt v2 supplies bounded, deterministic position hints derived from source text.
It still rejects wrong quotations, fields and offsets; it does not repair output.
In the successful attempt, preflight reached headers in approximately 0.053 s and
chat in 12.50 s. All extraction fields other than the two disclosed requirement/skill
categories remained null. At least one nonempty grounded fact was required to pass;
an all-null extraction would fail this check.

Successful input SHA-256:
`7beb1fca13700d335e6c6944cf3f3c6de95e604ce9dc1f2e07ab35c30226932c`.
Source revision fingerprint:
`599e68f41fa772b7a3950a13d24213ca1629bb61eb82d84a06a5552e12bfbd01`.
Local runtime summaries and the synthetic diagnostic response remain under ignored
`data/live-smoke-20261128/`; raw runtime payloads are not committed.

## Reproduce explicitly

Use a trusted local Ollama server and an already installed compatible model. From
the repository root, run:

```text
uv run --locked --offline --extra ollama python scripts/smoke_ollama.py --live --model qwen2.5-coder:7b --timeout 120
```

The command emits safe route-start events to stderr and one JSON summary to stdout.
It creates and removes its own temporary SQLite database. It never loads private
settings or an existing database. Without `--live` it refuses to run inference.
The maximum selectable timeout is 120 seconds. Cooperative client timeout does not
prove the daemon immediately stopped computing; server-side resource behavior is
outside that guarantee. CI runs offline script/provider tests and help only.

This is one successful short synthetic case after two failed trials. It is not a
held-out evaluation, a model comparison or evidence of reliable extraction on long
real-world postings. Cache/warm-up, prompt changes and output variation can affect
timing; the table does not establish a causal speed improvement. qwen3:8b remains
unvalidated for this workflow. Larger descriptions, recall, semantic interpretation,
context coverage and repeated-run reliability remain to be evaluated separately.
