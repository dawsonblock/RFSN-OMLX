# Operator Quickstart

Nine-step end-to-end workflow for OMLX `0.3.6` on a single
Apple-Silicon host. Assumes you have already run
[`install_internal.md`](./install_internal.md) and have a working
`.venv`.

Related:

* [`release_internal_v1.md`](./release_internal_v1.md)
* [`session_archive_ops.md`](./session_archive_ops.md)
* [`pruning_policy.md`](./pruning_policy.md)
* [`workspace_schema.md`](./workspace_schema.md)

---

## Run the operator UI

The operator SPA is served by the same `omlx serve` process at
`/ui/`; the JSON surface lives at `/ui/api/…`. The same backend supports
two frontends:

### Production (one process)

```bash
# One-time: build the SPA into the Python package.
bash scripts/build_ui.sh        # runs npm ci + npm run build + copies to omlx/ui_static/

omlx serve                      # serves API + SPA at /ui/
open "http://127.0.0.1:${OMLX_PORT:-8000}/ui/"
```

Asset URLs are emitted under `/ui/assets/*` (immutable cache). `/ui` (no
slash) redirects to `/ui/`. If you start the server without building the
bundle first, `/ui/` shows a placeholder page with build instructions —
the API continues to work.

### Development (two processes, hot reload)

```bash
# Terminal A: backend (serves /ui/api/*).
omlx serve

# Terminal B: Vite dev server with HMR, proxies /ui/api → backend.
npm --prefix ui run dev
open http://127.0.0.1:5173/
```

### Smoke checklist

With `omlx serve` running and the SPA in a browser:

1. Workspaces list renders and `GET /ui/api/workspaces` appears in the
   Network tab.
2. Create a workspace → it appears in the list.
3. Open the workspace, fork to a new session, pin it.
4. Maintenance → dry-run → `PruneReasonGroup` renders with counts.
5. Transfers → export a workspace, then import the bundle back.

---

## Environment setup

Pick a model, an SSD cache root, and an archive root. All three are
referenced in every step below.

```bash
cd omlx-main
source .venv/bin/activate

export MODEL=mlx-community/Qwen2.5-0.5B-Instruct-4bit
export SSD=/var/omlx/ssd
export ARCHIVE=/var/omlx/archive

alias admin='python scripts/session_archive_admin.py --archive "$ARCHIVE"'
```

`$ARCHIVE` holds workspace manifests and blocks. `$SSD` holds the
paged SSD cache. Both must be on a local filesystem with enough
space — the paged SSD cache grows per-turn.

---

## 1. Create a named workspace

```bash
admin create --name demo-session --model "$MODEL"
```

This writes `manifest.json` (schema `MANIFEST_VERSION=2`) under
`$ARCHIVE/demo-session/`. The manifest carries lineage metadata but
no blocks yet.

## 2. Commit a turn (checkpoint)

Turns are committed by the scheduler during normal inference.
After a turn completes you can inspect the lineage:

```bash
admin inspect --name demo-session
```

You should see `turn_id` entries growing and `updated_at` advancing.
`created_at` must not move. `last_used_at` is refreshed by
replay-check and export/import; it does **not** move `updated_at`.

## 3. Fork the session

Fork creates a sibling lineage from an existing turn. Forks are
cheap: they share blocks by content-address.

```bash
admin fork --name demo-session --from-turn <turn_id> --new-name demo-fork
```

## 4. Diff two workspaces

```bash
admin diff --a demo-session --b demo-fork
```

Shows divergent turn ids, missing blocks, and any lineage
inconsistencies.

## 5. Replay-check (validate without re-running prefill)

```bash
admin replay-check --name demo-session
```

Validates manifest schema, per-block sha256, and lineage
consistency. On success it refreshes `last_used_at`. On failure
the workspace is flagged `invalid` and becomes eligible for the
conservative `invalid` prune class after the frozen grace window.

## 6. Export a portable bundle

```bash
admin export --name demo-session --out "$ARCHIVE/bundles/demo-session.tar"
```

Bundle format is `BUNDLE_VERSION=1`. Every block is sha256-stamped.
The exporter's git commit and platform tuple are embedded. Export
also refreshes `last_used_at` on the source workspace.

## 7. Import on another host

Copy the tarball to the destination host, then:

```bash
admin import --in /path/to/demo-session.tar --name demo-session-restored
```

Importer rejects any block whose sha256 does not match. On success
`last_used_at` is set on the newly materialized workspace. Vision,
audio, and hybrid/SSM state are **not** restored — see
[`release_internal_v1.md`](./release_internal_v1.md) *Known
limitations*.

## 8. Resume

Point your scheduler at the restored workspace. Inference continues
from the last committed turn with no prefill re-run.

## 9. Prune dry-run

Always run prune with dry-run first. Without `--confirm` nothing is
deleted.

```bash
# Plan only — no deletions
admin prune --prune-stale --prune-invalid --prune-orphaned \
            --prune-empty --prune-unreadable \
            --prune-exports --bundle-dir "$ARCHIVE/bundles"
```

Review the plan. Each candidate has a class
(`stale|invalid|orphaned|empty|unreadable|exports`) and a reason.
Pinned workspaces and pinned bundles are skipped unless
`--include-pinned` is supplied; even then the underlying reason is
preserved.

When satisfied:

```bash
admin prune --prune-stale --prune-invalid --prune-orphaned \
            --prune-empty --prune-unreadable \
            --prune-exports --bundle-dir "$ARCHIVE/bundles" \
            --confirm
```

See [`pruning_policy.md`](./pruning_policy.md) for the frozen
retention windows.

---

## Pin / unpin

Pin a workspace to exempt it from all prune classes until
`--include-pinned` is used:

```bash
admin pin --name demo-session
admin unpin --name demo-session
```

Pin a bundle by writing a `<path>.pinned` sidecar — the admin CLI
does this for you:

```bash
admin pin --bundle "$ARCHIVE/bundles/demo-session.tar"
admin unpin --bundle "$ARCHIVE/bundles/demo-session.tar"
```

The tarball bytes are not modified.

---

## Common mistakes

* **Running prune without `--confirm`.** It is dry-run by design;
  missing `--confirm` is not a bug.
* **Assuming `updated_at` reflects "last operator touch".** It does
  not. `updated_at` moves on lineage changes. Use
  `last_used_at` for recency queries.
* **Treating pin as permanent.** `--include-pinned` can lift the
  protection during a prune; the underlying reason is preserved so
  a later unpin does not re-expose a surprise.
* **Exporting a vision or audio session and expecting full
  restore.** Bundles are text-only lineage. Multimodal restore is
  explicitly out of scope.
* **Upgrading mlx-lm without re-running Pass 4 proof tests.** The
  pinned commit is an API requirement. Changing it invalidates
  ownership claims.
* **Running on Linux or non-Apple-Silicon macOS.** Not supported.
  Bootstrap will refuse a wrong platform.
