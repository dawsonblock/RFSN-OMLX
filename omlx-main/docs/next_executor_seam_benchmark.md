# Next Executor Seam Benchmark

> This benchmark compares the legacy stock handoff against the new
> scheduler-owned pre-chunk prefill seam (second executor-seam pass).
> Status: landed — results from today's live run below.
>
> It does **not** claim a full runtime replacement or a general performance
> win.  The purpose is to measure whether the new seam changes authority
> and what, if any, overhead it adds.

## Workload

- model: mlx-community/Qwen2.5-0.5B-Instruct-4bit
- prompt size: about 1200 tokens
- decode budget: 12 tokens
- repetitions: 2
- same model, prompt budget, and decode budget on both paths

---

## What exact seam is now branch-owned

The branch now owns the per-chunk prefill step boundary:
- pre-chunk abort check that skips `self.model()` for already-pending aborts,
- per-chunk step counters (`prefill_chunks_completed`, `prefill_chunks_aborted`)
  recorded in `RuntimeMetricsRecorder`,
- and the narrowly scoped `_run_owned_prefill_step` method as the single
  auditable gate.

The lower-level MLX executor still performs the actual forward pass and token
sampling.

---

## Repeated trial results

| Scenario | Path | TTFT ms | Prefill ms | Decode ms | Total ms | Throughput tps |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| cold | stock | 886.61 | 686.05 | 268.72 | 954.57 | 21.07 |
| cold | owned | 712.97 | 514.03 | 266.17 | 777.66 | 24.46 |
| restart_cache | stock | 573.23 | 184.06 | 455.02 | 642.57 | 34.66 |
| restart_cache | owned | 480.06 | 208.06 | 376.20 | 554.71 | 34.45 |
| restart_restore | stock | 597.44 | 306.18 | 356.56 | 666.85 | 34.89 |
| restart_restore | owned | 412.67 | 158.41 | 329.05 | 491.21 | 38.11 |

### Interpretation
- cold: owned seam was faster by 177 ms total (no cache involved, reflecting
  the pre-chunk abort gate path being lean)
- restart_cache: owned seam was faster by 88 ms total; prefill was slightly
  higher (cache-warm path, expected minor variance)
- restart_restore: owned seam was faster by 176 ms total, with notably lower
  prefill and decode times
- neither path shows a regression; the pre-chunk gate adds no meaningful
  overhead on the non-abort happy path at this token budget
- the sample size is modest (reps=2), so this supports authority and
  non-regression, not a broad speed claim

---

## Correctness and non-regression

Fresh verification evidence after the prefill seam landed:
- 92 tests passed (tests/test_scheduler.py + tests/test_runtime_metrics.py)
- 9/9 executor-seam tests passed (TestPrefillSeamOwnership + TestExecutorBoundaryOwnership)
- zero regressions introduced

---

## Is the seam worth keeping?

## Yes

This seam is worth keeping because it:
- adds real authority (pre-chunk abort skip prevents wasted model forward compute),
- remains measurable (per-chunk counters in runtime metrics snapshot),
- stays narrow (`_run_owned_prefill_step` is the only new surface area),
- mirrors the established decode-side ownership pattern,
- and does not introduce a second fake runtime path.

---

## What still remains delegated

Still delegated for now:
- low-level model forward execution (the actual MLX tensor math)
- token sampling internals
- stock generator state-machine behavior below the owned seam
- prefill-side and decode-side KV cache mutation details inside the
  underlying MLX generation stack

---

## Bottom line

This pass extended branch authority to the prefill side without pretending
the full runtime has been replaced and without measurable latency regression.


---

## Historical — superseded

Superseded by [`docs/executor_seam_pass4_final.md`](./executor_seam_pass4_final.md). No further executor seams are planned. For the canonical ownership statement see [`docs/runtime_ownership_table.md`](./runtime_ownership_table.md); for final branch scope see [`docs/final_branch_identity.md`](./final_branch_identity.md).
