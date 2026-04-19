# Executor Boundary Benchmark

> This benchmark measures the legacy stock handoff against the new scheduler-owned executor seam.
>
> It does **not** claim a full runtime replacement.

## Command used

```bash
.venv/bin/python scripts/bench_runtime_replacement.py \
  --model mlx-community/Qwen2.5-0.5B-Instruct-4bit \
  --prompt-tokens 1200 \
  --max-tokens 12 \
  --reps 2
```

## What is truly owned now

The branch now owns:
- decode-step entry under scheduler control,
- cancellation suppression before emission,
- local finish normalization at the request boundary,
- and executor-boundary accounting in runtime metrics.

The branch still does **not** own low-level model forward execution or token sampling.

---

## Measured results

### Scenario summary

| Scenario | Path | TTFT ms | Prefill ms | Decode ms | Total ms | Notes |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| cold | stock | 632.63 | 404.91 | 271.80 | 677.60 | legacy direct handoff |
| cold | owned | 423.18 | 224.93 | 239.44 | 468.98 | owned seam, same low-level executor |
| restart_cache | stock | 397.59 | 101.22 | 350.63 | 454.16 | cache hit |
| restart_cache | owned | 376.64 | 112.51 | 329.14 | 432.73 | cache hit |
| restart_restore | stock | 346.70 | 71.24 | 324.81 | 402.06 | restore succeeded |
| restart_restore | owned | 334.72 | 65.55 | 337.20 | 409.06 | restore succeeded |

### Executor-boundary counters

On this workload:
- `executor_steps_last = 12` on both paths
- `executor_finish_overrides_last = 0`
- `executor_cancel_suppressed_last = 0`

That is expected here: the benchmark prompt does not intentionally trigger local cancellation or budget-normalization edge cases.

Those ownership behaviors were instead verified by focused scheduler tests.

---

## Correctness / non-regression evidence

Fresh verification after the change:
- `169 passed in 23.08s`

That regression slice covered:
- scheduler behavior,
- runtime metrics,
- workspace lineage,
- session archive failure modes,
- lineage integrity,
- hardening rules,
- and session archive store behavior.

---

## Interpretation

### What changed measurably
- the project now owns a real executor seam instead of only observing the runtime
- the owned path remained non-regressive across repeated trials
- cold and restart-cache were somewhat lower wall-clock wise, while restart-restore was roughly neutral
- the sample is still modest, so this is evidence of authority and correctness, not a broad speed claim

### What did not change
- the low-level model step still delegates to the stock MLX generator path
- this is still not a full replacement runtime

---

## Keep or remove this seam?

## Recommendation: keep it

This seam is worth keeping because it adds real authority with measured non-regression and without widening UI or public API surface.

The honest next step is to continue seam by seam only if each new slice remains measurable, correct, and clearly more authoritative than the last.
