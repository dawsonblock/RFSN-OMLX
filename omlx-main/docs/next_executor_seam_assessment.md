# Next Executor Seam Assessment

> Status: second real executor-seam ownership pass — landed.  A third pass
> followed — see `executor_seam_pass3_assessment.md`.
>
> Scope: take ownership of the per-chunk prefill step boundary, mirroring
> the decode-side seam already established in the first pass.

## Short answer

Before this change, the scheduler's prefill loop called `self.model()` inline
and only checked for pending aborts **after** each chunk completed.  That meant
a request whose abort was already registered before a chunk would still burn
compute on that chunk before the abort was detected.

After this pass, the branch owns the per-chunk prefill step gate:
- pre-chunk abort check that skips `self.model()` entirely if an abort is
  already pending,
- per-chunk step counters (`prefill_chunks_completed`,
  `prefill_chunks_aborted`) recorded in `RuntimeMetricsRecorder`,
- and a narrow, testable `_run_owned_prefill_step` method that is the single
  place where branch code decides whether a prefill chunk runs at all.

It still does not own low-level model forward execution, token sampling, or
KV cache mutation internals below the owned boundary.

---

## Exact stock handoff points (before this pass)

### Handoff — per-chunk prefill model call
- file: omlx/scheduler.py
- function: Scheduler._do_external_prefill
- handoff before this pass: inline `self.model(input_arr[:, :n_to_process], cache=prompt_cache, **model_kwargs)` followed by `mx.eval(...)`, then a **post**-chunk abort check

That post-chunk ordering was the concrete gap: compute fired first, abort was
tested second.

---

## Current ownership map (after this pass)

| Runtime concern | Owner now | Notes |
| --- | --- | --- |
| Prompt token ingestion | branch | Request plus scheduler intake path |
| Prefill orchestration (loop, cache init) | branch | external prefill loop in the scheduler |
| **Pre-chunk abort gating** | **branch-owned seam** | abort check runs before model forward |
| **Per-chunk prefill step counters** | **branch-owned seam** | mark_prefill_chunk records completed/aborted |
| Request-to-batch assembly | branch | waiting-to-running assembly in the scheduler |
| Decode-step entry | branch-owned seam | from first executor-seam pass |
| Finish normalization | branch-owned seam | from first executor-seam pass |
| Cancellation suppression (decode side) | branch-owned seam | from first executor-seam pass |
| Result emission | branch | output shaping remains local in the scheduler |
| Cache persistence and reuse | branch | paged cache and SSD cache remain branch-owned |
| Low-level model forward pass | delegated | still handled by the MLX execution path |
| Token sampling math | delegated | still inside the stock generation stack |
| KV cache mutation internals | delegated | still below both owned seams |

---

## Chosen candidate seam

## Scheduler-owned pre-chunk prefill step gate

Why this seam is the correct next one:
- it is the smallest place in the prefill path where a pending abort could
  waste compute that the branch can prevent,
- it mirrors `_run_owned_decode_step` exactly on the prefill side,
- it is real and testable without a fake adapter or second runtime,
- and it adds accountable per-chunk metrics that were previously absent.

---

## What still remains delegated after taking this seam

Still delegated for now:
- low-level model forward execution (MLX),
- token sampling and logits handling,
- sequence-state internals in the stock generation stack,
- decode-side and prefill-side KV cache mutation internals below the scheduler seam.
