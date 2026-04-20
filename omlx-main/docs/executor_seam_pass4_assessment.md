# Pass 4 — Prefill Seam Assessment

Honest audit of where branch-owned code ends and stock `mlx_lm` compute
begins inside the prefill path, written before deciding the Pass 4 target.

All line numbers are against the current commit of `omlx/scheduler.py` and
related files in this branch.

## TL;DR

The branch **already owns prefill orchestration** end-to-end. The only stock
code invoked during prefill is the per-chunk forward pass
`self.model(chunk, cache=prompt_cache, **model_kwargs)`. That single call
enters `mlx_lm.models.<arch>.Model.__call__`, which in turn drives the
per-layer attention path where each `KVCache.update_and_fetch(keys, values)`
mutation happens. Owning *that* layer means forking every model
architecture this branch supports (llama, qwen2, qwen3, gemma, mixtral,
deepseek_v32, qwen3_5 hybrid SSM, and the VLMs) and monkey-patching each
`KVCache.update_and_fetch`. That is a per-architecture rewrite, not a
scheduler seam.

The honest answer to Pass 4's governing question — "can real prefill-seam
ownership be taken cleanly without a much larger rewrite?" — is **no, it
already was taken in Pass 2**, and the remaining stock code is fused
forward compute that cannot be cleanly intercepted at the scheduler layer.

## 1. Where control currently leaves branch-owned code and enters stock prefill/generation

Single callsite, per prefill chunk, inside the branch's owned prefill step
method:

- `omlx/scheduler.py:1186` — `self.model(chunk, cache=prompt_cache, **model_kwargs)`

`self.model` is the `mlx_lm`-loaded model object (or its VLM wrapper).
Calling it enters `mlx_lm.models.<arch>.Model.__call__` which runs embed →
N × `TransformerBlock` (with `Attention.__call__` calling
`cache[i].update_and_fetch(keys, values)`) → norm → lm_head. Branch code
regains control immediately after that call on `omlx/scheduler.py:1187`
with `mx.eval([c.state for c in prompt_cache])`.

There is **no stock prefill driver** on the hot path anymore. The branch
does not call `mlx_lm.generate_step`, `BatchGenerator.prefill`,
`process_prompt`, or any other stock sequencing routine. The call to
`self.batch_generator.insert(...)` at `omlx/scheduler.py:3302-3309`
receives a **pre-filled cache and a single last token**, so the stock
`BatchGenerator` only ever sees decode work.

Two auxiliary prefill callsites exist but are off the main path and are
themselves branch-orchestrated:

- `omlx/scheduler.py:3164, 3176` — SpecPrefill system-prompt full-prefill;
  still `self.model(...)` with a branch-managed cache, branch decides when
  to run it.
- `omlx/patches/specprefill.py` — draft-model prefill for the SpecPrefill
  draft path (`_prefill_draft`, `sparse_prefill`); branch-owned
  orchestration, stock forward compute.

## 2. Function currently owning prompt token ingestion into prefill

Branch-owned:

- `Scheduler._schedule_waiting` at `omlx/scheduler.py:3237-3259` picks
  `tokens_to_process` (the prompt tokens minus anything already cached) and
  chooses between the external prefill driver or SpecPrefill.
- `Scheduler._do_external_prefill` at `omlx/scheduler.py:1194-1413` takes
  `tokens` and converts `tokens[:-1]` into an `mx.array(prefill_tokens)[None]`
  at `omlx/scheduler.py:1306`. Explicit branch policy: always hold back the
  final token so decode's first step sees a real logit-producing forward.

No stock code decides what tokens to ingest or when.

## 3. Function currently owning prefill chunk boundaries (sizing and sequencing)

Branch-owned, entirely inside `_do_external_prefill`
(`omlx/scheduler.py:1309-1325`):

```python
while input_arr.shape[1] > 0:
    remaining = input_arr.shape[1]
    n_to_process = min(prefill_step_size, remaining)
    if boundary_enabled and block_size > 0:
        current_total = base_size + processed_tokens
        next_boundary = ((current_total // block_size) + 1) * block_size
        target_boundary_prefill = next_boundary - base_size
        delta = target_boundary_prefill - processed_tokens
        if delta > 0:
            n_to_process = min(n_to_process, delta)
        n_to_process = max(1, n_to_process)
```

Configuration knobs are branch-owned:

- `SchedulerConfig.prefill_step_size = 2048` (`omlx/scheduler.py:327`)
- `SchedulerConfig.paged_cache_block_size = 256` (`omlx/scheduler.py:330`)

The stock `BatchGenerator` constructor is still handed `prefill_step_size`
via `Scheduler._create_batch_generator` at `omlx/scheduler.py:1021`, but
since external prefill runs first and hands `insert()` a pre-filled cache,
that stock value is not exercised on the prefill path.

## 4. Function currently owning the cache-mutation boundary around prefill

Branch-owned boundary; stock-owned internal mutation.

Branch-owned boundary tasks:

- Allocation: `make_prompt_cache(self.model)` at `omlx/scheduler.py:1241`
  (imported from `mlx_lm.models.cache` at `omlx/scheduler.py:33`).
  Deliberately **not** TurboQuantized during prefill — explicit policy
  comment at `omlx/scheduler.py:1221-1234` citing issue #771.
- Post-chunk eval: `mx.eval([c.state for c in prompt_cache])` at
  `omlx/scheduler.py:1187` (inside `_run_owned_prefill_step`).
- Boundary snapshot: `Scheduler._emit_prefill_boundary_snapshot(...)` at
  `omlx/scheduler.py:1355-1361` and again post-loop at
  `omlx/scheduler.py:1390-1398`. Snapshots fire at every
  `paged_cache_block_size`-aligned boundary when the paged SSD cache is
  active.
- Reclamation: `_sync_and_clear_cache()` at `omlx/scheduler.py:1386` and
  `omlx/scheduler.py:1400`.

Stock-owned internal mutation:

- Per-layer `cache[i].update_and_fetch(keys, values)` inside every
  attention layer. That method lives in `mlx_lm.models.cache.KVCache`,
  `RotatingKVCache`, `QuantizedKVCache`, and
  `mlx_vlm.turboquant.TurboQuantKVCache`. The branch does not wrap or
  intercept it during prefill. The re-exports at
  `omlx/turboquant_kv.py:17-42` (`TurboQuantKVCache`,
  `BatchTurboQuantKVCache`, `create_attention_mask`, `create_causal_mask`)
  are import shims; their implementations are stock.

## 5. Function currently deciding prefill is done and decode may begin

Branch-owned.

- `Scheduler._do_external_prefill` exits its `while input_arr.shape[1] > 0`
  loop when no more prompt tokens remain, then returns
  `(prompt_cache, last_token)` at `omlx/scheduler.py:1413`, where
  `last_token` is `tokens[-1:]`. End marker:
  `self.runtime_metrics.mark_prefill_end(request.request_id)` at
  `omlx/scheduler.py:1412`.
- `Scheduler._schedule_waiting` at `omlx/scheduler.py:3302-3309` then
  hands the pre-filled cache and the one remaining token to
  `self.batch_generator.insert([tokens_to_process], ..., caches=[cache_to_use], ...)`.
  Stock `BatchGenerator` sees only decode.

There is no stock-side gate that decides "prefill done". The branch is
authoritative.

## 6. Seams already owned per prior passes

| Seam | Pass | Owner | Location |
| --- | --- | --- | --- |
| Decode-step entry | 1 | `Scheduler._run_owned_decode_step` | `omlx/scheduler.py:3874-3989` |
| Local abort suppression | 1 | same method | `omlx/scheduler.py:3896-3906, 3925-3930` |
| Length-cap finish override | 1 | same method | `omlx/scheduler.py:3907-3919, 3968` |
| Pre-chunk prefill abort gate | 2 | `Scheduler._run_owned_prefill_step` | `omlx/scheduler.py:1140-1192` |
| Per-chunk prefill counters | 2 | `RuntimeMetricsRecorder.mark_prefill_chunk` | `omlx/runtime_metrics.py:272-279` |
| Prefill chunk sizing + block-boundary snap | 2 | `Scheduler._do_external_prefill` | `omlx/scheduler.py:1309-1325` |
| Boundary snapshot emission | 2 | `Scheduler._emit_prefill_boundary_snapshot` + inline call | `omlx/scheduler.py:1346-1361, 1390-1398` |
| Prefill→decode handoff via pre-filled cache | 2 | `Scheduler._schedule_waiting` | `omlx/scheduler.py:3302-3309` |
| EOS stop detection override | 3 | `Scheduler._run_owned_decode_step` via `_get_stop_tokens` | `omlx/scheduler.py:3958-3966`, `omlx/scheduler.py:892` |

Decode-seam gating knob:

- `OMLX_EXECUTOR_BOUNDARY_MODE` resolved in
  `Scheduler._resolve_executor_boundary_mode`
  (`omlx/scheduler.py:3840-3854`). Default `"owned"`; `"stock"` reverts
  only the **decode** step to the direct-`BatchGenerator` path for
  controlled comparison. It does not toggle the prefill seam; prefill is
  always branch-owned once a prompt has >1 token and SpecPrefill is not
  active.

## 7. Smallest real prefill seam the branch can own next — candidates

Every candidate below is either (a) an annotation/consolidation inside
already-owned branch code or (b) a fork of stock model forward compute.
None of (a) transfers real authority; (b) requires a much larger rewrite.

### Annotation-only (no new authority)

- **A. Per-chunk progress + cache-offset invariant.** Today
  `Scheduler._on_prompt_progress` at `omlx/scheduler.py:1027-1052` runs
  only as a dashboard-side effect. A branch-owned invariant check that
  `sum(cache.offset) == base_size + processed_tokens` after every chunk
  would harden correctness without adding authority.
- **B. Per-chunk memory guard hoist.** The memory-limit check at
  `omlx/scheduler.py:1367-1386` currently raises `RuntimeError` mid-loop
  from inside `_do_external_prefill`. Hoisting it into a
  `_run_owned_prefill_step` pre-hook gives a single-sited abort policy.
- **C. Boundary snapshot emission counter.** Add
  `prefill_boundary_snapshots_emitted` to `RuntimeMetricsRecorder` for
  observability parity with `mark_prefill_chunk`.
- **D. Owned cache-object construction wrapper.**
  `Scheduler._make_owned_prompt_cache(model, request)` would wrap
  `make_prompt_cache(self.model)` so TurboQuant policy (deliberately off
  for prefill per #771) is a single-sited decision. Counter-inflation, no
  new authority.
- **E. Preflight KV-byte estimate.** A branch-owned per-request estimate
  from prompt length plus `model.config.num_hidden_layers` /
  `hidden_size` / element size; admission-side, separate from
  `_preflight_memory_check`.

### Compute-taking (rejected as a seam)

- **F. Per-layer manual forward.** Bypass `self.model(chunk, cache=...)`
  by calling each `TransformerBlock` directly, invoking
  `cache[i].update_and_fetch` explicitly between layers. That is
  architecture-specific: every supported model exposes its own block list
  and attention layout. Branch would have to replicate, per architecture:
  token-embedding lookup, rotary position offsetting (position derived
  from `cache.offset` inside each stock `Attention.__call__`), attention
  mask construction, the VLM `inputs_embeds` injection path, chunked
  attention for sliding-window models, mRoPE delta capture, the gated
  delta / SSM hybrid path for qwen3_5, and MoE routing for mixtral /
  deepseek. None of that is a "seam" — it is a rewrite.

**Conclusion of Phase 1:** the branch has no clean prefill seam left to
take that transfers new authority. Candidates A–E consolidate existing
authority. Candidate F is the per-architecture rewrite that Pass 4 was
explicitly instructed to refuse if discovered.

## 8. What still remains delegated after taking any of A–E

- `mlx_lm.models.<arch>.Model.__call__` (full forward) at
  `omlx/scheduler.py:1186`.
- Per-layer `KVCache.update_and_fetch` / `RotatingKVCache.update_and_fetch`
  / `QuantizedKVCache.update_and_fetch` /
  `TurboQuantKVCache.update_and_fetch`.
- RoPE position offset logic inside each stock `Attention.__call__`
  (derived from `cache.offset`).
- Attention mask construction via
  `mlx_lm.models.base.create_attention_mask` /
  `create_causal_mask` (re-exported at `omlx/turboquant_kv.py:17-22`).
- Hybrid-cache semantics for SSD-state models (qwen3_5 GatedDeltaNet and
  friends — the existing `omlx/patches/gated_delta_advance.py` touches
  `advance_stream`, not `update_and_fetch`).
- Decode compute: `BatchGenerator.next_generated()` at
  `omlx/scheduler.py:3942` still owns sampling, logits processing, and
  decode-side `update_and_fetch`.

## 9. What would prove the prefill seam is actually owned (not just wrapped)

A test is "proof of ownership" only if it demonstrates that removing or
changing branch code changes what happens at the stock boundary. Tests
that only import modules or assert counters exist do not qualify. Proofs
below are implementable against the branch today:

- **P1 — prefill→decode handoff shape.** Spy
  `Scheduler.batch_generator.insert` and assert it is always called with
  exactly one token per request when that request had >1 prompt token and
  SpecPrefill is inactive. This proves `_do_external_prefill` took
  ownership before stock saw the prompt.
- **P2 — pre-chunk abort truly pre-empts compute.** Register an abort
  before invoking `_run_owned_prefill_step` on a request's UID; spy
  `self.model.__call__`. Assert `model.__call__` is not invoked and
  `_PrefillAbortedError` is raised.
- **P3 — chunk sequencing is branch math.** Set
  `prefill_step_size=7` on a 22-token prompt; spy `self.model.__call__`.
  Assert the chunk-length sequence is exactly `[7, 7, 7]` (since the last
  token is held back, 21 tokens over chunks of 7).
- **P4 — block-boundary snap is branch math.** Set
  `prefill_step_size=16`, `paged_cache_block_size=8`, prompt length 41
  (prefill 40), block_aware_cache active, starting offset 0. Assert
  `self.model.__call__` receives chunks `[8, 8, 8, 8, 8]` (each clipped
  to the next 8-boundary) and boundary snapshots fire at
  `total_tokens ∈ {8,16,24,32,40}`. [Deferred to future work: pending
  integration fixture for the paged-SSD `BlockAwareCache`.]
- **P5 — per-chunk counters match chunk count.** Assert
  `runtime_metrics.mark_prefill_chunk(..., aborted=False)` is invoked
  exactly once per chunk across several prompt lengths. Assert
  `aborted=True` is invoked exactly once when a pre-chunk abort fires.
- **P6 — last-token handoff invariant.** Assert the return value of
  `_do_external_prefill(request, tokens, None)` is
  `(cache, [tokens[-1]])` for any `len(tokens) > 1`.
- **P7 — model forward is the sole stock callsite during prefill.**
  Instrument `self.model.__call__` only; assert no other stock entry
  point (no `mlx_lm.generate_step`, no `BatchGenerator.insert`, no
  `next_generated`) is called during `_do_external_prefill`.

Proofs **not** possible without new code:

- Stock-vs-owned prefill comparison via an env flag. The prefill seam has
  no stock-comparison mode (unlike the decode seam). A benchmark can
  measure owned prefill at varying `prefill_step_size`, which is the
  honest thing to measure, but it cannot claim a stock-vs-branch speed
  delta on prefill at the scheduler level.

## 10. Stock API contract

`mlx_lm` is pinned at `pyproject.toml:35`:

```
mlx-lm @ git+https://github.com/ml-explore/mlx-lm@dcbf6e33d135a1b7c6767ca0fe7ebbd23df814a7
```

Any Pass 4 reasoning about what is "stock" is relative to that commit.

## Verdict

There is no clean prefill seam left for Pass 4 to take. The prefill
orchestration seam is already fully owned; the only remaining code under
stock authority inside prefill is the fused per-layer forward +
`update_and_fetch`, which requires a per-architecture rewrite. Per the
task's own governing rule ("If that cannot be owned cleanly without a
much larger rewrite, stop and report that instead of faking it"), Pass 4
is a stop-and-report with formal verification of existing authority and
an honest benchmark of the already-owned path.
