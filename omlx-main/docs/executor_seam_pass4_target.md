# Pass 4 — Prefill Seam Target

Narrow target for Pass 4, derived from `docs/executor_seam_pass4_assessment.md`.

## Governing instruction

From the Pass 4 task:

> If that cannot be owned cleanly without a much larger rewrite, stop and
> report that instead of faking it.

The assessment established that every item in the task's "real prefill
ownership" checklist is already branch-owned (see
`docs/executor_seam_pass4_assessment.md` §1–§5). The only remaining stock
code in prefill is the per-chunk forward pass
`self.model(chunk, cache=prompt_cache, ...)` and the per-layer
`KVCache.update_and_fetch` it drives internally. Owning either requires
per-architecture model forks — a much larger rewrite.

**Therefore Pass 4's target is not to take a new seam. It is to formally
verify that the seam taken in Pass 2 is real, to document the per-
architecture rewrite wall beyond it, and to benchmark the already-owned
path honestly.**

## Exact authority the branch will gain in Pass 4

None. No new authority. This is a stop-and-report.

## Authority the branch already holds going into Pass 4

Cited verbatim from the assessment (§2–§5). These are the transitions Pass
4 will lock down with tests:

1. Prompt token ingestion — `Scheduler._do_external_prefill`
   (`omlx/scheduler.py:1194`).
2. Chunk sizing + block-boundary snap — the while-loop at
   `omlx/scheduler.py:1309-1325` using
   `SchedulerConfig.prefill_step_size=2048` and
   `SchedulerConfig.paged_cache_block_size=256`.
3. Pre-chunk abort gate and per-chunk counters —
   `Scheduler._run_owned_prefill_step` (`omlx/scheduler.py:1140-1192`) +
   `RuntimeMetricsRecorder.mark_prefill_chunk`
   (`omlx/runtime_metrics.py:272-279`).
4. Cache-mutation boundary — `make_prompt_cache` allocation, per-chunk
   `mx.eval([c.state for c in prompt_cache])`, boundary snapshots at
   `paged_cache_block_size`-aligned offsets, `_sync_and_clear_cache()`
   reclamation.
5. Prefill-complete → decode-eligible decision —
   `_do_external_prefill` returns `(prompt_cache, tokens[-1:])`;
   `_schedule_waiting` at `omlx/scheduler.py:3302-3309` hands
   `batch_generator.insert` a pre-filled cache + one token.

## Exact stock code that remains delegated after Pass 4

Per assessment §8:

- `mlx_lm.models.<arch>.Model.__call__` (the full forward) invoked at
  `omlx/scheduler.py:1186`.
- `KVCache.update_and_fetch` / `RotatingKVCache.update_and_fetch` /
  `QuantizedKVCache.update_and_fetch` /
  `TurboQuantKVCache.update_and_fetch` inside every attention layer.
- RoPE position offset logic inside each stock `Attention.__call__`.
- Attention mask construction (`create_attention_mask`,
  `create_causal_mask`).
- Hybrid-cache semantics for SSM-state models.
- All decode compute via `BatchGenerator.next_generated()`.

## Exact request state transitions under branch control

| Transition | Branch-owned trigger |
| --- | --- |
| `WAITING → PREFILLING` (first chunk) | `Scheduler._schedule_waiting` decides tokens_to_process length and dispatches (`omlx/scheduler.py:3237-3259`). |
| `PREFILLING (chunk k) → PREFILLING (chunk k+1)` | Loop in `_do_external_prefill` (`omlx/scheduler.py:1309`) controls stepping; each iteration goes through `_run_owned_prefill_step`. |
| `PREFILLING → PREFILL_ABORTED` | `_run_owned_prefill_step` pre-chunk abort gate raises `_PrefillAbortedError`, skipping the stock forward (`omlx/scheduler.py:1181-1188`). |
| `PREFILLING → DECODE_ELIGIBLE` | `_do_external_prefill` returns and `_schedule_waiting` hands a pre-filled cache + last token to `batch_generator.insert` (`omlx/scheduler.py:3302-3309`). |
| `DECODE_ELIGIBLE → RUNNING` | `_schedule_waiting` sets `request.status = RequestStatus.RUNNING` after `insert` returns UIDs. |

All five transitions are decided by branch code before any stock call.

## Correctness risks

Even without new code, Pass 4 tests must guard against regressions that
would break the existing seam:

- Any change that reorders `mx.eval` relative to `self.model(...)` inside
  `_run_owned_prefill_step` can desync `cache.offset` from
  `processed_tokens` (observed previously in stale `meta_state` on
  restored `RotatingKVCache`; see `omlx/scheduler.py:1264-1275`).
- Any change that lets `_do_external_prefill` ingest the last token would
  leave `batch_generator.insert` with zero decode-kickoff tokens and
  break `next_generated()`.
- Any change that runs TurboQuant conversion during prefill re-introduces
  the cache-merge failure path documented at
  `omlx/scheduler.py:1221-1234` (#771).
- Any change that lets the pre-chunk abort run *after* `self.model(...)`
  re-introduces the Pass 2 bug the owned seam fixed (abort detected only
  after compute was already spent).
- Any change that breaks the block-boundary snap math lets snapshots
  drift off `paged_cache_block_size` alignment, corrupting restore
  continuity tested by `test_restore_continuity`.

## Tests and metrics that will prove ownership

Per assessment §9, the following proofs are in scope for Pass 4 and are
implemented in `tests/test_prefill_seam_pass4.py`:

- **P1** — last-token handoff invariant (return shape of
  `_do_external_prefill`).
- **P2** — pre-chunk abort prevents `self.model.__call__` entirely.
- **P3** — chunk sequence length matches branch math at a custom
  `prefill_step_size`.
- **P5** — per-chunk counters match chunk count (completed + aborted).
- **P6** — `_do_external_prefill` returns `(cache, [tokens[-1]])` for
  every `len(tokens) > 1`.
- **P7** — `self.model.__call__` is the sole stock entry-point invoked
  during prefill (no `generate_step`, no `insert`, no `next_generated`
  during `_do_external_prefill`).

Deferred (requires paged-SSD `BlockAwareCache` integration fixture, not
worth the scope for a stop-and-report): **P4** — block-boundary snap.

Metric observability: `RuntimeMetricsRecorder._prefill_chunks_completed`
and `_prefill_chunks_aborted` (`omlx/runtime_metrics.py:272-279`) remain
the single source of truth for per-chunk counts.

## Benchmark scope

Honest, narrow. Since the prefill seam has no stock-comparison mode
(unlike the decode seam's `OMLX_EXECUTOR_BOUNDARY_MODE`), the benchmark
measures:

- Per-chunk prefill latency under owned orchestration at varying
  `prefill_step_size`.
- Aggregate prefill wall time for fixed prompt lengths.
- TTFT contribution of the owned prefill seam.
- Overhead of the `_run_owned_prefill_step` pre-chunk gate (one extra
  Python call per chunk) vs a hypothetical inline variant — measured as
  the cost ceiling the owned seam adds.

The benchmark will **not** claim a stock-vs-owned prefill speed delta
because no runtime toggle exists for that comparison on prefill. The
benchmark's conclusion will be a clarity-and-authority statement, not a
speed story.

## Explicit non-goals for Pass 4

- No per-architecture forward fork (candidate F in assessment §7).
- No `KVCache.update_and_fetch` interception (including no new wrappers
  over `TurboQuantKVCache`).
- No new executor seam, new runtime wrapper, or new public API.
- No UI.
- No additional workspace-lineage / session-archive surface.
- No speed claim without JSON benchmark proof.
- No speculative annotations (candidates A–E from assessment §7) in Pass
  4 — leaving room for a disciplined follow-up if observability need
  materializes.
- No broadening of Pass 4 scope to "just take one more seam" if doing so
  would imply a per-arch fork.

## Definition of done for Pass 4

1. `docs/executor_seam_pass4_assessment.md` — written.
2. `docs/executor_seam_pass4_target.md` — this document.
3. `tests/test_prefill_seam_pass4.py` — implements P1, P2, P3, P5, P6,
   P7 against a Scheduler built on the existing `mock_model` +
   `mock_tokenizer` fixtures, with `make_prompt_cache` monkey-patched to
   a deterministic stub cache and `self.model.__call__` spied to record
   chunk shapes.
4. `scripts/bench_pass4_prefill.py` + `scripts/bench_results_pass4_prefill.json`
   — measures per-chunk latency, total prefill latency, TTFT over a
   sweep of `prefill_step_size` values on a fixed prompt length.
5. `docs/executor_seam_pass4_benchmark.md` — honest results + plain
   verdict: owned prefill orchestration is real and cheap relative to
   forward compute; there is no Pass-4 speed story.
6. `docs/executor_seam_pass4_final.md` — final recommendation: stop
   seam-by-seam runtime replacement and keep branch as
   workspace-lineage + runtime observability + limited executor control.

No source file under `omlx/` is modified by Pass 4.
