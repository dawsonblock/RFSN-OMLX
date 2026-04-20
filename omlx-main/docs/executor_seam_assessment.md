# Executor Seam Assessment

> Status: first real executor-seam ownership pass. A second pass followed — see `next_executor_seam_assessment.md`.
>
> Scope: identify the exact stock handoff, record current ownership, and pin down the smallest clean seam the branch can truly own.

## Short answer

Before this change, control left branch-owned code in two precise places inside the scheduler:

1. after external prefill, when the scheduler called the stock insert path into the MLX generator
2. during each decode step, when the scheduler directly delegated to the stock generation call and only received opaque responses back

That meant the branch owned admission, queueing, and prefill orchestration, but not the decode-step boundary itself.

After this pass, the branch owns one real seam:
- decode-step entry and gating,
- pre-step pruning of locally ineligible work,
- cancellation suppression before emission,
- local finish normalization against request budget,
- and executor-boundary accounting for proof and audit.

It still does not own low-level model forward execution or token sampling.

---

## Exact stock handoff points

### Handoff 1 — request enters stock generation
- file: omlx/scheduler.py
- function: Scheduler._schedule_waiting
- handoff: the scheduler inserts the last prompt token plus cache state into the stock generator path

### Handoff 2 — decode stepping leaves branch control
- file: omlx/scheduler.py
- function: Scheduler.step
- handoff before this pass: the scheduler directly called the stock generator for the next decode responses

These were the concrete places where control left branch-owned code.

---

## Current ownership map

| Runtime concern | Owner now | Notes |
| --- | --- | --- |
| Prompt token ingestion | branch | Request plus scheduler intake path |
| Prefill orchestration | branch | external prefill loop in the scheduler |
| Request-to-batch assembly | branch | waiting-to-running assembly in the scheduler |
| Decode-step entry | branch-owned seam | scheduler now gates decode responses through its own seam |
| Finish normalization | branch-owned seam | local request budget can force a length finish |
| Cancellation suppression | branch-owned seam | cancelled requests can be suppressed before emission |
| Result emission | branch | output shaping remains local in the scheduler |
| Cache persistence and reuse | branch | paged cache and SSD cache remain branch-owned |
| Low-level model forward pass | delegated | still handled by the stock MLX execution path |
| Token sampling math | delegated | still inside the stock generation stack |
| Decode-side cache mutation internals | delegated for now | still below the owned seam |
| Pre-chunk prefill abort gating | branch-owned seam | added in second executor-seam pass; abort check runs before model forward |
| Per-chunk prefill step counters | branch-owned seam | added in second executor-seam pass; mark_prefill_chunk in RuntimeMetricsRecorder |

---

## Chosen candidate seam

## Scheduler-owned decode-step, finish, cancellation, and emission boundary

Why this seam is the correct next one:
- it is the smallest place where control actually left the branch on every decode step,
- it is real and testable,
- it does not require a fake adapter layer,
- and it preserves one authoritative runtime path.

---

## What still remains delegated after taking this seam

Still delegated for now:
- low-level model forward execution,
- token sampling and logits handling,
- sequence-state internals in the stock generation stack,
- decode-side KV cache mutation internals below the scheduler seam.

The second executor-seam pass took the pre-chunk prefill step gate — see `next_executor_seam_assessment.md`.  The third pass took the decode-side EOS stop detection gate — see `executor_seam_pass3_assessment.md`.

That is why this is a real ownership gain without pretending the entire runtime is already replaced.

---

## Risks and non-goals

### Risks
1. semantic drift between local finish normalization and stock generation state
2. double-finalization if cancellation handling is not kept disciplined
3. cache-lifecycle mistakes if suppressed requests are not cleaned up correctly

### Non-goals
- no UI
- no public API expansion
- no second runtime story
- no speed claims without measured evidence
- no broad rewrite of the full MLX executor path

---

## Evidence that the seam is actually owned

This seam counts as real ownership because:
- the default scheduler path now passes through branch code before a decode response can advance request state,
- the branch can finalize some cancelled or locally complete requests without entering stock decode at all,
- the branch can suppress cancellation locally before emission,
- the branch can locally normalize finish state when the request budget is exhausted,
- and targeted tests plus stock-versus-owned benchmarks verify that this path is exercised.
