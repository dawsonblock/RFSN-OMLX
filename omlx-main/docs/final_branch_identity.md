# Final Branch Identity (Pass 5)

Clinical statement of what the `RFSN-OMLX` branch is as of Pass 5.
This note is intentionally short. No marketing language, no future
work promises.

Related:

* [`workspace_schema.md`](./workspace_schema.md)
* [`runtime_ownership_table.md`](./runtime_ownership_table.md)
* [`executor_seam_pass4_final.md`](./executor_seam_pass4_final.md)

---

## What this branch is

An internal, single-box, Apple-Silicon-targeted **workspace-lineage
and recovery tool** for OMLX, layered over stock mlx-lm. It adds:

* named-session manifests with deterministic turn/fork semantics;
* portable, sha256-verified session bundles for export/import;
* a stable integrity-grade vocabulary for operator tooling;
* runtime observability counters (prefill chunks, decode steps,
  ownership-seam entry/exit);
* a narrow set of **scheduler-owned** execution seams — see
  [`runtime_ownership_table.md`](./runtime_ownership_table.md).

## What this branch is not

* **Not** a full runtime replacement for mlx-lm.
* **Not** a latency, throughput, or memory-footprint feature. It
  makes no performance claim and ships no benchmark that shows a
  speedup.
* **Not** a public API. All surfaces (Python, CLI, admin script) are
  internal.
* **Not** a UI. There is no frontend.
* **Not** a multimodal or hybrid-cache restore story. Vision,
  audio, and SSM/hybrid caches are delegated to stock mlx-lm and
  are not exportable via bundles.

## What it owns

(Mirrors the Owned section of
[`runtime_ownership_table.md`](./runtime_ownership_table.md).)

* Workspace lineage + on-disk schema + portable bundles.
* Runtime observability counters.
* Scheduler decode-entry gate (env-gated).
* Local abort / length-cap normalization on that gate.
* EOS stop-token override.
* Scheduler-level prefill orchestration, pre-chunk abort gate, and
  per-chunk counters.

## What it delegates

(Mirrors the Delegated section of
[`runtime_ownership_table.md`](./runtime_ownership_table.md).)

* Per-layer forward (`Model.__call__`).
* KV cache update/fetch inside attention.
* RoPE offset bookkeeping.
* Attention mask construction.
* Hybrid / SSM state caches.
* Decode compute kernel (`BatchGenerator.next_generated`).

## Why runtime replacement stops here

Crossing the next boundary (per-layer compute or KV cache update)
would require a per-architecture model fork: OMLX would have to carry
its own copy of each supported model class and keep it in lockstep
with upstream mlx-lm. The cost/benefit was evaluated in Pass 4 and
rejected. See
[`executor_seam_pass4_final.md`](./executor_seam_pass4_final.md) §7.

No further executor seams are planned. Earlier seam-proposal docs
(`docs/executor_seam_target.md`,
`docs/next_executor_seam_*.md`,
`docs/executor_seam_pass3_target.md`) are historical and carry a
superseded footer pointing here.

## Who this is for

Operators running Apple-Silicon OMLX single-box deployments who need:

1. reproducible **named-session resume** (you can replay a
   conversation turn-for-turn);
2. **workspace export/import** with cryptographically verifiable
   provenance (sha256 per block, git commit of the exporter, platform
   tuple);
3. a stable **integrity vocabulary** their scripts can filter on.

## Why it is still worth keeping

Even without a runtime-replacement story, the owned surface is
useful as a primitive:

* Named workspaces + replay-check give deterministic recovery from
  mid-session failures without re-running prefill.
* Portable bundles with per-block sha256 let two hosts share a
  conversation state provably and without trust.
* The scheduler's owned seams give deterministic decode lifecycle
  control (env-gated entry, local abort, EOS override) and observable
  prefill/decode counters — enough for operational triage.

## Verification

This is the exact command set that proves the stabilization claims
in this branch.

```bash
cd omlx-main
.venv/bin/python -m pytest \
  tests/test_session_archive_schema_contract.py \
  tests/test_session_archive_failure_modes.py \
  tests/test_session_archive_hardening.py \
  tests/test_session_archive_lineage.py \
  tests/test_session_archive_admin_cli.py \
  tests/test_session_archive_store.py \
  tests/test_workspace_lineage.py \
  tests/test_workspace_demo.py \
  tests/test_paged_ssd_cache.py \
  tests/test_ssd_cache_integrity.py \
  tests/test_scheduler.py \
  tests/test_prefill_seam_pass4.py \
  tests/test_runtime_metrics.py \
  tests/test_non_regression_shared_cache.py \
  -x
```

The mlx-lm version this branch is pinned against is recorded in
[`pyproject.toml`](../pyproject.toml) (search for the `mlx-lm @ git+`
line). Upgrading that pin invalidates the ownership claims in this
document until Pass 4's proof tests are re-run and pass.

## Source-surface pruning check

`grep -rE "next seam|future seam|pass 5|upcoming|runtime replacement|full runtime|TODO|FIXME" omlx/`
returns zero hits. The source tree carries no pointer to future
replacement work; overclaiming language lives only in historical docs
(which now carry a superseded footer).

## Recommendation

**Merge as an experimental / internal workspace-lineage and recovery
tool**, contingent on the verification command above passing green in
CI. Do not market as a runtime replacement. Do not advertise
performance. The operator primitives above stand on their own.
