# Pruning policy

Status: **experimental / internal**. This document is the canonical
reference for how the session-archive prune surface decides what to
keep and what to delete. The policy is conservative by design: **a
workspace is never deleted by accident**.

This is not a feature of the request path. It runs only when an
operator explicitly invokes the admin CLI, and destructive execution
always requires both an opt-in prune class and a `--confirm` flag.

---

## 1. Principles

1. **Healthy workspaces are kept.** A successfully loaded manifest with
   no missing blocks is never eligible for deletion in the recent
   window.
2. **Broken metadata can be cleaned up, but not immediately.** Invalid,
   unreadable, and empty manifests live through a grace window so
   transient failures are not mistaken for orphans.
3. **Portable bundles have their own retention window.** Bundles are
   operator-exported artefacts — they are *not* governed by workspace
   retention and they carry their own pin mechanism (a sidecar file).
4. **Deletion requires clear intent.** Every destructive verb needs a
   prune-class opt-in AND `--confirm`; absence of either produces a
   dry-run plan.
5. **Dry-run output is trustworthy.** Candidates are grouped by reason,
   protected rows are shown with why they were protected, and the
   latest healthy head per model is always visible.

---

## 2. Retention windows

All windows are deliberately coarse so operators can reason about them
without a calculator. Windows are defined in
[`omlx/cache/session_archive_retention.py`](../omlx/cache/session_archive_retention.py)
as frozen constants.

| Constant                 | Days | Applies to                                            |
|--------------------------|------|--------------------------------------------------------|
| `HEALTHY_RECENT_DAYS`    | 30   | Healthy workspaces younger than this are **never** eligible. |
| `HEALTHY_STALE_DAYS`     | 90   | Healthy workspaces older than this are graded `stale`. |
| `INVALID_GRACE_DAYS`     | 7    | Invalid / unreadable / empty manifests must age past this before becoming eligible. |
| `ORPHANED_GRACE_DAYS`    | 14   | Manifest-less session directories must age past this before becoming eligible. |
| `BUNDLE_RETENTION_DAYS`  | 21   | Portable bundles older than this become eligible for `--prune-exports`. |

Freshness for healthy workspaces is measured from `last_used_at` when
present, falling back to the manifest file's mtime. `last_used_at` is
refreshed on successful `replay_check`, `export_session`, and
`import_session`; it does **not** move on plain `commit()` writes, so
an operator can distinguish "still useful" from "recently modified".

---

## 3. Prune classes

The structured prune layer requires the operator to opt into specific
classes of candidates. Classes are independent — passing one does not
imply another.

| CLI flag             | Token        | Selects                                                                                  |
|----------------------|--------------|-------------------------------------------------------------------------------------------|
| `--prune-stale`      | `stale`      | Healthy workspaces older than `HEALTHY_RECENT_DAYS` (graded `stale`).                     |
| `--prune-invalid`    | `invalid`    | Manifests graded `invalid_manifest` or `missing_blocks`, past `INVALID_GRACE_DAYS`.       |
| `--prune-empty`      | `empty`      | Manifests with no head / no turns, past `INVALID_GRACE_DAYS`.                             |
| `--prune-unreadable` | `unreadable` | Manifests that fail to load for I/O or JSON reasons, past `INVALID_GRACE_DAYS`.           |
| `--prune-orphaned`   | `orphaned`   | Session directories with no `manifest.json`, past `ORPHANED_GRACE_DAYS`.                  |
| `--prune-exports`    | `exports`    | Portable bundle files under `--bundle-dir` older than `BUNDLE_RETENTION_DAYS`.            |

Candidates whose class was not requested are **omitted** from the
plan entirely. The only rows shown without an opt-in are `pinned` and
`protected_latest_head`, and only as protection notices.

---

## 4. Protections

The following candidates are always `protected` regardless of class:

1. **Healthy workspaces younger than `HEALTHY_RECENT_DAYS`.**
2. **The latest healthy head per model**, where "latest" is the
   session with the most recent `updated_at` whose manifest loads
   successfully and has at least one turn. This guarantees at least
   one revivable checkpoint survives every prune pass.
3. **Pinned workspaces** (`manifest.pinned == true`).
4. **Pinned bundles** (sidecar file `<bundle>.pinned` exists).
5. **Invalid / orphaned rows still inside their grace window.**

Pinning can be toggled with `session_archive_admin pin` /
`session_archive_admin unpin`:

```
pin --model <m> --session <s>        # workspace
pin --bundle path/to/bundle.tar      # bundle
```

`--include-pinned` lifts protection (3) and (4) **only when combined
with `--confirm`** and a matching `--prune-*` class. It never lifts
(1), (2), or (5).

---

## 5. Reason vocabulary

Every row in a `PrunePlan` carries one of the following reasons
(defined as frozen constants in
[`omlx/cache/session_archive_retention.py`](../omlx/cache/session_archive_retention.py)):

| Reason                        | Kind       | Action (default)   |
|-------------------------------|------------|--------------------|
| `healthy_recent`              | workspace  | `protected`        |
| `healthy_stale`               | workspace  | `eligible` (stale) |
| `invalid_manifest`            | workspace  | `eligible` after grace |
| `unreadable_manifest`         | workspace  | `eligible` after grace |
| `empty_archive`               | workspace  | `eligible` after grace |
| `orphaned`                    | workspace  | `eligible` after grace |
| `export_bundle_old`           | bundle     | `eligible` after 21d |
| `pinned`                      | either     | `protected`        |
| `protected_latest_head`       | workspace  | `protected`        |

The vocabulary is **closed** — callers must not invent new reasons.

---

## 6. Workflow

```
# 1. Plan only (dry-run, no deletion).
session_archive_admin --archive-root /var/omlx/wspaces \
  prune --model M --prune-stale --prune-invalid --prune-orphaned \
        --prune-exports --bundle-dir /var/omlx/bundles

# 2. Execute the same plan.
session_archive_admin --archive-root /var/omlx/wspaces \
  prune --model M --prune-stale --prune-invalid --prune-orphaned \
        --prune-exports --bundle-dir /var/omlx/bundles --confirm
```

Dry-run output groups candidates by reason and lists protected rows so
the operator can sanity-check before re-running with `--confirm`.

The legacy `--invalid / --older-than / --max-per-model / --no-dry-run`
flags continue to work unchanged for existing wrappers. When any of
the new `--prune-*` flags are passed, the CLI routes through the
structured layer; otherwise it preserves the legacy `prune()`
behaviour.

---

## 7. Cross-references

* [workspace_schema.md §2.7](workspace_schema.md) — optional operational fields.
* [session_archive_ops.md](session_archive_ops.md) — operator-facing task guide.
* [pruning_policy_assessment.md](pruning_policy_assessment.md) — Pass 6 rule-by-rule audit.
