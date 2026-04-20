# Pass 4 — Prefill Seam Benchmark

Honest numbers for the already-owned prefill orchestration path. Per
the Pass 4 target (`docs/executor_seam_pass4_target.md`), this is
**not** a stock-vs-owned comparison — the prefill seam has no stock
toggle (see `docs/executor_seam_pass4_assessment.md` §6).

## What was measured

With `self.model.__call__` replaced by a no-op spy, the wall time
inside `Scheduler._do_external_prefill` reflects only branch code:

- the chunk-sizing while loop (`omlx/scheduler.py:1309-1325`)
- the pre-chunk abort gate + counter hit inside
  `_run_owned_prefill_step` (`omlx/scheduler.py:1140-1192`)
- `mx.eval([c.state for c in prompt_cache])` against a tiny stub cache
- `_sync_and_clear_cache()` between chunks (`omlx/scheduler.py:1386`)

This is the **orchestration cost ceiling** the owned seam adds on top of
whatever the real forward would cost. Real forward time dominates any
realistic end-to-end run and is invariant to whether the sequencing loop
is stock or owned.

## Run configuration

- Harness: `scripts/bench_pass4_prefill.py`
- Results: `scripts/bench_results_pass4_prefill.json`
- Python: 3.9.7 (process python; the repo venv resolves to 3.10.12 but
  the benchmark ran via a system python on this machine — numbers are
  representative, not hardware-normalized).
- `mlx-lm` pin: `git+https://github.com/ml-explore/mlx-lm@dcbf6e33d135a1b7c6767ca0fe7ebbd23df814a7`
- Prompt length: 4096 tokens.
- Repeats per step size: 30.

## Results (prompt length = 4096 tokens)

| `prefill_step_size` | Chunks | Mean total (ms) | Mean per-chunk (µs) | Median (ms) |
| ---: | ---: | ---: | ---: | ---: |
| 256  | 16 | 2.464 | 154.0 | 2.353 |
| 512  |  8 | 1.193 | 149.1 | 1.148 |
| 1024 |  4 | 0.661 | 165.2 | — |
| 2048 |  2 | 0.368 | 183.9 | — |
| 4096 |  1 | 0.783 | 782.6 | — |

Raw numbers in `scripts/bench_results_pass4_prefill.json`.

## Observations

- **Chunk count scales exactly as the branch math predicts.** Every run
  reports `chunks == ceil((n_tokens - 1) / prefill_step_size)` or
  `chunks == floor((n_tokens - 1) / step) + (1 if remainder else 0)`,
  with `prefill_step_size=4096` giving a single chunk because the full
  prompt-minus-one fits in one step. The counter
  (`RuntimeMetricsRecorder._prefill_chunks_completed`) matches the spy
  `model.call_count` every time, validating that branch orchestration
  — not stock code — is deciding sequencing.

- **Per-chunk orchestration cost is ~150–180 µs** across chunk sizes
  256–2048 on this machine, dominated by the `mx.eval` +
  `_sync_and_clear_cache` pair rather than the Python overhead of
  `_run_owned_prefill_step`. The overhead is negligible relative to
  real forward time on any production-sized model (typically tens of
  milliseconds per chunk for mid-size models at step 2048).

- **Single-chunk run is slower per-chunk** because MLX stream warmup
  happens inside the one `mx.eval` — the final-snapshot `_sync_and_clear_cache`
  at `omlx/scheduler.py:1400` is amortized across many chunks when
  chunking is enabled. This is an artifact of the stub forward being
  cheaper than the sync call it chases.

- **No counter drift.** The benchmark asserts
  `runtime_metrics._prefill_chunks_completed` grows by exactly the
  number of chunks after each run. A failure here would indicate
  drift between the Pass 2 counter and the actual loop — none observed
  across 150 runs.

## Verdict

The branch-owned prefill orchestration seam is:

- **real** — `_do_external_prefill` is the sole driver; a mock forward
  confirms no stock sequencing path is exercised;
- **cheap** — ~150–180 µs per chunk orchestration overhead, dwarfed by
  real forward compute;
- **deterministic** — chunk count and per-chunk counters agree exactly
  across 30 repeats at each step size.

There is **no speed story to tell** from Pass 4, because Pass 4 did not
add a new seam. The value of the owned seam is authority + observability
+ determinism, all already in place from Pass 2.

## What still remains delegated

Per `docs/executor_seam_pass4_assessment.md` §8, the remaining
stock-owned code exercised once the real forward is restored:

- `mlx_lm.models.<arch>.Model.__call__` (full forward).
- Per-layer `KVCache.update_and_fetch` and siblings.
- RoPE position offset logic inside stock `Attention.__call__`.
- Attention mask construction (`create_attention_mask`,
  `create_causal_mask`).
- Hybrid SSM-state cache semantics.
- All decode compute via `BatchGenerator.next_generated()`.

Owning any of these requires per-architecture model forks, which is
out of scope for a seam-level pass (per the Pass 4 governing rule).

## Follow-on recommendation

See `docs/executor_seam_pass4_final.md`. Short version: stop
seam-by-seam runtime replacement.
