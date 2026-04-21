# Internal Operator UI — Plan

This document is the design record for the local operator UI that fronts the
`omlx.cache.session_archive*` primitives. It is **internal-only**, runs on
`localhost`, and never exposes its endpoints beyond the in-process FastAPI
surface (`omlx.server:app`).

## Scope

### In scope
1. Workspaces: list, detail, create, fork, pin, metadata edit, validate.
2. Lineage: ancestry chain + children tree, diff two sessions.
3. Transfers: export → `ui_exports/`, drop-in import from `ui_imports/`,
   inspect envelope+manifest, list / pin bundles.
4. Maintenance: signed prune dry-run, explicit-confirm execute, archive
   stats.
5. Settings: environment fingerprint, health check (archive writable,
   SSD cache reachable, manifest schema, mlx-lm importable).

### Out of scope (non-goals)
* No authentication beyond the existing optional `admin.auth.require_admin`
  gate.
* No multimodal content or raw file-system browsing.
* No public API; all endpoints are under `/ui/api` and intended for the
  operator's browser on `localhost`.
* No new backend capability: the bridge composes existing primitives
  and never invents new archive mutations.

## Backend bridge — `omlx/ui_api/`

### Files
| File | Purpose |
|---|---|
| `__init__.py` | Exports the aggregate `router`. |
| `schemas.py` | Pydantic v2 DTOs that mirror backend dataclasses 1:1. |
| `service.py` | Pure functions composing `session_archive*` primitives. |
| `routes_workspace.py` | Workspace CRUD, fork, pin, metadata, validate. |
| `routes_lineage.py` | Lineage + diff. |
| `routes_transfer.py` | Export / list / inspect / import / pin bundles. |
| `routes_maintenance.py` | Prune dry-run / execute, archive stats. |
| `routes_env.py` | Environment fingerprint + health. |
| `routes.py` | Aggregate router mounted under `/ui/api`. |

### Routes (prefix `/ui/api`)

| Method & Path | Primitive mapping |
|---|---|
| `GET  /workspaces` | `iter_model_names` × `iter_sessions` + `classify_integrity` (+ optional `replay_check`) |
| `POST /workspaces` | `SessionArchiveStore.init_workspace` |
| `GET  /workspaces/{model}/{id}` | `load_raw` + `lineage` + `list_turns` + `classify_integrity` (+ `replay_check` on `?validate=true`) |
| `PUT  /workspaces/{model}/{id}/metadata` | `SessionArchiveStore.set_label` |
| `POST /workspaces/{model}/{id}/fork` | `SessionArchiveStore.fork` (requires `branch_reason`, min_len=4) |
| `POST /workspaces/{model}/{id}/pin` | `SessionArchiveStore.set_pinned(True/False)` |
| `DELETE /workspaces/{model}/{id}/pin` | `SessionArchiveStore.set_pinned(False)` |
| `POST /workspaces/{model}/{id}/validate` | `replay_check` + schema compare |
| `GET  /workspaces/{model}/{id}/lineage` | `ancestry_chain` + children walk via `iter_sessions`/`load_raw` |
| `GET  /diff` | `diff_sessions` (query: `left_model`, `left_session`, `right_model`, `right_session`) |
| `POST /transfers/export` | `export_session` → file in `ui_exports/` |
| `GET  /transfers/bundles` | `iter_bundles` on `ui_exports/` |
| `POST /transfers/inspect` | `inspect_bundle` on `ui_imports/` (or `ui_exports/` fallback) |
| `POST /transfers/import` | `import_session` (conflict policy: `fail`\|`rename`\|`overwrite`) |
| `POST /transfers/bundles/pin` | `set_bundle_pinned` |
| `POST /maintenance/prune/dry-run` | per-model `plan_prune` aggregated + SHA256 plan signature |
| `POST /maintenance/prune/execute` | replay plan with same `now`, verify signature, `execute_plan(confirm=bool)` |
| `GET  /maintenance/stats` | `session_archive_metrics.snapshot` + aggregate archive size/bundle count |
| `GET  /env` | version/schema constants + resolved paths + mlx-lm pin |
| `POST /env/health` | archive-root write probe + SSD cache exists + schema sanity + mlx-lm import |

### Trust rules preserved verbatim
* `_ReadOnlySSDProbe` duplicated from `scripts/session_archive_admin.py`.
  The bridge **never** instantiates `PagedSSDCacheManager`.
* Malformed manifests surface as errors, never silent repair.
* Conflict policy defaults to `fail`.
* Imports arrive via `ui_imports/` (operator drops files there); no HTTP
  upload path.
* Prune execute requires the matching dry-run signature — any drift in
  the set of candidates forces a re-run.

### Archive-root resolution
* `OMLX_UI_ARCHIVE_ROOT` → explicit override.
* Otherwise `settings.base_path / "session_archive"` with `~/.omlx` as
  fallback when `get_settings()` is unavailable.

### Error mapping
| Cause | HTTP |
|---|---|
| Unknown workspace or turn | 404 |
| Already exists / compatibility mismatch | 409 |
| Plan signature drift | 409 |
| Validation / policy errors | 400 |
| Bundle envelope / manifest errors | 400 (404 when "not found") |

## Frontend plan — `omlx-main/ui/`

Vite + React 18 + TypeScript + Tailwind v3. State via TanStack Query; zod
mirrors schemas for runtime validation. Routes:

| Path | View |
|---|---|
| `/` | Workspace list with filters (model/status/pinned/exportable). |
| `/w/:model/:session` | Detail tabs: `Lineage` · `Turns` · `Validate`. |
| `/w/:model/:session/fork` | Fork flow (requires `branch_reason`). |
| `/diff` | Side-by-side session diff picker + result. |
| `/transfers` | Bundles list + export/import drawers. |
| `/maintenance` | Prune dry-run + signed-confirm execute + stats. |
| `/settings` | Environment fingerprint + health. |

Destructive flows (unpin, import with `overwrite`, prune execute) all
require an explicit confirmation modal that repeats the exact model
name / session id / signature.
