# Runtime Ownership Table (Pass 5 — canonical)

This table is the canonical statement of what OMLX runtime code owns
versus what it delegates to stock mlx-lm. If any other doc disagrees
with this table, **this table wins**.

Related:

* [`workspace_schema.md`](./workspace_schema.md) — frozen on-disk schema.
* [`final_branch_identity.md`](./final_branch_identity.md) — branch
  scope statement.
* [`executor_seam_pass4_final.md`](./executor_seam_pass4_final.md) —
  the stop-and-report that closed further runtime replacement work.

All file:line citations point at `omlx-main/`.

---

## Owned seams

| Seam | Status | File:Line | Proof test | Benchmark | Limits / gate |
|---|---|---|---|---|---|
| Workspace lineage & on-disk schema | Owned | `omlx/cache/session_archive.py`, `omlx/cache/session_archive_portable.py`, `omlx/cache/session_archive_retention.py` | `test_session_archive_*`, `test_workspace_lineage.py`, `test_session_archive_schema_contract.py` | — | No runtime replacement; manifest/bundle only. |
| Runtime observability counters | Owned | `omlx/runtime_metrics.py`, `omlx/server_metrics.py` | `test_runtime_metrics.py`, `test_server_metrics.py` | — | Observability only; no behaviour changes. |
| Scheduler decode-entry gate | Owned | `omlx/scheduler.py:3874` (`Scheduler._run_owned_decode_step`) | `test_scheduler.py::TestExecutorBoundaryOwnership::*` | `docs/executor_seam_benchmark.md` | Gated by env `OMLX_EXECUTOR_BOUNDARY_MODE` (`scheduler.py:3840`); decode-only. |
| Local abort / length-cap normalization | Owned | `omlx/scheduler.py:3896-3919` (inside `_run_owned_decode_step`) | `test_scheduler.py::TestExecutorBoundaryOwnership::*` | `docs/executor_seam_benchmark.md` | Same gate as above. |
| EOS stop-token override | Owned | `omlx/scheduler.py:3958-3966` via `_get_stop_tokens` at `:892` | `test_scheduler.py::TestEosStopOwnership::*` | `docs/executor_seam_pass3_benchmark.md` | — |
| Scheduler-level prefill orchestration | Owned | `omlx/scheduler.py:1194` (`Scheduler._do_external_prefill`) + `:1140` (`Scheduler._run_owned_prefill_step`) | `test_scheduler.py::TestPrefillSeamOwnership::*`, `test_prefill_seam_pass4.py::*` | `docs/executor_seam_pass4_benchmark.md` | Orchestration only; per-layer compute delegated (see below). |
| Pre-chunk abort gate & per-chunk counters | Owned | `omlx/scheduler.py:1140` region | `test_prefill_seam_pass4.py::*` | `docs/executor_seam_pass4_benchmark.md` | — |

## Delegated (explicitly out of scope as of Pass 4 final)

| Responsibility | Delegated to | File:Line |
|---|---|---|
| Per-layer forward pass | stock `Model.__call__` | `omlx/scheduler.py:1186` (call site) |
| KV cache update/fetch inside attention | stock `KVCache.update_and_fetch` | mlx-lm internal |
| RoPE offset bookkeeping | stock `Attention.__call__` | mlx-lm internal |
| Attention mask construction | stock mlx-lm | mlx-lm internal |
| Hybrid / SSM state caches | stock mlx-lm | mlx-lm internal |
| Decode compute kernel | `BatchGenerator.next_generated()` | `omlx/scheduler.py:3942` |

Anything not listed in the **Owned seams** section is stock-delegated.
Further replacement requires a per-architecture model fork and is
explicitly out of scope (see
[`executor_seam_pass4_final.md`](./executor_seam_pass4_final.md) §7).
