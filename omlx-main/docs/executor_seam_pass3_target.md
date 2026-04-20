# Executor Seam Pass 3 Target

> Status: landed.
>
> This document describes what was taken in Pass 3 and recommends Pass 4
> candidates.

---

## What was taken in Pass 3

### Scheduler-owned EOS stop detection

The decode-side response processing loop inside `_run_owned_decode_step` now
independently checks `response.token` against the branch-held EOS token set
before forwarding the response.  When stock has not yet set `finish_reason`
and the emitted token is EOS, the branch overrides to `finish_reason="stop"`
locally, without waiting on the stock `BatchGenerator` to surface the signal.

Gate placement — the EOS check is evaluated before the existing length-cap
override so that an EOS token at exactly the max-tokens boundary is classified
as `"stop"` not `"length"`.

Metrics surface — `executor_stop_overrides` in `RuntimeMetricsRecorder.snapshot()`.

---

## What the ownership state looks like now

After three passes the branch owns three seam-level decode/prefill concerns:
1. Decode-step entry, length-cap finish override, cancellation suppression
   (Pass 1 — `_run_owned_decode_step` entry gate and length check)
2. Pre-chunk prefill abort gating, per-chunk counters (Pass 2 —
   `_run_owned_prefill_step`)
3. Decode-side EOS stop detection (Pass 3 — token-vs-EOS check)

All three seams add zero latency to the hot decode path (set membership
test, no I/O, no model calls).

---

## Recommended Pass 4 candidates

The following three candidates are ready to evaluate:

### Candidate A — Per-token logprob authority
- Current state: the branch surfaces whatever token logprobs arrive in the
  response object from stock.  If stock returns `None`, the branch cannot fill
  in its own estimate.
- What the branch already owns: it holds the raw sampling distribution from the
  MLX forward-pass helper when `logprobs` is enabled (or can hook it).  The
  `request.sampling_params.logprobs` field is already read by the scheduler.
- Seam: decode loop, after the EOS check — add a guard that back-fills
  `response.logprobs` from branch state when the stock field is absent.
- Benefit: closes a correctness gap for OpenAI-compatible `/v1/completions`
  logprobs without relying on stock to emit them.

### Candidate B — Finish-reason authority for custom stop strings
- Current state: stop-string matching (sequence-level, not token-level) is
  delegated.  The branch knows `sampling_params.stop` but does not evaluate it
  independently.
- What the branch already owns: the `output_parser_factory` and the per-request
  `output_tokens` list.
- Seam: end of decode loop per response — match the accumulated token string
  against `stop` patterns; override `finish_reason` to `"stop"` if matched.
- Benefit: stops depending on the stock execution path for string-level stop,
  giving the branch independent control of response boundaries.

### Candidate C — Decode-side per-request cache-write authority
- Current state: KV cache writes during decode are handled inside the stock
  execution path.
- What the branch already owns: the paged cache handle and the SSD cache both
  exist in the scheduler; page-in/page-out is already branch-driven.
- Seam: between receiving decoded tokens and feeding the next decode step —
  schedule the cache write using branch state rather than delegating to stock.
- Benefit: enables cache eviction and reuse decisions to be made with full
  knowledge of in-flight requests, without waiting on the stock path.

### Recommended ordering

Candidate A first — it is narrow (one field, one guard, one test), does not
require changing the prefill path or cache machinery, and closes a correctness
gap that users can observe via the API.  Candidate B second (slightly wider,
requires a string-match loop).  Candidate C is the largest structural change
and should come last.
