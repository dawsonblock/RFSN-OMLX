# Executor Boundary Target

> One seam only. One authoritative path. No fake replacement claims.

## Targeted seam

The exact seam targeted in this pass is:

## Scheduler-owned decode-step / finish / cancellation / emission boundary

This means the main runtime path now routes decode stepping through branch code before any response is allowed to mutate request-visible state.

The stock generator is still used as the low-level execution primitive, but the scheduler is now the authority over:
- when a decode step is consumed,
- which responses are suppressed after cancellation,
- when a request is locally marked length-capped,
- and how the executor boundary is measured.

---

## Why this is the correct next step

This seam is the next correct ownership step because:

1. it is the smallest place where the branch previously lost real control,
2. it can be owned without adding UI or public API surface,
3. it improves authority rather than decoration,
4. and it keeps the runtime honest about what is still delegated.

Taking a larger seam now would have required a much broader rewrite of the MLX decode path and cache internals.

---

## What branch-owned authority exists after this lands

After this pass, the branch owns:
- the default decode-step boundary in `Scheduler.step()`
- the internal owned path in `Scheduler._run_owned_decode_step()`
- cancellation suppression before token emission
- local finish normalization when request budget is exhausted
- executor-boundary counters in the runtime metrics snapshot
- a controlled `stock` comparison mode for benchmark/regression only

This is a real authority shift, not just a passive observer layer.

---

## What remains delegated

Still delegated for now:
- MLX model forward passes,
- low-level token sampling,
- `BatchGenerator`’s internal cache stepping,
- and stop-state machinery inside the stock generation stack.

This is why the branch is still **not** claiming a full runtime replacement.

---

## Controlled comparison path

The previous direct stock handoff remains available only behind the narrow internal mode:

- `OMLX_EXECUTOR_BOUNDARY_MODE=stock`

Default behavior is the new owned seam.

That comparison path exists strictly so the repo can benchmark and validate the seam honestly.

---

## Recommendation

Keep going **only seam by seam**.

The next worthwhile step would be deeper control over the token-step primitive itself, but only if the current owned boundary remains stable and continues to justify its authority with measurements.
