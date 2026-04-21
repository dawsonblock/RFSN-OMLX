# OMLX Operator UI — Quickstart

Local, internal web UI for the OMLX workspace-lineage system. Runs on your
machine only; no authentication is enabled by default.

## Prerequisites

1. The OMLX Python package is installed and the FastAPI server can start.
2. Node.js 18+ is available for local frontend development.
3. An archive root exists. By default: `$OMLX_BASE_PATH/session_archive/` or
   `~/.omlx/session_archive/`. Override with `OMLX_UI_ARCHIVE_ROOT`.

## Start the backend

```bash
# Bundled with the existing OMLX server — no separate process.
python -m omlx server
```

This mounts the UI bridge under `http://127.0.0.1:8000/ui/api`.

## Start the frontend (dev)

```bash
cd ui
npm install
npm run dev
# -> http://127.0.0.1:5173 (proxies /ui/api to the backend)
```

## Build the frontend (production)

```bash
cd ui
npm run build
# -> ui/dist/
```

## Environment overrides

| Variable | Meaning | Default |
|---|---|---|
| `OMLX_UI_ARCHIVE_ROOT` | Session-archive root. | `<base_path>/session_archive` |
| `OMLX_UI_SSD_CACHE_DIR` | Read-only SSD cache probe path. | Value from `settings.cache.get_ssd_cache_dir` |
| `OMLX_UI_BASE_PATH` | Base path for `ui_imports/` and `ui_exports/`. | `settings.base_path` or `~/.omlx` |

The UI bridge **never** instantiates the live `PagedSSDCacheManager`. It only
reads presence via the hex-shard layout under `OMLX_UI_SSD_CACHE_DIR`.

## Operator flows

### Create → inspect → fork
1. **Workspaces** → *Create workspace* → enter `model_name` + `session_id`.
2. Click into the new row. Metadata, lineage (empty), and validate tabs
   appear.
3. After your code has appended turns, refresh to see the updated turn count.
4. Click **Fork**, supply a `branch_reason` (≥ 4 chars), and optionally `at_turn`.

### Diff two sessions
**Diff** page → fill in both sides → *Compute diff*.
The response includes the common ancestor plus per-turn block counts and a
diverged flag.

### Validate
On a workspace detail page, use the **validate** tab. It runs `replay_check`
against the read-only SSD probe, reports the integrity grade, manifest
schema, and missing block hashes if any.

### Export / import
**Transfers** page.

* **Export** drops a `.omlx-session.tar` bundle into `ui_exports/`.
  `allow_missing_blocks` enables export of partially-exportable sessions.
* **Import** reads a bundle from `ui_imports/`. Drop the file there first
  (no HTTP upload path). Choose `fail` (safest, default), `rename`, or
  `overwrite` for conflicts. `re_root_lineage` drops the parent link.
* **Pin** marks a bundle as protected from prune.

### Prune (signed-plan handshake)
**Maintenance** page.

1. Select one or more classes (`stale`, `invalid`, `orphaned`, `exports`,
   `empty`, `unreadable`).
2. Optionally scope to a single model and/or include pinned items.
3. Click **Dry-run** — the server responds with a candidate list and a
   SHA-256 plan signature.
4. Review eligible vs protected rows.
5. Click **Execute (N)** — the modal requires you to retype the first 6
   characters of the signature. The server re-runs the plan with the same
   `now` timestamp and refuses to execute if the signature drifts (archive
   changed mid-flight).

### Environment + health
**Settings** page exposes the resolved archive root, SSD cache dir, manifest
schema version, bundle schema version, and mlx-lm pinned commit. The
*Run health check* button probes archive write access, SSD cache presence,
and `mlx_lm` importability.

## Safety rules the UI preserves

* No destructive action executes without explicit confirmation.
* Prune execute requires a signed-plan handshake.
* Import defaults to `conflict_policy=fail`.
* Bundle filenames are restricted to `[A-Za-z0-9._-]+`.
* Malformed manifests surface as errors, never silent repair.

## Troubleshooting

| Symptom | Check |
|---|---|
| `404 Not Found` from `/ui/api/...` | Is the backend running? Confirm `from omlx.ui_api import router` loads. |
| Every session reports `missing_blocks` | The SSD cache dir is empty. Set `OMLX_UI_SSD_CACHE_DIR` to the real cache location. |
| `plan signature drift` on prune execute | The archive changed between dry-run and execute. Re-run dry-run. |
| `bundle filename must match ...` | Filename contains a path separator or non-allowed characters. Rename before dropping. |
