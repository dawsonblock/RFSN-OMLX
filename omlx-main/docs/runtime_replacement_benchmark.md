# Runtime Replacement Benchmark

> This benchmark compares **stock oMLX** against the branch’s **first runtime-owned slice**.
>
> It does **not** claim a new faster runtime. The purpose is to measure whether the branch is replacing anything meaningful yet.

## Workload

- **Model:** `mlx-community/Qwen2.5-0.5B-Instruct-4bit`
- **Prompt:** ~2000 tokens
- **Decode:** 16 tokens
- **Runs:** 2 reps, median reported
- **Command:**

```bash
.venv/bin/python scripts/bench_runtime_replacement.py \
  --model mlx-community/Qwen2.5-0.5B-Instruct-4bit \
  --prompt-tokens 2000 \
  --max-tokens 16 \
  --reps 2
```

### Compared paths

- **stock** — current oMLX path with the new runtime recorder disabled
- **branch** — current branch with the runtime lifecycle recorder enabled and used as the benchmark truth source

## Wall-clock comparison

| Scenario | Stock TTFT ms | Branch TTFT ms | Stock total ms | Branch total ms | Readout |
| --- | ---: | ---: | ---: | ---: | --- |
| cold | 786.33 | 524.39 | 875.09 | 593.42 | branch recorded lower wall time here, but this is **not** evidence of a replaced executor |
| restart via cache | 388.83 | 448.96 | 459.91 | 514.91 | branch slightly worse |
| restart via restore | 325.89 | 351.68 | 400.41 | 418.61 | branch slightly worse |

## Branch-only runtime truth now available

This is the actual value added by the first runtime-owned slice: the branch can now report phase timings that stock did not own directly.

| Scenario | Prefill ms | Decode ms | Throughput tps | Peak batch | Restore success |
| --- | ---: | ---: | ---: | ---: | ---: |
| cold | 339.90 | 249.69 | 27.46 | 1 | 0 |
| restart via cache | 160.96 | 345.52 | 38.62 | 1 | 0 |
| restart via restore | 108.34 | 302.74 | 43.33 | 1 | 2 |

## Cache / recovery observations

- cache hits occurred on both stock and branch restart-cache runs
- restore succeeded on the branch restore scenario and is now visible in the runtime benchmark
- `archive_non_manifest_files` remained empty in every run, which confirms the archive stayed metadata-only

## What this benchmark proves

### Better
- the branch now owns **runtime lifecycle measurement** in one place
- the branch can report TTFT, prefill, decode, restore cost, batch size, and tail latency from the runtime path itself

### Worse or unchanged
- there is **no measured proof yet** that the branch has replaced the execution spine
- restart wall-clock behavior is not consistently better than stock
- the underlying executor is still the same scheduler + `BatchGenerator` path

## Verdict

**This pass replaces something meaningful in observability and benchmark ownership, not in decode ownership.**

That is enough to continue the runtime-replacement effort honestly, but it is **not** enough to claim that the branch is already a replacement engine.
