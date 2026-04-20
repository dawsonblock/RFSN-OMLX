# Next Executor Seam Target

> One seam only. One authoritative path. No fake replacement claims.

## Chosen seam

The seam chosen in this pass is:

## Scheduler-owned pre-chunk prefill step and abort gate

This means the prefill path now routes every chunk through branch-owned
scheduler code before `self.model()` is allowed to fire.

The lower-level MLX forward pass remains delegated, but the scheduler is the
authority over:
- whether a prefill chunk runs at all (pre-chunk abort gate),
- when an already-pending abort is detected before wasting compute,
- and how many chunks were completed versus aborted (per-chunk metrics).

---

## Exact authority gained after landing it

After this lands, the branch owns:
- the default per-chunk prefill step boundary inside `_do_external_prefill`
- pre-chunk abort detection that skips `self.model()` for already-aborted requests
- per-chunk step counters: `prefill_chunks_completed` and `prefill_chunks_aborted`
- a single `_run_owned_prefill_step` method as the narrow, auditable gate

This is real authority, not just observation.  The pre-chunk abort skip is a
materially different code path from the former post-chunk check: it prevents
compute from firing rather than merely detecting it afterward.

---

## What remains delegated

Still delegated after this seam:
- MLX model forward passes (the actual tensor math)
- low-level token sampling
- deeper stock generation internals
- prefill-side and decode-side KV cache mutation details under the scheduler

That is why the project is moving closer to replacement, but is not yet a
replacement runtime.

---

## How correctness is protected

Correctness is protected by:
- keeping one scheduler story — no second runtime path introduced,
- `_run_owned_prefill_step` calling the same `self.model()` and `mx.eval()`
  that the inline code did, only with the pre-check gate around them,
- focused seam tests covering success, pre-abort skip, mid-sequence abort, and
  snapshot reporting,
- and non-regression verification across the full scheduler and runtime-metrics
  test suites.

---

## What metrics and tests prove ownership

Ownership is proven by:
- `TestPrefillSeamOwnership` (4 tests in tests/test_scheduler.py):
  - `test_prefill_seam_records_chunk_on_success` — model fires, chunk counted
  - `test_prefill_seam_pre_abort_skips_model_call` — model not called, abort counted, _PrefillAbortedError raised
  - `test_prefill_seam_abort_mid_multi_chunk` — first chunk fires, second is pre-aborted
  - `test_runtime_metrics_snapshot_reports_prefill_chunk_counts` — snapshot includes both new keys
- `RuntimeMetricsRecorder.mark_prefill_chunk` and its snapshot/reset coverage
- 92 scheduler + runtime-metrics tests passing with zero regressions

---

## Recommendation

Keep the owned seam.

If work continues, the next seam should only be taken if it increases real
authority again without forcing a much larger rewrite.  Candidates include
decode-side cache mutation ownership or early-exit logic inside the generation
loop, but only if each can be taken narrowly and without faking the runtime.
