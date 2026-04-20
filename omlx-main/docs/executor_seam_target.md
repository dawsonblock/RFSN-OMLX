# Executor Seam Target

> One seam only. One authoritative path. No fake replacement claims.

## Chosen seam

The seam chosen in this pass is:

## Scheduler-owned decode-step and finish-cancellation-emission boundary

This means the main runtime path now routes decode responses through branch-owned scheduler code before they are allowed to mutate request-visible state.

The lower-level generator remains delegated for now, but the scheduler is the authority over:
- whether a decode response is accepted,
- whether a cancelled request is suppressed,
- when a request is locally marked as length-capped,
- and how the executor boundary is measured and compared.

---

## Exact authority gained after landing it

After this lands, the branch owns:
- the default decode-step boundary in the scheduler step loop
- pre-step pruning of cancelled or locally complete requests before stock decode
- branch-side finish normalization
- branch-side cancellation suppression before emission
- branch-side executor-seam counters and runtime proof data
- a narrow stock comparison mode for controlled benchmarking only

This is real authority, not just observation.

---

## What remains delegated

Still delegated after this seam:
- MLX model forward passes
- low-level token sampling
- deeper stock generation internals
- decode-side cache mutation details under the generator

That is why the project is closer to replacement, but not yet a replacement runtime.

---

## How correctness is protected

Correctness is protected by:
- keeping one scheduler story,
- preserving the stock path only for controlled comparison,
- using focused scheduler tests for cancellation and finish behavior,
- and verifying non-regression across scheduler and lineage suites.

---

## What metrics and tests prove ownership

Ownership is proven by:
- focused seam tests for local finish normalization and cancellation suppression
- runtime metrics that report owned versus stock executor mode
- repeated stock-versus-owned benchmark trials on the same model and workload
- non-regression test evidence across scheduler and lineage behavior

---

## Recommendation

Keep the owned seam.

The next seam (pre-chunk prefill step gate) was taken in the second executor-seam pass — see `next_executor_seam_target.md`. It added pre-chunk abort gating and per-chunk prefill metrics without requiring a broader rewrite.
