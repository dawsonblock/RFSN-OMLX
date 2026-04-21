# Pruning policy — assessment (Pass 6, Phase 1)

Read-only audit of the existing retention / prune surface in this
branch as of Pass 5 (Pass 6 starts from this file). All line numbers
are relative to `omlx-main/`. This document motivates the Pass 6
implementation but does not change any code.

Related:

* [`workspace_schema.md`](./workspace_schema.md) — frozen on-disk schema.
* [`runtime_ownership_table.md`](./runtime_ownership_table.md) — what
  runtime code this branch owns.
* [`pruning_policy.md`](./pruning_policy.md) — the conservative
  policy this assessment motivates.

---

## 1. Current prune / retention behavior

All retention code lives in
[`omlx/cache/session_archive_retention.py`](../omlx/cache/session_archive_retention.py)
and is consumed by the `prune` subcommand of
[`scripts/session_archive_admin.py`](../scripts/session_archive_admin.py).

The library entry point is
`prune(store, ssd_cache, model_name, *, invalid=False,
older_than=None, max_per_model=None, dry_run=True)` at
`session_archive_retention.py:255`. It builds a `PruneReport` with
three independent selector lists (`invalid`, `expired`, `over_cap`),
deduplicates them into `to_delete`, and either reports them (dry-run
default True) or `shutil.rmtree`s each selected session directory.

Helpers are:

* `iter_sessions()` — yields one `SessionDescriptor` per manifest on
  disk; skips directories without a `manifest.json`.
* `classify_session()` — returns `("ok", ...)`,
  `("missing_blocks", ...)`, or `("invalid:<reason>", ...)` per
  session. Backed by `SessionArchiveStore.load()`.
* `integrity_grade()` — maps `classify_session` output to the shared
  integrity vocabulary (`healthy`, `missing_blocks`, `invalid_manifest`,
  `incompatible_model`, `unreadable`). Accepts a `stale=True` override.
* `find_invalid()`, `find_expired(older_than: timedelta)`,
  `select_over_cap(max_count: int)`.

The admin CLI surface is `prune --model M [--invalid] [--older-than
7d] [--max-per-model N] [--dry-run|--no-dry-run]`
(`session_archive_admin.py:140`). Defaults: `--dry-run` is True;
`--invalid` / `--older-than` / `--max-per-model` all default off.

## 2. Existing metadata (timestamps, lineage, pinning)

* Every v2 manifest carries `created_at` (float) and `updated_at`
  (float). `updated_at` is rewritten on every `commit()`, `fork()`,
  and `set_label()`.
* Retention helpers use `manifest.stat().st_mtime` as the selection
  timestamp (`session_archive_retention.py:iter_sessions` →
  `SessionDescriptor.mtime`).
* `classify_integrity()` at `session_archive.py:1072` already supports
  grading a healthy session as `stale` via `stale_after_seconds`, but
  **no caller in `session_archive_retention.py` uses it**.
* Lineage metadata exists: `LineageInfo.parent = (session_id,
  turn_id) | None`, `ancestry_chain()` walks the chain to the root.
* **No pinning exists.** `grep -rE "pin|pinned" omlx/cache/` → 0 hits.
* **No last-useful timestamp exists** beyond `updated_at` (which only
  tracks commits / label edits, not resumes / replay / export /
  import).
* **No bundle retention exists.** Bundles live outside the archive
  root and `session_archive_portable.py` ships only export / import /
  inspect.

## 3. Desired policy — already implemented vs missing

The user-provided policy has 11 rules. Mapping each against the
current state:

| # | Rule | Status |
|---|---|---|
| 1 | Never auto-delete healthy replay-valid workspaces newer than 30d | **Missing.** `prune(older_than=N)` does not check integrity grade — a 31d healthy workspace under `--older-than 30d` is deleted. |
| 2 | Mark healthy workspaces older than 30d as `stale` | **Partially present.** `classify_integrity(stale_after_seconds=...)` exists; retention does not call it. |
| 3 | Healthy stale deletion requires ≥90d **and** explicit confirmation | **Missing.** No confirmation flag at all; `--no-dry-run` is the only gate. |
| 4 | Invalid / unreadable / empty manifests prune-eligible after 7d | **Partially present.** `--invalid` selects them but with no age gate — a 1-minute-old malformed manifest is already eligible. |
| 5 | Orphaned (readable but missing blocks) prune-eligible after 14d, separate from invalid | **Missing.** `missing_blocks` is lumped into `find_invalid()` output with grade `missing_blocks`; no age gate, no separate flag. |
| 6 | Portable bundles retained 21d by default unless pinned | **Missing.** No bundle retention code at all. |
| 7 | Pinned workspaces and pinned bundles never auto-pruned | **Missing.** No pinning support anywhere. |
| 8 | Never auto-delete the most recent healthy head in any workspace lineage | **Missing.** Nothing walks the lineage tree during pruning. |
| 9 | Resume / replay-validate / export / import refresh a "last useful" timestamp | **Missing.** `updated_at` only advances on commit / fork / set_label. |
| 10 | Prune always supports dry-run mode and groups candidates by reason | **Partially present.** Dry-run exists; candidate grouping is by selector (`invalid` / `expired` / `over_cap`), not by *reason*. |
| 11 | Destructive pruning requires explicit class flags; no broad "delete everything old" default | **Partially present.** `--no-dry-run` is the only gate; a single call `--invalid --older-than 7d --no-dry-run` deletes healthy items aged past the window. |

## 4. Schema additions required

Two additive, **non-breaking** optional fields on the v2 manifest
(per the migration policy in
[`workspace_schema.md §6.1`](./workspace_schema.md)):

* `pinned: bool` — default `false` (absent ≡ false). Set / cleared via
  an explicit admin action.
* `last_used_at: float | null` — default `null`. Refreshed by a small
  number of read-side actions (resume / replay-validate / export /
  import). Initialized to `created_at` on first write.

No `MANIFEST_VERSION` bump. `SUPPORTED_MANIFEST_VERSIONS` stays
`("1", "2")`. The schema-contract test
[`test_manifest_version_constant_contract`](../tests/test_session_archive_schema_contract.py)
stays green.

Bundles are tar archives whose integrity is pinned by `block_sha256`
in the envelope; mutating them to add a pin flag would invalidate
that checksum. **Bundle pin is a sidecar marker file**
`<bundle_path>.pinned` (empty). This keeps the bundle byte-identical
and leaves the frozen bundle v1 envelope untouched.

## 5. Risk of deleting healthy lineage heads accidentally

As the code stands today, the following sequence would delete a
healthy head:

1. Operator runs `prune --invalid --older-than 30d --no-dry-run`.
2. `find_expired()` selects every workspace with `mtime` older than
   30 days — including healthy heads that happen to be quiet.
3. `prune()` deduplicates, calls `shutil.rmtree` on each.

The only protection today is operator discipline (read the dry-run
output carefully). There is no code-level guardrail for:

* healthy-recent ages (nothing differentiates 1d vs 31d);
* latest healthy head in a fork-lineage root;
* pinned items (no such concept);
* separating invalid-schema from orphaned-cache issues.

This is the core motivation for Pass 6's class-per-flag + confirmation
split.

## 6. Conclusion

The existing retention module is structurally adequate — iteration,
classification, and atomic directory removal are correct — but the
**selection policy is too coarse** for the asked behaviour. Pass 6
tightens it by:

1. Adding two optional manifest fields (`pinned`, `last_used_at`) and
   one sidecar marker (`<bundle>.pinned`).
2. Adding a `PruneCandidate` / `PrunePlan` layer on top of the current
   selectors that assigns exactly one **reason** per candidate and
   distinguishes *protected* from *eligible*.
3. Splitting destructive intent into per-class flags
   (`--prune-invalid`, `--prune-orphaned`, `--prune-stale`,
   `--prune-exports`) plus a required `--confirm`.
4. Refreshing `last_used_at` from `replay_check` /
   `export_session` / `import_session` (and the CLI's `resume`
   verb). `load()` stays pure — see
   [`pruning_policy.md`](./pruning_policy.md) for the rationale.
