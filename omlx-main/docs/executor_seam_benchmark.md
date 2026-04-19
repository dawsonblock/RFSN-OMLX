# Executor Seam Benchmark

> This benchmark compares the legacy stock handoff against the new scheduler-owned seam.
>
> It does not claim a full runtime replacement or a general performance win.

## Workload

- model: mlx-community/Qwen2.5-0.5B-Instruct-4bit
- prompt size: about 1200 tokens
- decode budget: 12 tokens
- repetitions: 2
- same model, prompt budget, and decode budget on both paths

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
| cold | stock | 675.05 | 453.62 | 270.91 | 725.24 |
| cold | owned | 394.96 | 210.86 | 230.74 | 444.84 |
| restart_cache | stock | 276.73 | 64.01 | 261.23 | 331.11 |
| restart_cache | owned | 259.10 | 63.59 | 244.38 | 312.37 |
| restart_restore | stock | 259.99 | 62.87 | 244.20 | 313.30 |
| restart_restore | owned | 272.15 | 60.07 | 259.68 | 324.05 |

### Interpretation
- cold and restart-cache were lower on the owned seam in this repeated run
- restart-restore stayed close to neutral, with the owned seam slightly higher total latency
- the sample size is still modest, so this supports authority and non-regression, not a broad speed claim

---

## Correctness and non-regression

Fresh verification evidence after the stronger seam landed:
- 171 tests passed in 23.86s

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
