# Runtime Replacement Target

> One target runtime spine. One request lifecycle. One cache authority. One benchmark story.

This document defines the target architecture **if** runtime replacement continues.

---

## Target verdict

The target should be a **single authoritative runtime spine** rooted in the scheduler path — not a second parallel engine pretending to be equal.

## Runtime spine

```text
Server / Engine ingress
        ↓
EngineCore (single intake path)
        ↓
Scheduler (single owner of lifecycle + batching policy)
        ↓
ModelExecutionAdapter (single executor boundary)
        ↓
PagedCacheManager (single block authority)
        ↓
PagedSSDCacheManager (single persisted KV authority)
```

---

## Authoritative components

### 1. Request intake
**Authority:** `EngineCore.add_request()`

Rules:
- one ingress path for text/VLM requests
- request admission timestamps recorded here or immediately in the scheduler
- no side entry that bypasses scheduler truth

### 2. Request state
**Authority:** `Request` + the runtime lifecycle recorder

Rules:
- `Request` carries execution-relevant state
- the lifecycle recorder owns phase timing and outcomes
- no second request-state system with conflicting truth

### 3. Batching / scheduling
**Authority:** `Scheduler`

Rules:
- waiting/running/finished sets live here
- prefill vs decode ordering lives here
- abort/reschedule/recovery lives here
- if replacement continues, this is the module that must eventually own the execution policy end-to-end

### 4. KV / cache / block management
**Authority:** `PagedCacheManager` for block metadata and residency

Rules:
- block allocation, reference counts, block-table lifecycle, and eviction state belong here
- `BlockAwarePrefixCache` should remain a lookup façade, not a second authority

### 5. Spill / restore policy
**Authority:** `PagedSSDCacheManager` for persisted block bytes, `Scheduler` for restore policy

Rules:
- one persisted KV authority only
- `SessionArchiveStore` remains metadata for recovery and validation, not a parallel KV owner

### 6. Model execution adapter
**Authority:** one adapter boundary between scheduler policy and MLX execution

Current reality:
- `BatchGenerator` is still the executor

Target:
- keep one adapter seam that can later encapsulate or replace `BatchGenerator`
- do not let execution behavior remain scattered across patches and helpers forever

### 7. Metrics and tracing
**Authority:** `RuntimeMetricsRecorder`

Rules:
- TTFT, prefill, decode, total latency, batch size, and restore cost come from one source
- benchmark scripts read this source instead of inferring everything from ad hoc wall clocks

### 8. Shutdown / recovery behavior
**Authority:** `EngineCore.stop()` + `Scheduler.shutdown()`

Rules:
- one shutdown path
- one recovery path
- session/workspace metadata remains recovery support only

---

## First owned slice chosen now

This pass chooses:

## **Request lifecycle + benchmark ownership**

Why this slice first:
- it is real runtime ground truth
- it reduces ambiguity immediately
- it supports blocker-first decisions
- it is small enough not to destabilize execution correctness

What is now owned:
- request admission timing
- prefill timing
- TTFT timing
- decode completion timing
- restore attempt/success timing
- batch-size observations
- stock-vs-branch benchmark evidence

What is **not** yet owned:
- the decode loop implementation
- model stepping semantics
- full block spill policy replacement

---

## What should happen next if replacement continues

1. **Keep one scheduler** — do not add a parallel runtime path.
2. **Encapsulate the executor boundary** around `BatchGenerator`.
3. **Narrow cache authority** so block truth and SSD truth are explicit and singular.
4. **Use the benchmark as the gate** for any future replacement claim.
5. **Delete ornamental runtime-looking code** if it is not actually controlling execution.

---

## Recommendation

**Continue runtime replacement only through the scheduler/request/cache spine.**

Do not expand workspace-lineage surface unless it directly supports runtime recovery or restart truth.
