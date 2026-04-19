# Executor Boundary Assessment

> Status: first real executor-boundary ownership pass.
>
> Scope: identify the exact stock handoff, document current ownership, and record the smallest clean seam the branch now owns.

## Short answer

Before this change, control left the branch in two exact places:

1. `Scheduler._schedule_waiting()` after external prefill, when the scheduler called `self.batch_generator.insert(...)`
2. `Scheduler.step()` when it called `self.batch_generator.next_generated()` directly

That meant the scheduler owned request admission and prefill orchestration, but it did **not** yet own the decode-step boundary itself.

After this pass, the scheduler now owns one real seam:

- decode-step entry and gating,
- cancellation suppression before emission,
- local finish normalization against the request budget,
- and executor-boundary accounting for measurement and audit.

It still does **not** own low-level model forward or token sampling.

---

## Exact handoff points to stock execution before this pass

### Handoff 1 — request admitted into stock generation
- File: `omlx/scheduler.py`
- Function: `Scheduler._schedule_waiting()`
- Exact boundary: `self.batch_generator.insert(...)`

At this point the branch handed the request’s last prompt token and any prefilled cache state into the stock MLX generator path.

### Handoff 2 — decode stepping fully delegated
- File: `omlx/scheduler.py`
- Function: `Scheduler.step()`
- Exact boundary before this change: `responses = self.batch_generator.next_generated()`

That call was the main place where decode control disappeared into stock execution and came back only as opaque response objects.

---

## Current ownership map from code

| Runtime concern | Owner now | Evidence |
| --- | --- | --- |
| Prompt token ingestion | branch | `Request`, `Scheduler.add_request()` |
| Prefill orchestration | branch | `Scheduler._do_external_prefill()` |
| Request-to-batch assembly | branch | `Scheduler._schedule_waiting()` |
| Decode-step entry | **branch-owned seam** | `Scheduler._run_owned_decode_step()` |
| Low-level model forward pass | delegated | `BatchGenerator.next_generated()` |
| Token sampling math | delegated | `mlx_lm.generate.GenerationBatch` |
| Decode-side cache mutation internals | delegated for now | stock MLX/BatchGenerator cache objects |
| Finish normalization against local budget | **branch** | `Scheduler._run_owned_decode_step()` |
| Cancellation suppression before emission | **branch** | `Scheduler._run_owned_decode_step()` |
| Result emission and output shaping | branch | `Scheduler._process_batch_responses()` |
| Cache persistence / block store | branch | `BlockAwarePrefixCache`, `PagedCacheManager`, `PagedSSDCacheManager` |

---

## Smallest plausible seam to own next

The chosen seam was:

## Scheduler-owned decode-step control with a narrow executor callback boundary

Why this was the smallest clean step:
- it is the first place where control fully left the branch on every decode step,
- it gives the scheduler real authority without a large rewrite,
- it does not invent a fake adapter system,
- and it keeps one authoritative runtime path.

What landed:
- the default path now goes through `Scheduler._run_owned_decode_step()`
- the scheduler decides whether a response is allowed to advance state
- pending cancellation can suppress emission before user-visible output is produced
- a locally exhausted request budget can be normalized to `length` under branch control even if the stock response is still open-ended

---

## Risks of taking this seam

1. **Semantic drift risk**
   - local finish normalization must stay aligned with the lower-level generator contract
2. **Double-finalization risk**
   - cancellation suppression must not cause the same request to be finished twice
3. **Cache-lifecycle risk**
   - suppressed responses still need safe cleanup through the existing request/block lifecycle
4. **Illusion risk**
   - this seam must not be mistaken for full executor replacement; low-level stepping is still delegated

---

## What still remains delegated

The following remain outside true branch ownership for now:
- low-level model forward execution,
- token sampling and logits handling inside the stock MLX generator,
- `SequenceStateMachine` internals,
- decode-side KV cache mutation details inside `BatchGenerator` / MLX cache objects.

That is why this branch is **closer** to runtime replacement, but is still not a full replacement runtime.

---

## Verdict

This pass moved the project forward in a real way.

It no longer only observes runtime behavior; it now owns one narrow but meaningful executor boundary.

The right next move, if work continues, is to keep taking ownership seam by seam rather than attempting a full rewrite all at once.
