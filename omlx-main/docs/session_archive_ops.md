# Session Archive — Operator Reference

> **Status: experimental / internal.** Metadata-only named-session
> recovery handle on top of the paged SSD prefix cache. **Not a latency
> feature.** See [session_archive_after.md](./session_archive_after.md)
> for the benchmark verdict. Do not expand this surface (no partial
> restore, no public API, no UI, no multimodal/hybrid restore) without a
> concrete operational use case.
>
> **Semantics and policy:** see
> [workspace_lineage.md](./workspace_lineage.md) for the locked
> vocabulary (workspace, turn, head, branch, replay check, compat
> family), integrity grades, and operator workflow. This page is the
> reference for the individual verbs.

This page is a consolidated cheat-sheet for operators. It links to the
existing scripts and docs; it does not introduce new commands or claims.

## 1. What it is

- A per-`(model_name, session_id)` JSON manifest that lists the ordered
  SHA-256 hashes of blocks a conversation's KV state depends on.
- Manifests live under `<archive-root>/<model_slug>/<session_slug>/manifest.json`.
- KV payload bytes are owned exclusively by the paged SSD cache. The
  archive **never** duplicates them.
- Writes are atomic: temp file in the same directory, `fsync`
  best-effort, then `os.replace` — a crash mid-write never leaves a
  partial manifest on disk.

See [omlx/cache/session_archive.py](../omlx/cache/session_archive.py).

## 2. What it is not

- Not a restart-latency feature. For identical-prompt restarts the
  paged SSD prefix cache already finds the same blocks by content hash;
  the session archive contributes ~0 % median TTFT improvement
  ([session_archive_after.md](./session_archive_after.md) §3, §5).
- Not a public API or HTTP endpoint.
- Not a UI or admin panel.
- Not partial restore. Restore is all-or-nothing: a single missing
  block raises `SessionArchiveError("gapped session archive ...")`.
- Not automatic. Restore and commit only run on explicit
  `request.restore` / `request.session_id`. Retention is manual.

## 3. Inspect / delete / validate a session

The operator CLI is stdlib-only and safe to run against a live archive
root; it never mutates KV payload bytes.

```
python scripts/session_archive_admin.py --archive-root <ROOT> <subcommand>
```

| Subcommand | Purpose |
| --- | --- |
| `list --model M` | One row per manifest for model `M`. |
| `show --model M --session S` | Full manifest + SSD block-presence summary. |
| `validate --model M [--session S]` | Load each manifest and (when `--ssd-cache-dir` is given) check every referenced block is present. Exit `1` on any failure. |
| `delete --model M --session S [--yes]` | Remove one session directory. Prompts unless `--yes`. |
| `prune --model M [--invalid] [--older-than 7d] [--max-per-model 100] [--dry-run\|--no-dry-run]` | Identify (and optionally delete) invalid / expired / over-cap manifests. |
| `prune --model M --prune-stale/--prune-invalid/--prune-orphaned/--prune-exports [--bundle-dir PATH] [--include-pinned] [--confirm]` | **Pass 6 structured prune.** Opt-in per class; dry-run unless `--confirm`. See [pruning_policy.md](pruning_policy.md). |
| `pin --model M --session S` / `pin --bundle PATH` | Mark a workspace or bundle pinned (never prunable without `--include-pinned`). |
| `unpin --model M --session S` / `unpin --bundle PATH` | Clear the pinned flag. |
| `stats` | Print in-process counters from [session_archive_metrics](../omlx/cache/session_archive_metrics.py). |

Exit codes: `0` OK, `1` validation / prune candidates found, `2` bad
arguments. Source: [scripts/session_archive_admin.py](../scripts/session_archive_admin.py).

Retention helpers (`iter_sessions`, `classify_session`, `find_invalid`,
`find_expired`, `select_over_cap`, `prune`) live in
[omlx/cache/session_archive_retention.py](../omlx/cache/session_archive_retention.py)
if you need them from Python directly.

### Example: identify bad manifests, dry-run, then delete

```
python scripts/session_archive_admin.py \
    --archive-root /var/lib/omlx/archive \
    --ssd-cache-dir /var/lib/omlx/ssd \
    prune --model mlx-community/Qwen2.5-0.5B-Instruct-4bit \
    --invalid --older-than 30d --max-per-model 200 --dry-run

# Review output. If it looks right:
python scripts/session_archive_admin.py \
    --archive-root /var/lib/omlx/archive \
    --ssd-cache-dir /var/lib/omlx/ssd \
    prune --model mlx-community/Qwen2.5-0.5B-Instruct-4bit \
    --invalid --older-than 30d --max-per-model 200 --no-dry-run
```

## 4. Observability

In-process counters (stdlib `collections.Counter` + a `threading.Lock`,
no metrics-platform dependency). Event names are stable; tests match on
them exactly.

| Event | When it bumps |
| --- | --- |
| `restore_attempted` | `Scheduler.restore_session` called. |
| `restore_succeeded` | Block table rebuilt from manifest. |
| `restore_rejected` (+ `:<reason>`) | Restore raised. Reasons: `unknown`, `malformed`, `empty`, `compat`, `missing_blocks`, `no_session_id`, `no_store`, `no_ssd`. |
| `manifest_committed` | `SessionArchiveStore.commit` completed. |
| `manifest_commit_failed` | Commit path raised. |
| `session_archive_invalid` (+ `:<reason>`) | `SessionArchiveStore.load` raised. |
| `session_archive_missing_blocks` | Restore found referenced blocks absent from the SSD cache. |
| `ssd_block_quarantined` | Paged SSD cache moved a bad file to `quarantine/`. |

Snapshot via `python scripts/session_archive_admin.py ... stats`, or
programmatically with `session_archive_metrics.snapshot()`
([omlx/cache/session_archive_metrics.py](../omlx/cache/session_archive_metrics.py)).

## 5. Reproduce the environment

The `GenerationBatch` API between `mlx-lm` 0.31.1 (tag) and the commit
we actually depend on differs; a tag-based install silently regresses
tool-calling and multi-token reasoning. The bootstrap script fails loud
if the wrong commit is installed.

```
bash scripts/bootstrap_dev_env.sh
```

What it does: resolves Python 3.10.12 (via `pyenv` if available),
creates `.venv/`, installs `-c constraints/dev.txt -e .`, then reads
`direct_url.json` for the installed `mlx-lm` distribution and asserts
the recorded vcs commit equals the pin.

Authoritative pin: [constraints/dev.txt](../constraints/dev.txt) →
`mlx-lm @ git+https://github.com/ml-explore/mlx-lm@dcbf6e33d135a1b7c6767ca0fe7ebbd23df814a7`.

## 6. Rerun the benchmark

The benchmark exists as a **guardrail**, not a speed story. Use it to
confirm the feature has not silently drifted into something that
behaves differently from the documented verdict.

```
source .venv/bin/activate
PYENV_VERSION=3.10.12 python scripts/bench_session_archive.py \
    --model mlx-community/Qwen2.5-0.5B-Instruct-4bit \
    --prompt-tokens 4000 --reps 3
```

Driver: [scripts/bench_session_archive.py](../scripts/bench_session_archive.py).
Worker: [scripts/_bench_session_archive_worker.py](../scripts/_bench_session_archive_worker.py).
Saved baseline: [scripts/bench_results_session_archive.json](../scripts/bench_results_session_archive.json).

Compare against the baseline and the interpretation in
[session_archive_after.md](./session_archive_after.md) §3. The honest
story is still the one there: **C vs D ≈ 0 % median**.

## 7. Lineage / recovery surface (schema v2)

> **Status: experimental / internal.** Metadata-only. Does not change
> scheduler behaviour, does not duplicate KV bytes, does not add a
> network API. Same hard rules as §1 apply.

Schema v2 adds an append-only `turns[]` list per session, plus optional
`parent`, `label`, `description`, and a `model_compat` stamp. v1
manifests are auto-upgraded on first commit; existing call sites keep
working unchanged.

### 7.1 Vocabulary

- **session / workspace**: a named `(model_name, session_id)` pair. One manifest on disk. The operator-facing noun is **workspace**.
- **turn**: a single `commit()` — an ordered list of block hashes
  representing the KV state after that turn. Turn ids are
  `t-00001`, `t-00002`, … and are assigned by the store. A turn may carry a short `note`; branch turns may also carry a `branch_reason`.
- **branch / fork**: a new session whose `parent` points at an earlier
  turn of another session. Seeded with the source turn's block list;
  diverges freely after that.
- **head**: the latest turn of a session.
- **lineage**: session + parent + turn history.

### 7.2 New CLI subcommands

All are stdlib-only, safe against a live archive root, and never touch
KV payload bytes.

| Subcommand | Purpose |
| --- | --- |
| `create --model-name M --session-id S [--label L] [--description D] [--task-tag TAG]` | Create an empty workspace manifest with optional human-readable metadata. |
| `status --model-name M --session-id S [--stale-after 7d] [--expected-model-name M]` | One-screen summary: label, description, task tag, head turn, parent/ancestry, turn count, integrity grade, replay/export readiness. |
| `resume --model-name M --session-id S` | `status` plus next-step hints (`fork`, `diff`, `export-session`, etc.). Exits non-zero when the workspace is invalid, incompatible, or missing required blocks. |
| `turns --model-name M --session-id S` | List every turn of a session (`turn_id`, `committed_at`, block count, note, `branch_reason`). |
| `head --model-name M --session-id S` | Print `head_turn_id` and its block count. |
| `lineage --model-name M --session-id S` | Session metadata + parent link + turn count + model_compat. |
| `fork --model-name M --src-session-id SRC --dst-session-id DST --at-turn t-0000N [--label L] [--description D] [--branch-reason WHY] [--task-tag TAG]` | Create a branch session seeded from SRC@turn. Refuses if DST already exists or the source turn is unknown. |
| `diff --model-a MA --session-a SA --model-b MB --session-b SB` | Per-turn diff between two sessions. Shows common prefix, common ancestor (when both sessions share a fork point), and per-turn `a_blocks`/`b_blocks`/`common_prefix`/`diverged`. |
| `replay-check --model-name M --session-id S [--turn t-0000N] [--expected-model-name M]` | Walk a turn's block list and report every hash that is not in the SSD cache. Metadata-only — does not load payloads. |
| `export-session --model-name M --session-id S --out BUNDLE.tar [--allow-missing-blocks]` | Write an integrity-verified tarball (`bundle.json` + `manifest.json` + `blocks/<hex>.safetensors`) with provenance (`source_label`, `task_tag`, `git_commit`, platform, compatibility family). |
| `inspect-bundle --bundle BUNDLE.tar` | Show bundle provenance and compatibility without mutating archive state. |
| `import-session --bundle BUNDLE.tar [--expected-model-name M] [--expected-block-size N] [--fail-if-exists | --rename-on-conflict | --overwrite] [--re-root-lineage]` | Restore a bundle into `<archive-root>` and `<ssd-cache-dir>`. Verifies every block's SHA-256, rejects path-traversal/symlinks, and uses a deterministic, explicit conflict policy. |

### 7.3 Integrity grades

`classify_integrity()` and the CLI output map every manifest to one of:

| Grade | Meaning |
| --- | --- |
| `healthy` | Manifest loads, model_compat matches (when checked), all head-turn blocks present in the SSD cache. |
| `stale` | Healthy but `updated_at` is older than the configured threshold. |
| `invalid_manifest` | File missing, truncated, JSON error, or schema error. Surfaced conservatively; the archive does not silently overwrite a malformed manifest on the next commit. |
| `missing_blocks` | Manifest loads but one or more head-turn blocks are absent from the SSD cache. |
| `incompatible_model` | Manifest's `model_compat.model_name` does not match the expected model. |
| `unreadable` | I/O error reaching the manifest file. |
| `partially_exportable` | Only emitted by `export-session --allow-missing-blocks`. |

Constants live in [omlx/cache/session_archive.py](../omlx/cache/session_archive.py)
(`INTEGRITY_HEALTHY`, `INTEGRITY_STALE`, …). The retention helper
`integrity_grade(status, *, stale=False)` in
[omlx/cache/session_archive_retention.py](../omlx/cache/session_archive_retention.py)
maps existing retention statuses to the same vocabulary.

### 7.4 Bundle format (v1)

Uncompressed tarball, `BUNDLE_VERSION = "1"`.
The envelope includes metadata-only provenance such as:

- `source_label`
- `source_description`
- `task_tag`
- `model_compat`
- `platform`
- `exporter_version`
- `git_commit` (when available)

Conflict policy is explicit and conservative:

- **default / `--fail-if-exists`**: fail if the destination session already exists,
- **`--rename-on-conflict`**: rename deterministically to `<session>-imported-N`,
- **`--overwrite`**: replace only with explicit operator intent,
- **`--re-root-lineage`**: clear the imported `parent` link instead of preserving an external ancestor.

The store also rejects identity ambiguity: a manifest or bundle whose
recorded `session_id` does not exactly match the requested/imported
workspace is treated as invalid rather than aliased.

Uncompressed tarball, `BUNDLE_VERSION = "1"`:

```
bundle.json           # envelope: {version, model_name, session_id, block_sha256:{hex:hex}, source_cache_layout}
manifest.json         # verbatim copy of the session manifest
blocks/<hex>.safetensors   # one file per block; SHA-256 verified on import
```

Source layout is recorded as `paged-ssd-safetensors/v1` (matches the
`<cache_dir>/<hex[0]>/<hex>.safetensors` sharding used by
`PagedSSDCacheManager`). Imports that ship an unknown layout are
refused.

Safe extraction rejects: absolute paths, `..` traversal, symlinks,
hardlinks, device/FIFO entries, and files outside `blocks/`.

## 8. Hard rules

- `PagedSSDCacheManager` remains the sole KV payload authority.
- `SessionArchiveStore` writes metadata only. `export-session` /
  `import-session` read and write SSD payload files by hash only; they
  never rewrite or recompute them.
- Restore is explicit, opt-in, and all-or-nothing.
- Non-session request traffic bypasses both restore and commit.
- No background service, no cron hook, no deletion from the request path.
- Fork, diff, replay-check, export, and import are **operator-only**
  commands. They are not wired into the scheduler or the HTTP API and
  are not intended to be.
- The store fails loud on structural drift: a `head_turn_id` that does
  not resolve to any recorded turn raises `SessionArchiveError` rather
  than silently falling back to the last turn on disk.
