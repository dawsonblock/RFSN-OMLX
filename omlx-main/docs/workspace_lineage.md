# Workspace Lineage — semantics for the OMLX branchable local agent workspace

> **Status:** experimental / internal.
> **Scope:** Apple-Silicon single-box deployments of OMLX. This document
> locks vocabulary, invariants, and operator policy. The implementation
> lives in `omlx/cache/session_archive*.py` and is driven by
> `scripts/session_archive_admin.py`. The internal code noun is
> "session archive"; the operator-facing noun is **workspace**.

---

## 1. What this is (and is not)

OMLX records an append-only lineage of KV-block references per logical
session. Operators can list, fork, diff, replay-check, export, and
import these lineages. The shipped surface is a **branchable local
agent workspace** — a tool that lets a long-running local inference
task branch, resume after a restart, and move between two machines,
all under explicit operator action.

This is **not**:

- a latency feature,
- a UI,
- a public HTTP / JSON-RPC API,
- a multimodal snapshot,
- a hybrid-cache restore layer,
- a performance claim.

---

## 2. Vocabulary

| Term | Definition |
| --- | --- |
| **Workspace** | One logical agent task, identified by `(model_name, session_id)`. One workspace has one manifest file on disk and may carry compact human metadata such as `label`, `description`, and `task_tag`. |
| **Turn** | An immutable commit of a KV-block-hash list onto a workspace. Turns are numbered `t-00001`, `t-00002`, … and never renamed. A turn may carry a short operator `note`; branch turns may also carry a `branch_reason`. |
| **Head** | The most recent turn of a workspace; the one `load()` returns. Stored in `head_turn_id`. |
| **Branch / Fork** | A new workspace created from a parent workspace at a specific parent turn. Records `parent = (parent_session_id, parent_turn_id)`. |
| **Ancestry chain** | The list of `(session_id, turn_id)` pairs walked from a workspace up to a root by following `parent` links. |
| **Replay check** | A read-only probe that, for a given turn, confirms every referenced KV block is still present in the paged SSD cache. Touches no tensor bytes. |
| **Compatibility family** | The `(model_name, block_size, schema)` tuple recorded in `model_compat`. Two workspaces are compatible iff their families match. |
| **Integrity grade** | One of seven canonical labels (see §5). Every operator output that describes a workspace's health uses one of these labels. |

---

## 3. On-disk shape

Each workspace manifest is a single JSON file:
`<archive_root>/<slug(model_name)>/<slug(session_id)>/manifest.json`.

Schema v2:

```json
{
  "version": "2",
  "model_name": "...",
  "session_id": "...",
  "label": "...|null",
  "description": "...|null",
  "task_tag": "coding.parser|nullable",
  "created_at": 1713500000.0,
  "updated_at": 1713500000.0,
  "head_turn_id": "t-00002",
  "parent": {"session_id": "...", "turn_id": "..."} | null,
  "model_compat": {"model_name": "...", "block_size": 16, "schema": "2"},
  "turns": [
    {"turn_id": "t-00001", "committed_at": 1.0, "block_hashes": ["<hex>", ...], "note": "...", "branch_reason": "...|null"}
  ]
}
```

Schema v1 is still loadable; it is auto-upgraded in memory on read and
written as v2 on the next commit.

---

## 4. Invariants (policy)

### 4.1 Turns are immutable
Once committed, a turn's `turn_id`, `committed_at`, and `block_hashes`
never change. A failed request does not mutate the existing head.

### 4.2 Head must resolve
`head_turn_id` MUST match exactly one turn in the `turns` array. A
manifest whose `head_turn_id` does not resolve is rejected with
`malformed manifest: head_turn_id=... does not match any recorded turn`.

### 4.3 Empty workspace is not "malformed"
A workspace with `turns=[]` is an **empty workspace** (produced by
`init_workspace` / CLI `create`). `load()` raises
`SessionArchiveError("empty session archive ...")` — the distinct
error vocabulary that was present before the reframe is preserved.

### 4.4 Fork refuses overwrite
`fork` into an existing non-empty destination is refused unless
`overwrite=True`. The new workspace records `parent =
(src_session_id, src_turn_id)` and may record a compact `branch_reason`
explaining *why* the branch was made.

### 4.4a Human metadata stays bounded
`label`, `description`, `task_tag`, `note`, and `branch_reason` are all
optional, compact, and validated. The archive does **not** store full
transcripts, prompts, or payload bytes.

### 4.5 Compatibility family is load-bearing
Operators can pin it at import time with `--expected-model-name` and
`--expected-block-size`. A mismatch raises `BundleError` before any
bytes hit the destination SSD or archive.

### 4.6 Replay check is metadata-only
`replay_check()` probes block presence on the SSD cache but never
reads, writes, or decodes tensor bytes. When `expected_model_name` is
supplied and differs from the manifest, the grade is
`incompatible_model` with zero SSD probes.

### 4.7 Prune eligibility is explicit
`prune` without at least one of `--invalid`, `--older-than`, or
`--max-per-model` selects nothing and deletes nothing. Dry-run is the
default; deletion needs `--no-dry-run`.

### 4.8 Branch ancestry crosses archive boundaries
`export-session` records the workspace's `parent` pointer but does
**not** recursively pack parent workspaces into the bundle. Importing
a branch into a fresh archive produces a valid, loadable workspace
whose `parent` points at a session that does not exist there.
`ancestry_chain` handles this gracefully: the walk stops at the first
unreachable parent and the caller sees the dangling reference at the
tail.

### 4.9 Import conflict policy is conservative by default
`import-session` validates compatibility and SHA-256 integrity **before**
mutating archive state. If the destination session already exists, the
default policy is to **fail**. Operators may explicitly choose one of:

- `--fail-if-exists` — explicit no-overwrite policy (same as default),
- `--rename-on-conflict` — deterministic rename to `<session>-imported-N`,
- `--overwrite` — replace the existing destination only with explicit intent,
- `--re-root-lineage` — clear the imported `parent` pointer rather than preserve external ancestry.

---

## 5. Integrity grades

Seven canonical labels, shared across replay-check, retention/prune,
CLI `validate`, CLI `status` / `resume`, and export/import:

| Label | Meaning |
| --- | --- |
| `healthy` | Manifest loads, head resolves, all probed blocks present. |
| `stale` | Healthy but `updated_at` older than the configured stale threshold. Not deleted automatically. |
| `invalid_manifest` | JSON parse error, schema mismatch, or head that does not resolve. |
| `missing_blocks` | Manifest loads but at least one head block is absent from the SSD cache. |
| `incompatible_model` | `model_name` (or `--expected-block-size`) does not match operator expectation. |
| `unreadable` | Manifest path missing, unreadable, or empty-workspace sentinel. |
| `partially_exportable` | Export produced a bundle with known-missing blocks (only via `--allow-missing-blocks`). |

Every operator command that describes a workspace emits a `grade\t<label>`
line using exactly these strings.

---

## 6. Canonical operator workflow

```
create → commit … → fork before risky change → diff → validate → export-session → inspect-bundle → import-session → resume
```

1. **create** an empty coding workspace (optional — `commit` also creates).
2. **commit** appends a turn; each turn can carry a short note like
   "baseline checkpoint" or "after parser fix attempt".
3. **fork** branches at a chosen turn and may record a human-readable
   `branch_reason` such as "before risky refactor".
4. **diff** compares two workspaces by turn.
5. **validate** loads every workspace and (with `--ssd-cache-dir`)
   probes block presence.
6. **export-session** writes a tarball bundle with SHA-256 per block and metadata-only provenance.
7. **inspect-bundle** shows provenance (`source_label`, `task_tag`, `git_commit`, platform, compatibility family) without mutating anything.
8. **import-session** verifies all SHAs before a single byte lands and applies the explicit conflict policy.
9. **resume** prints the status block plus "next steps" hints.

See `scripts/workspace_demo.sh` for a real end-to-end run.

---

## 7. What is explicitly *not* coupled

- **Scheduler.** The scheduler only calls `restore_session` when
  `request.restore` is set, and `commit_session` only when
  `session_id` is set. Non-session traffic bypasses both paths.
- **HTTP / JSON-RPC API.** None. This is an operator CLI + Python
  module. If an API is ever added it will be a separate layer.
- **Background services.** There are none. Prune, validate, import,
  export, replay-check all run only when an operator runs them.
- **Tensor payloads.** No path mutates KV bytes except the explicit
  `import-session` bundle extraction, which verifies SHA-256 first.

---

## 8. Further reading

- `docs/session_archive_ops.md` — operator reference for every verb.
- `docs/session_archive_after.md` — design journal.
- `tests/test_workspace_lineage.py` — contract tests for the verbs
  this document names.
- `tests/test_session_archive_hardening.py` — contract tests for the
  invariants in §4.
