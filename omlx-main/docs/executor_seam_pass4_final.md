# Pass 4 — Final Recommendation

Outcome of the Pass 4 stop-and-report, cross-referenced against the task
spec's required outputs.

## 1. Exact previous stock prefill handoff point

Before Pass 2: stock `BatchGenerator` owned prompt ingestion and the
prefill loop via `insert(...)` receiving the full prompt; per-chunk
abort was detected only *after* stock had already spent the forward.

Before Pass 4 (i.e. after Passes 1–3): the single stock call remaining
in prefill is `self.model(chunk, cache=prompt_cache, **model_kwargs)`
at `omlx/scheduler.py:1186`, which enters
`mlx_lm.models.<arch>.Model.__call__` and drives the per-layer
`KVCache.update_and_fetch` mutations.

## 2. Exact new seam now owned after Pass 4

**None.** Pass 4 did not take a new seam. The assessment
(`docs/executor_seam_pass4_assessment.md`) established that every item
in the task's "real prefill ownership" checklist was already
branch-owned by Pass 2:

- prompt token ingestion — `Scheduler._do_external_prefill`
- prefill chunk sequencing — the while-loop in the same function
- cache-mutation boundary — allocation via `make_prompt_cache`,
  per-chunk `mx.eval`, boundary-aligned snapshots, `_sync_and_clear_cache`
- prefill-done decision — the function's return of
  `(cache, [tokens[-1]])` and the pre-filled handoff to
  `batch_generator.insert`
- local policy checks during prefill — the pre-chunk abort gate inside
  `_run_owned_prefill_step` plus per-chunk counters

What Pass 4 did do: formalize that authority with focused ownership
tests and an honest orchestration benchmark, and document the
per-architecture rewrite wall that lies beyond it.

## 3. Exact files changed / added in Pass 4

Added (all documentation, tests, and benchmarks — no `omlx/` source
file was modified):

- `docs/executor_seam_pass4_assessment.md`
- `docs/executor_seam_pass4_target.md`
- `docs/executor_seam_pass4_benchmark.md`
- `docs/executor_seam_pass4_final.md` (this document)
- `tests/test_prefill_seam_pass4.py`
- `scripts/bench_pass4_prefill.py`
- `scripts/bench_results_pass4_prefill.json`

No changes to `omlx/scheduler.py`, `omlx/request.py`,
`omlx/runtime_metrics.py`, or any other runtime source file.

## 4. New tests added

`tests/test_prefill_seam_pass4.py` — 13 tests covering:

- **P1 / P6** — `_do_external_prefill` returns `(cache, [tokens[-1]])`
  for every `n_tokens ∈ {2, 3, 17, 2048, 4099}`.
- **P3** — chunk sequencing matches branch math for
  `(step, n_tokens)` in `{(7, 22), (50, 101), (3, 11), (2048, 4097)}`.
- **P2** — a pre-registered abort prevents `self.model.__call__` from
  running and raises `_PrefillAbortedError`, with counters
  `aborted=+1`, `completed=+0`.
- **P5** — per-chunk counters match chunk count during a 14-token,
  step-4 run (four chunks, four completed-counter increments).
- **P7** — during `_do_external_prefill` no `BatchGenerator` method is
  invoked (spy `batch_generator` confirmed untouched).
- Trivial-path invariant — 1-token prompt returns `(cache, tokens)`
  without touching the model.

All 13 pass under `pytest -x` in the repo venv.

## 5. Benchmark results

From `docs/executor_seam_pass4_benchmark.md` (prompt length 4096,
30 repeats per step):

| `prefill_step_size` | Chunks | Mean total | Mean per-chunk |
| ---: | ---: | ---: | ---: |
| 256  | 16 | 2.464 ms | 154 µs |
| 512  |  8 | 1.193 ms | 149 µs |
| 1024 |  4 | 0.661 ms | 165 µs |
| 2048 |  2 | 0.368 ms | 184 µs |
| 4096 |  1 | 0.783 ms | 783 µs |

Orchestration overhead of the owned seam is ~150–180 µs per chunk on
this machine, which is negligible relative to real per-chunk forward
compute. No stock-vs-owned speed claim is made, because no such
toggle exists on the prefill path.

## 6. What still remains delegated

- `mlx_lm.models.<arch>.Model.__call__` (full forward, invoked at
  `omlx/scheduler.py:1186`).
- Per-layer `KVCache.update_and_fetch`,
  `RotatingKVCache.update_and_fetch`,
  `QuantizedKVCache.update_and_fetch`,
  `TurboQuantKVCache.update_and_fetch`.
- RoPE position offset logic inside stock `Attention.__call__`.
- Attention mask construction (`create_attention_mask`,
  `create_causal_mask`).
- Hybrid SSM-state cache semantics for qwen3_5-family and relatives.
- Decode compute via `BatchGenerator.next_generated()`.

## 7. Did Pass 4 materially advance runtime replacement?

**No.** Pass 4 did not transfer any new authority from stock code to
branch code. It verified that the authority taken in Pass 2 is real
and measured its overhead honestly.

To materially advance further, the next step would be to replace or
intercept the per-layer forward, which requires a per-architecture
rewrite (llama, qwen2, qwen3, gemma, mixtral, deepseek_v32, qwen3_5
hybrid SSM, VLMs). That is not a scheduler seam; it is a fork of
model code. The task explicitly instructed to stop and report rather
than pursue such a rewrite.

## 8. Final recommendation

**Stop seam-by-seam runtime replacement.**

Keep the branch as:

> workspace-lineage tool + runtime observability + limited executor
> control (scheduler decode entry, local abort, length-cap finish
> override; branch-owned prefill orchestration with pre-chunk abort
> and per-chunk counters; branch-owned EOS stop detection with local
> finish-reason override).

Rationale:

1. Every seam cleanly extractable at the scheduler layer has been
   extracted (Passes 1–3) and is now formally verified (Pass 4).
2. The next boundary — per-layer forward + `update_and_fetch` — is a
   fused stock API with no Python-level interception point. Crossing
   it would mean a per-architecture rewrite that this project has
   explicitly said it will not take on.
3. The current authority set is sufficient for the stated goals:
   deterministic lifecycle control, observable prefill/decode
   counters, and a pre-compute abort path. Extra seams beyond this
   would be decorative.
4. Benchmark evidence (`docs/executor_seam_pass4_benchmark.md`) shows
   the owned orchestration layer is cheap, so there is no cost
   argument for reverting any seam either.

The branch has reached a stable state for its declared scope. Future
work should target workspace-lineage hardening or observability — not
more executor seams.
