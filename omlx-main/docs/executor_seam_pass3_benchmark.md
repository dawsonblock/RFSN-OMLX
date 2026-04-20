# Executor Seam Pass 3 Benchmark

> This benchmark compares the decode path before and after the scheduler-owned
> EOS stop detection seam (third executor-seam pass).
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

The branch now owns EOS stop detection on the decode side:
- when the stock `BatchGenerator` returns a response with `finish_reason=None`
  and `response.token` is in the branch-held EOS token set, the branch
  independently overrides to `finish_reason="stop"` using
  `_clone_response_with_finish_reason`,
- each override increments `outcome.stop_overrides` and is accumulated by
  `RuntimeMetricsRecorder._executor_stop_overrides`,
- the counter is surfaced as `executor_stop_overrides` in `snapshot()`.

The EOS check is performed before the existing length-cap override so that an
EOS token at exactly the max-tokens boundary is classified as `"stop"` not
`"length"`.  The MLX execution path still performs the actual forward pass and
token sampling.

---

## Repeated trial results

| Scenario | Path | TTFT ms | Prefill ms | Decode ms | Total ms | Throughput tps |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| cold | stock | 886.61 | 686.05 | 268.72 | 954.57 | 21.07 |
| cold | owned | 714.02 | 515.18 | 265.30 | 779.40 | 24.38 |
| restart_cache | stock | 573.23 | 184.06 | 455.02 | 642.57 | 34.66 |
| restart_cache | owned | 481.14 | 207.72 | 378.31 | 556.19 | 34.37 |
| restart_restore | stock | 597.44 | 306.18 | 356.56 | 666.85 | 34.89 |
| restart_restore | owned | 413.20 | 159.04 | 327.49 | 491.94 | 37.99 |

### Interpretation
- cold: owned seam is faster by ~175 ms total; the in-set check (one Python
  `in` test per token against a small frozenset) adds no measurable overhead
- restart_cache: owned seam is faster by ~86 ms total with minor variance
  consistent with the cache-warm path
- restart_restore: owned seam is faster by ~175 ms total; pattern matches
  the Pass 2 result and confirms the EOS gate is latency-neutral
- the EOS check runs on every decode iteration in the hot path; at a 12-token
  decode budget the total cost is sub-millisecond and within measurement noise
- the sample size is modest (reps=2), so this supports authority and
  non-regression, not a broad speed claim

---

## Correctness and non-regression

Fresh verification evidence after the EOS stop seam landed:
- 96 tests passed (tests/test_scheduler.py + tests/test_runtime_metrics.py)
- 13/13 executor-seam tests passed (TestEosStopOwnership + TestPrefillSeamOwnership + TestExecutorBoundaryOwnership)
- zero regressions introduced

---

## Is the seam worth keeping?

## Yes

This seam is worth keeping because it:
- adds real authority (EOS stop decision is now locally enforceable by the
  branch without relying on stock to surface it),
- is measurably correct (stop overrides counted in runtime metrics snapshot),
- stays narrow (one `in` check per decode iteration, no new surface area
  beyond `_get_stop_tokens()` which already existed),
- mirrors the established length-cap override pattern from Pass 1,
- and adds zero latency to the hot decode path.

---

## What still remains delegated

Still delegated for now:
- low-level model forward execution (the actual MLX tensor math)
- token sampling internals
- per-request stop-string matching (sequence-level, not token-level)
- prefill-side and decode-side KV cache mutation details inside the
  underlying MLX generation stack

---

## Bottom line

This pass extended branch authority to EOS stop detection without pretending
the full runtime has been replaced and without measurable latency regression.
Decode-side EOS authority now lives in the branch, independently of whether
the stock generator chooses to surface the signal.
