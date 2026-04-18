# Session Archive — After-the-Fact Benchmark and Verdict

> **Merge label: experimental / internal — metadata-only session archive
> for explicit named-session recovery.** Not a latency feature. The paged
> SSD prefix cache does the real restart work; the session archive is a
> small operational handle on top of it. Do not expand further (no
> partial restore, no API / UI / admin surface, no multimodal, no hybrid
> restore) without a concrete operational use case.

This document records the truth-check of the session archive feature that
was wired in the previous pass: `Scheduler.restore_session` /
`commit_session`, the narrow `_hashes_from_block_table` +
`_finalize_session_for_request` helpers, the `add_request` / finish
boundary call sites, and the `SessionArchiveStore` JSON manifest layer.

The question is deliberately blunt: **does the feature earn its
complexity on the intended workload**, or does the paged SSD prefix
cache already cover the same ground?

## 1. Setup

- **Model**: `mlx-community/Qwen2.5-0.5B-Instruct-4bit` (text-only, 4-bit
  quantized, cached locally — no network fetch).
- **Python**: `3.10.12` via pyenv (`PYENV_VERSION=3.10.12`).
- **Package**: `mlx-lm` pinned to commit `dcbf6e3` (post-0.31.1 API requirement; see `pyproject.toml`).
- **Hardware**: Apple Silicon, Metal / MLX default device.
- **Config** (`SchedulerConfig` instantiated per run):
  - `max_num_seqs=1`, `max_num_batched_tokens=8192`
  - `paged_cache_block_size=128`
  - `paged_ssd_cache_dir=<tmp>/ssd`,
    `paged_ssd_cache_max_size=8 GiB`
  - `initial_cache_blocks=256`
  - `model_name="mlx-community/Qwen2.5-0.5B-Instruct-4bit"` (namespacing
    key for both SSD cache and session archive)
- **Session archive root**: `<tmp>/archive`.
- **Prompt shape**: deterministic `"The quick brown fox jumps over the
  lazy dog. "` repeated until `len(tokenizer.encode(text)) >= 4000`;
  resulting `num_prompt_tokens = 4001` per turn.
- **Sampling**: `temperature=0.0`, `max_tokens=4` (we only care about
  prefill and time-to-first-token; decoding past that is irrelevant).

## 2. Scenarios

All four scenarios drive the real `Scheduler` on the real MLX model and
tokenizer. Each restart is a true fresh Python subprocess on the same
ssd+archive dirs.

| Id | Process        | ssd/archive | Request fields                          | What it measures                                    |
| -- | -------------- | ----------- | --------------------------------------- | --------------------------------------------------- |
| A  | fresh          | fresh       | `session_id="bench-sess-1"`, no restore | cold prefill baseline; commits the manifest         |
| B  | same process as A | same     | no session fields                       | shared prefix cache (same prompt, same engine)      |
| D  | restart        | same        | no session fields                       | restart win from SSD prefix cache only              |
| C  | restart        | same        | `session_id="bench-sess-1"`, `restore=True` | restart win with explicit session restore       |

The comparison that directly answers "does session restore pay for
itself" is **C vs D** — both are restart paths on the same warm SSD
cache; only C uses the session archive.

Driver: [omlx-main/scripts/bench_session_archive.py](../scripts/bench_session_archive.py)
and worker [omlx-main/scripts/_bench_session_archive_worker.py](../scripts/_bench_session_archive_worker.py).
Raw results: [omlx-main/scripts/bench_results_session_archive.json](../scripts/bench_results_session_archive.json).

## 3. Raw numbers (3 reps, min and median)

| Scenario | TTFT min (ms) | TTFT median (ms) | Total min (ms) | Total median (ms) | `block_ids_after_add` |
| -------- | ------------: | ---------------: | -------------: | ----------------: | --------------------: |
| **A** cold                  | 969.13  | 1006.56 | 1027.69 | 1080.87 |  0 |
| **B** warm, same process    | 250.91  |  250.98 |  278.44 |  279.40 | 31 |
| **D** restart, no session   | 369.09  |  404.55 |  397.87 |  435.02 | 31 |
| **C** restart + restore     | 324.36  |  405.05 |  352.81 |  437.59 | 31 |

Deltas against the cold baseline (min-of-3):

| Comparison | TTFT Δ | Total Δ |
| ---------- | -----: | ------: |
| B vs A (same-process shared prefix) | **-74.1%** | **-72.9%** |
| D vs A (restart, SSD prefix only)   | **-61.9%** | **-61.3%** |
| C vs A (restart + session restore)  | **-66.5%** | **-65.7%** |
| **C vs D (session archive marginal)** | **-12.1% (min) / +0.1% (median)** | **-11.3% (min) / +0.6% (median)** |

## 4. Runtime invariant check

Every rep's worker also globs the archive root for any non-JSON files:

```
archive_non_manifest_files: []   # all 3 reps, A/B/D/C
```

That pins Invariant #2 ("manifests only, no payload bytes") at runtime,
not just at unit-test level.

## 5. Verdict on value

**C vs D is where the truth lives.** Both restart on a warm SSD cache;
only C asks the session archive to do anything. The best-case (min of 3
reps) is a 12% TTFT improvement for C; the steady-state (median) is
**0%**. On the prompt shape the user specifically asked about — a
repeated long-prefix workflow — the explicit session restore adds
essentially nothing once the paged SSD prefix cache is in play, because
the prefix cache finds the same 31 blocks by content hash regardless of
the session identity.

That is not a bug; it is a direct consequence of the current `add_request`
ordering: `restore_session` sets `request.block_table` first, then
`block_aware_cache.fetch_cache(...)` overwrites it with its own
`BlockTable` on content-hash match. For an identical-prompt restart the
restore path is effectively transparent.

The feature **does** provide a real operational handle that the paged
SSD cache does not:

- A named session_id that survives restarts and maps back to an ordered
  block manifest without needing the original prompt bytes.
- A point where an operator or higher-level tool can discover, list, or
  drop a specific conversation's blocks without hashing the prompt.

That handle is cheap: a single JSON file per session, atomic rewrite,
metadata-only, no payload duplication.

**Recommendation: keep as-is.** Specifically:

- Keep because the code cost is already minimal (one ~200-line
  metadata-only module, two narrow helpers, two call sites), the design
  invariants pass the audit cleanly, and there is no smaller version of
  this feature worth having.
- Do **not** sell it as a restart-latency feature. For identical-prompt
  restart the SSD prefix cache is doing the real work.
- Do **not** extend it into partial restore or cross-session blending
  without defining and testing that policy first (user's standing
  constraint).

If the operational handle ever turns out to be unused after a few
releases, revisit and drop. Until then the feature's footprint is
small enough that removal is not the best use of time.

## 6. Design invariants audited (code review, April 2026)

Independent read-only audit of the shipped code. All quotes are from the
live tree; nothing below is aspirational.

| # | Invariant                                                          | Status | Evidence |
| - | ------------------------------------------------------------------ | ------ | -------- |
| 1 | `PagedSSDCacheManager` is the sole persisted KV payload authority  | PASS   | [paged_ssd_cache.py save_block / writer loop](../omlx/cache/paged_ssd_cache.py) |
| 2 | `SessionArchiveStore` writes JSON manifests only                   | PASS   | [session_archive.py::commit atomic temp+rename](../omlx/cache/session_archive.py) |
| 3 | Archive paths are namespaced by `(model, session)`                 | PASS   | `_session_dir = root / _slug(model) / _slug(session)` in [session_archive.py](../omlx/cache/session_archive.py) |
| 4 | Restore is explicit and opt-in                                     | PASS   | `Request.__post_init__` guards `restore=True` without session_id; `add_request` uses `getattr(request, "restore", False)` — no ambient restore |
| 5 | `restore_session` raises loudly; no silent fallback                | PASS   | [scheduler.py::restore_session](../omlx/scheduler.py) — no try/except swallowing `SessionArchiveError`, missing blocks raise `"gapped session archive"` |
| 6 | Non-session commit is a true no-op                                 | PASS   | `commit_session` and `_finalize_session_for_request` both early-return when `session_id` is falsy |
| 7 | Manifest write failure cannot corrupt scheduler cleanup            | PASS   | Finish-boundary try/except around `_finalize_session_for_request` in [scheduler.py](../omlx/scheduler.py); failure is logged, cleanup continues |
| 8 | SSD cache rejects mismatched block_hash / checksum                 | PASS   | Hash-identity check + `_quarantine_file()` in [paged_ssd_cache.py](../omlx/cache/paged_ssd_cache.py) |

No dead session helpers, no compat shims from the `GenerationBatch` fix,
no second restore-like call in `add_request`.

## 7. Bug caught during benchmark (and fixed in this pass)

The first bench pass found a real defect that the unit tests had not
pinned: `_hashes_from_block_table` read `request.block_table`, but a
fresh cold turn (no prefix-cache hit) never attaches a block_table to
the request object — the paged cache manager owns the table internally
at `paged_cache_manager.get_block_table(request_id)`. The result was
that `_finalize_session_for_request` silently wrote an empty manifest
for turn A, and turn C could not find the session on restart (it
raised `unknown session`).

Fix: `_hashes_from_block_table` now prefers `request.block_table` and
falls back to `paged_cache_manager.get_block_table(request_id)` when
the request's own table is absent or empty. A new regression test
pins the fallback: `test_commit_falls_back_to_paged_cache_block_table_when_request_has_none`
in [tests/test_engine_turn_wiring.py](../tests/test_engine_turn_wiring.py).

36/36 session-archive tests green after the fix.

## 8. Files changed in this pass

| Path | Change |
| ---- | ------ |
| [omlx/scheduler.py](../omlx/scheduler.py) | `_hashes_from_block_table` now falls back to `paged_cache_manager.get_block_table(request_id)` when the request has no attached block_table. |
| [tests/test_engine_turn_wiring.py](../tests/test_engine_turn_wiring.py) | Added `test_commit_falls_back_to_paged_cache_block_table_when_request_has_none`. |
| [scripts/bench_session_archive.py](../scripts/bench_session_archive.py) | New. Benchmark driver: spawns `AB`, `D`, `C` subprocesses and aggregates min/median across reps. |
| [scripts/_bench_session_archive_worker.py](../scripts/_bench_session_archive_worker.py) | New. Benchmark worker: one scenario per process, returns `RESULT:<json>` on stdout. |
| [scripts/bench_results_session_archive.json](../scripts/bench_results_session_archive.json) | New. Raw benchmark output, 3 reps × 4 scenarios. |
| [docs/session_archive_after.md](./session_archive_after.md) | This file. |

## 9. What stays the way it is

- No new production code beyond the bug fix.
- No narrowing: the audit found nothing redundant to remove.
- No partial restore, no API surface change, no multimodal or hybrid
  cache work.
- The session archive remains metadata-only, opt-in, and fail-loud.
