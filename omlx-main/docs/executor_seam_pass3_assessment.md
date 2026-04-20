# Executor Seam Pass 3 Assessment

> Status: landed.
>
> Scope: scheduler-owned EOS stop detection gate inside `_run_owned_decode_step`.

## Short answer

Before this pass, when the stock `BatchGenerator` returned a response with
`finish_reason=None` but the emitted token was an EOS token, the branch
would pass that response through without claiming any ownership over the
stop decision.  Stop authority resided entirely with the stock generation
stack.

After this pass, the branch owns EOS stop detection independently:
- the emitted `response.token` is checked against the branch-held EOS token
  set (built by `_get_stop_tokens()` from tokenizer + generation_config.json),
- if the token is EOS and stock has not yet set `finish_reason`, the branch
  overrides to `finish_reason="stop"` using the existing
  `_clone_response_with_finish_reason` helper,
- each such override is counted as `stop_overrides` in `_ExecutorStepOutcome`
  and accumulated in `RuntimeMetricsRecorder._executor_stop_overrides`,
- the `snapshot()` key `executor_stop_overrides` reports the total.

It still does not own low-level token sampling, logits processing, or
KV cache mutation internals.

---

## Exact stock handoff points (before this pass)

### Handoff — EOS stop decision
- file: omlx/scheduler.py
- function: Scheduler._run_owned_decode_step — response processing loop
- gap before this pass: `finish_reason` was read from the response as returned
  by `BatchGenerator.next_generated()` and passed through as-is. If stock
  missed or deferred the EOS signal, the branch had no independent check.

---

## Current ownership map (after this pass)

| Runtime concern | Owner now | Notes |
| --- | --- | --- |
| Prompt token ingestion | branch | Request plus scheduler intake path |
| Prefill orchestration (loop, cache init) | branch | external prefill loop in the scheduler |
| Pre-chunk abort gating | branch-owned seam | from Pass 2 |
| Per-chunk prefill step counters | branch-owned seam | from Pass 2 |
| Request-to-batch assembly | branch | waiting-to-running assembly in the scheduler |
| Decode-step entry | branch-owned seam | from Pass 1 |
| Finish normalization (length) | branch-owned seam | from Pass 1 |
| Cancellation suppression (decode side) | branch-owned seam | from Pass 1 |
| **EOS stop detection (decode side)** | **branch-owned seam** | Pass 3: token-vs-EOS check precedes stock finish_reason |
| **Stop override counter** | **branch-owned seam** | executor_stop_overrides in snapshot() |
| Result emission | branch | output shaping remains local in the scheduler |
| Cache persistence and reuse | branch | paged cache and SSD cache remain branch-owned |
| Low-level model forward pass | delegated | still handled by the MLX execution path |
| Token sampling math | delegated | still inside the stock generation stack |
| KV cache mutation internals | delegated | still below all owned seams |

---

## Chosen candidate seam

### Scheduler-owned EOS stop detection

Why this seam is the correct next one:
- the branch already holds EOS token sets built from the tokenizer and
  generation_config.json via `_get_stop_tokens()`,
- `response.token` (int) is available in the decode response loop,
- the pattern is symmetric with the length-override already in Pass 1 —
  both are finish-reason overrides the branch can make locally,
- the gate is narrow: one `in` check against a pre-built set, zero new
  imports, no model forward involvement,
- it is real and testable without a fake adapter or second runtime.

---

## What still remains delegated after taking this seam

Still delegated:
- low-level model forward execution (MLX),
- token sampling and logits handling,
- per-request stop-string matching (sequence state machine, not token IDs),
- decode-side and prefill-side KV cache mutation internals below the seam.
