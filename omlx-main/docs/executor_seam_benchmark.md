# Executor Seam Benchmark

> This benchmark compares the legacy stock handoff against the new scheduler-owned seam.
>
> It does not claim a full runtime replacement or a general performance win.

## Workload

- model: mlx-community/Qwen2.5-0.5B-Instruct-4bit
- prompt size: about 1200 tokens
- decode budget: 12 tokens
- repetitions: 2

---

## What exact seam is now branch-owned

The branch now owns the scheduler-side decode-step boundary:
- decode-step entry,
- cancellation suppression before emission,
- local finish normalization,
- and executor-seam accounting.

The lower-level MLX executor still performs the actual forward pass and token sampling.

---

## Repeated trial results

| Scenario | Path | TTFT ms | Prefill ms | Decode ms | Total ms |
| --- | --- | ---: | ---: | ---: | ---: |
| cold | stock | 632.63 | 404.91 | 271.80 | 677.60 |
| cold | owned | 423.18 | 224.93 | 239.44 | 468.98 |
| restart_cache | stock | 397.59 | 101.22 | 350.63 | 454.16 |
| restart_cache | owned | 376.64 | 112.51 | 329.14 | 432.73 |
| restart_restore | stock | 346.70 | 71.24 | 324.81 | 402.06 |
| restart_restore | owned | 334.72 | 65.55 | 337.20 | 409.06 |

### Interpretation
- cold and restart-cache were slightly better in the owned seam run
- restart-restore was roughly neutral, with the owned seam slightly higher total latency
- the sample size is still modest, so this supports authority and non-regression, not a broad speed claim

---

## Correctness and non-regression

Fresh verification evidence after the seam landed:
- 169 tests passed in 23.08s

This covered scheduler behavior, runtime metrics, workspace lineage, and archive hardening flows.

---

## Is the seam worth keeping?

## Yes

This seam is worth keeping because it:
- adds real authority,
- remains measurable,
- stays narrow,
- and does not introduce a second fake runtime path.

---

## What still remains delegated

Still delegated for now:
- low-level model forward execution
- token sampling internals
- stock generator state-machine behavior below the owned seam
- decode-side cache mutation details inside the underlying MLX generation stack

---

## Bottom line

This pass materially improved runtime authority without pretending the full runtime has been replaced.
