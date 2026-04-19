# Runtime Replacement Assessment

> Status: blocker-first assessment for whether this branch can evolve from an operator/recovery layer into a real runtime spine.
>
> Scope of this document: scheduler ownership, request lifecycle ownership, cache/KV ownership, execution-loop ownership, and benchmark ownership. This is **not** a product/UI plan.

## Short answer

**Conditionally viable, but not yet a replacement runtime.**

The codebase already owns meaningful pieces of the runtime path:
- request objects and request admission,
- queueing and scheduling policy,
- paged block metadata,
- SSD spill/prefix cache policy,
- engine lifetime and streaming.

However, the actual decode/prefill execution loop is still structurally anchored to `mlx_lm.generate.BatchGenerator` and a set of MLX-specific patches. Today this branch is still **mostly a layer above** the real executor, not a fully separate engine.

---

## 1. What oMLX still fully owns in the runtime path

### Authoritative runtime pieces today

| Runtime concern | Current owner | Evidence from code | Replacement status |
| --- | --- | --- | --- |
| Request intake | `EngineCore.add_request()` and server handlers | `omlx/engine_core.py`, `omlx/server.py` | Replaceable only by changing the engine ingress path |
| Request state | `Request` and `RequestStatus` | `omlx/request.py` | Partially replaceable |
| Scheduling queue | `Scheduler.waiting`, `Scheduler.running`, `Scheduler.step()` | `omlx/scheduler.py` | Replaceable in-place |
| Continuous batching | `mlx_lm.generate.BatchGenerator` via the scheduler | `omlx/scheduler.py` | **Hard blocker** |
| Prompt/decode stepping | `BatchGenerator.next_generated()` plus MLX model calls | `omlx/scheduler.py` | **Hard blocker** |
| Block metadata / residency | `PagedCacheManager` | `omlx/cache/paged_cache.py` | Replaceable with care |
| SSD spill/persisted KV bytes | `PagedSSDCacheManager` | `omlx/cache/paged_ssd_cache.py` | Replaceable with care |
| Prefix reuse policy | `BlockAwarePrefixCache` | `omlx/cache/prefix_cache.py` | Replaceable in-place |
| Recovery metadata | `SessionArchiveStore` | `omlx/cache/session_archive.py` | Useful but not runtime-authoritative |
| Engine lifetime / async loop | `EngineCore` / `AsyncEngineCore` | `omlx/engine_core.py` | Replaceable at the seam |
| Benchmark ownership | mostly ad hoc scripts | `scripts/bench_session_archive.py` | Previously weak; tightened in this pass |

### Bottom line

oMLX still owns the core executor path through the scheduler, the engine loop, and the MLX/BatchGenerator integration. The workspace-lineage layer does **not** currently own execution.

---

## 2. What is actually replaceable from this branch without tearing the system apart

### Replaceable now

- **Request lifecycle accounting and truth surface**
  - Request admission, prefill timing, first-token timing, finish timing
  - This pass adds an internal authoritative recorder for those events
- **Scheduler policy and queueing behavior**
  - FCFS ordering, rescheduling, recovery policy, abort handling
- **Cache/block policy**
  - Block size, block residency decisions, prefix sharing heuristics, release-for-eviction behavior
- **Recovery and spill policy**
  - Session restore / commit hooks, replay validation, restart benchmarking
- **Benchmark ownership**
  - The repo can now benchmark a runtime slice directly against stock behavior

### Not realistically replaceable yet without deeper surgery

- **The decode loop itself** (`BatchGenerator.next_generated()`)
- **The prefill execution core** (still MLX model calls + BatchGenerator contract)
- **The tokenizer/model adapter assumptions** spread across scheduler, engine, and patches
- **Patch-driven model behavior** for grammar, mRoPE, TurboQuant, SpecPrefill, etc.

---

## 3. Workspace-lineage pieces that matter to a future runtime vs. operator-only features

### Useful to a future runtime

- `SessionArchiveStore` as a **recovery metadata log**
- integrity grades and replay validation for restart truth
- portable import/export only as a controlled recovery boundary
- benchmark scenarios that measure restart and restore costs

### Operator-only / not runtime ownership

- workspace labels/descriptions/task tags
- CLI `fork`, `diff`, `status`, `resume`, `export-session`, `import-session`
- branch ancestry presentation
- provenance-oriented bundle inspection

Those should remain clearly subordinate to the runtime spine, not peers of it.

---

## 4. Hard blockers to replacing oMLX’s runtime spine

### P0 blockers

1. **Decode-loop ownership is external**
   - The scheduler still depends on `mlx_lm.generate.BatchGenerator` for generation stepping.
2. **Prefill semantics are intertwined with MLX patching**
   - Grammar, mRoPE, boundary snapshots, and special cache behavior are patched around the current executor.
3. **Cache authority is split across multiple layers**
   - `BlockAwarePrefixCache`, `PagedCacheManager`, and `PagedSSDCacheManager` each own part of the truth.
4. **No single runtime truth surface existed until this pass**
   - TTFT, prefill, decode, and restore cost were not all owned in one place.

### P1 blockers

5. **Request lifecycle is spread across server, engine, scheduler, and collectors**
6. **Model adapter boundary is not singular**
   - loading, generation, tool parsing, and output shaping are mixed across modules
7. **Benchmark story was previously too narrow**
   - focused on session archive value, not on replacement-runtime viability

---

## 5. Is this structurally capable of becoming a runtime?

### Assessment

**Yes, but only incrementally.**

The code is **not fundamentally just a decorative layer**. The scheduler, request objects, cache managers, and engine loop already form a spine. What is missing is authoritative ownership of execution and a single runtime boundary around the MLX generator.

### Recommendation

**Viable — continue only if the project is willing to displace or encapsulate the current `BatchGenerator` dependency over time.**

If that work will not happen, the honest answer is to stop and keep this repo as a workspace-lineage / operator-recovery layer only.

---

## First implementation slice added in this pass

This pass intentionally chooses the smallest slice that increases real runtime ownership:

- **request lifecycle observability is now owned by the runtime path**, via `omlx/runtime_metrics.py`
- the scheduler now records:
  - request admission,
  - cache reuse at intake,
  - restore timing and result,
  - prefill timing,
  - first-token timing,
  - finish timing,
  - batch-size behavior
- a direct stock-vs-branch benchmark now exists to test whether the branch owns anything meaningful yet

That is not a new engine. It is the minimum truthful seam required before larger replacement work.
