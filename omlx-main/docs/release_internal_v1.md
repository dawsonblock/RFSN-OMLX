# OMLX Internal Release v1 (0.3.6)

Status: **experimental, internal-only, frozen**.

This note describes the OMLX branch at version `0.3.6` as an internal
release. It is a workspace-lineage and recovery tool for stock
mlx-lm on Apple Silicon. It is not a public release, not a runtime
replacement, and makes no performance claim.

Related:

* [`final_branch_identity.md`](./final_branch_identity.md)
* [`runtime_ownership_table.md`](./runtime_ownership_table.md)
* [`workspace_schema.md`](./workspace_schema.md)
* [`session_archive_ops.md`](./session_archive_ops.md)
* [`pruning_policy.md`](./pruning_policy.md)
* [`operator_quickstart.md`](./operator_quickstart.md)
* [`install_internal.md`](./install_internal.md)

---

## Description

OMLX `0.3.6` layers a named-session workspace store and a portable
export/import format over stock mlx-lm, plus a narrow set of
scheduler-owned execution seams. It is intended for Apple-Silicon
single-box operators who need deterministic session resume and
cryptographically verifiable bundle transfer between hosts.

## Capabilities

1. **Named-session workspaces** with deterministic `commit →
   checkpoint → fork` lineage and integrity-grade vocabulary.
2. **Portable session bundles** — tarballs with per-block sha256,
   exporter git commit, and platform tuple. Round-trippable across
   hosts.
3. **Replay check** that validates a workspace's manifest, block
   integrity, and lineage without re-running prefill.
4. **Conservative retention / pruning** (Pass 6): six frozen classes
   (`stale`, `invalid`, `orphaned`, `exports`, `empty`,
   `unreadable`), structured plan/execute separation, pinning of
   both workspaces and bundles.
5. **Runtime observability counters** — prefill chunks, decode steps,
   ownership-seam entry/exit, scheduler entry gate.
6. **Scheduler-owned execution seams** — decode-entry gate (env
   gated), local abort / length-cap normalization on the gate, EOS
   stop-token override, scheduler-level prefill orchestration with
   per-chunk abort and counters.
7. **Admin CLI** (`scripts/session_archive_admin.py`) for inspect,
   export, import, replay-check, prune (dry-run + confirm), pin,
   unpin.

## Non-goals (explicit, frozen)

* Not a runtime replacement for mlx-lm. Per-layer compute, KV cache
  update/fetch, RoPE bookkeeping, attention mask construction, and
  hybrid/SSM caches are delegated to stock mlx-lm.
* Not a latency, throughput, or memory-footprint feature. No
  performance claim, no shipped benchmark that shows a speedup.
* Not a public API. Python, CLI, and admin script surfaces are
  internal.
* Not a UI. No frontend.
* Not a multimodal or hybrid-cache restore story. Vision, audio,
  and SSM/hybrid caches are not exportable via bundles.

## Known limitations

* Apple Silicon (macOS arm64) single-box only. No cross-platform
  support claim.
* Python **exactly** `3.10.12`. Bootstrap enforces this.
* mlx-lm is pinned to a specific git commit (see *Dependency pin*
  below). Upgrading invalidates the ownership claims in
  [`final_branch_identity.md`](./final_branch_identity.md) until
  Pass 4 proof tests pass again.
* Bundles do not export vision/audio tensors or hybrid/SSM state.
  Importing on the other side restores text-only lineage.
* `0.3.6` is an internal identifier; there is no stable public
  versioning guarantee.

## Dependency pin

mlx-lm is pinned to git commit
`dcbf6e33d135a1b7c6767ca0fe7ebbd23df814a7` (post-0.31.1). This pin
is an **API requirement**, not environmental drift: the Gemma 4
tool-call parser and multi-token think/tool paths require this
commit. The exact line lives in
[`pyproject.toml`](../pyproject.toml) (`mlx-lm @ git+…@dcbf6e33…`).
The install path validates the pin via
`direct_url.json` `vcs_info.commit_id`; see
[`install_internal.md`](./install_internal.md).

## Use cases (what this is for)

* Reproducible named-session resume: replay a conversation
  turn-for-turn after a mid-session failure without re-running
  prefill.
* Workspace export/import between two Apple-Silicon hosts with
  provable provenance (per-block sha256, exporter commit, platform
  tuple).
* Stable integrity vocabulary for operator tooling and scripts.
* Conservative on-disk cleanup of stale/invalid/orphaned
  workspaces and bundles, with pinning for long-lived artefacts.

## Explicit non-uses (what this is not for)

* Serving production traffic to end users.
* Demonstrating mlx-lm speedups or reduced memory use.
* Cross-platform deployment (Linux/CUDA/Windows).
* Multimodal conversation restore.
* Anything that depends on the CLI or Python API remaining stable
  across versions.

## Safety notes

### Pruning

* Pruning is **dry-run by default** (`--confirm` is required to
  delete).
* Pinned workspaces and pinned bundles are protected. The
  `--include-pinned` flag is required to even consider them, and
  the underlying reason is preserved so a subsequent unpin behaves
  as expected.
* Retention windows are frozen: `HEALTHY_RECENT_DAYS=30`,
  `HEALTHY_STALE_DAYS=90`, `INVALID_GRACE_DAYS=7`,
  `ORPHANED_GRACE_DAYS=14`, `BUNDLE_RETENTION_DAYS=21`.
* `touch_last_used` and `set_pinned` do **not** move
  `updated_at`; lineage is preserved.

### Export / import

* Bundles are content-addressed per block (sha256). Importer
  rejects any block whose hash does not match.
* Bundle sidecar `<path>.pinned` protects a bundle from pruning
  without modifying tarball bytes.
* Vision, audio, and hybrid/SSM state are not exported; operators
  must not assume multimodal restore.

### Schema compatibility

* `MANIFEST_VERSION="2"`, `SUPPORTED_MANIFEST_VERSIONS=("1","2")`,
  `BUNDLE_VERSION="1"`, `_CACHE_LAYOUT="paged-ssd-safetensors/v1"`.
* Pass 6 added optional fields `pinned` and `last_used_at`. The
  schema validator already tolerated extra keys; old manifests
  load and round-trip unchanged.

## Focused validation

The canonical focused-test command set lives in
[`install_internal.md`](./install_internal.md). Running it from a
clean bootstrap must be green before this release is considered
usable on a new host.

## Cross-references

* Operator workflow: [`operator_quickstart.md`](./operator_quickstart.md)
* Install + verification: [`install_internal.md`](./install_internal.md)
* Ownership boundaries: [`runtime_ownership_table.md`](./runtime_ownership_table.md)
* Schema: [`workspace_schema.md`](./workspace_schema.md)
* Retention rules: [`pruning_policy.md`](./pruning_policy.md)
* Admin verbs: [`session_archive_ops.md`](./session_archive_ops.md)
