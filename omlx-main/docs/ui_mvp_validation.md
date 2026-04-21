# OMLX Operator UI — MVP validation

Status: **Usable internal MVP.** Full operator loop (create → inspect → fork → diff → validate → export → import → prune → resume) is reachable through the UI alone without opening a terminal.

## Toolchain

| Tool | Command | Result |
|---|---|---|
| Backend tests | `pytest tests/test_ui_api_workspaces.py tests/test_ui_api_maintenance.py -q` | 25 passed |
| TS typecheck | `npm run typecheck` (inside `ui/`) | clean |
| Vite build | `npm run build` | `dist/` produced, 318 KB JS (90 KB gzipped) |

## Architecture at a glance

```
ui/src/
  app/                    shell: SidebarNav, TopBar, DiagnosticsPanel, AppShell
  lib/                    fetcher (ApiError) · queryClient · keys (qk) · api · schemas (Zod)
  types/                  type-only re-exports from schemas
  hooks/                  useWorkspaces · useWorkspace · useCreateWorkspace · useUpdateMetadata
                          useForkWorkspace · usePinWorkspace · useValidateWorkspace · useLineage
                          useBundles · useExportWorkspace · useInspectBundle · useImportBundle · usePinBundle
                          useMaintenanceStats · usePruneDryRun · usePruneExecute
                          useEnvironmentInfo · useHealthCheck
  features/
    workspace/            StatusPill · WorkspaceHeader · WorkspaceActionBar · TurnTimeline
                          IntegrityPanel · CompatibilityPanel · ReplayValidationPanel
    lineage/              LineageList
    transfers/            BundleProvenanceCard · ConflictPolicyDialog
    maintenance/          PruneReasonGroup · DangerConfirmDialog
    settings/             EnvironmentCard · SchemaInfoCard
  pages/                  WorkspaceList · WorkspaceDetail · WorkspaceLineagePage · WorkspaceDiffPage
                          ForkPage · DiffPage · Transfers · Maintenance · SettingsPage
```

All data flow: `pages → hooks (TanStack Query) → lib/api → lib/fetcher → /ui/api`.
Every response is validated with Zod in `lib/schemas.ts`; types are re-exported from `types/` for consumers.

## Flow walkthrough

| Step | Route(s) | Surface |
|---|---|---|
| 1. List / create workspace | `/` | `WorkspaceList` + create form → `useCreateWorkspace` |
| 2. Inspect | `/w/:model/:session` | `WorkspaceHeader`, `IntegrityPanel`, `CompatibilityPanel`, `TurnTimeline`, `MetadataEditor` |
| 3. Lineage | `/w/:model/:session/lineage` | `LineageList` (indented tree, dangling-parent warnings) |
| 4. Fork | `/w/:model/:session/fork` | `ForkPage` → `api.forkWorkspace` |
| 5. Diff | `/w/:model/:session/diff` (scoped) and `/diff` (arbitrary pair) | Depth-1 banner, per-turn table |
| 6. Validate / replay | `/w/:model/:session` → "Validate / replay" section | `ReplayValidationPanel` → `useValidateWorkspace` |
| 7. Export | `/transfers` → Export card | `useExportWorkspace`; shows resulting path + block counts |
| 8. Inspect + Import | `/transfers` → Import card | `useInspectBundle` → `BundleProvenanceCard`; import gated by `ConflictPolicyDialog` |
| 9. Prune dry-run | `/maintenance` | `PruneReasonGroup` per-reason; signature-gated `DangerConfirmDialog` on execute |
| Resume | `/w/:model/:session` → "Resume" section | Informational (head turn, exportable, integrity) — does not write |
| Env / health | `/settings` | `EnvironmentCard`, `SchemaInfoCard`, health button |

`DiagnosticsPanel` (toggle `diag` in the top bar) exposes live fetching/mutation counts plus an always-available health check.

## Safety rules enforced in the UI

- Every mutation-on-failure raises `ApiError` from `lib/fetcher.ts` with backend status + detail payload — rendered via `ErrorBox`.
- Pin/unpin, prune-execute, and import-overwrite require explicit confirmation via `ConfirmModal`; destructive styling is applied.
- **Prune execute** is gated by a `DangerConfirmDialog` that requires the operator to type the first 6 chars of `plan_signature`. The server re-verifies the signature independently.
- **Import overwrite** requires the operator to type `overwrite {session_id}` (`ConflictPolicyDialog`); `fail` and `rename` pass through with only a normal confirm.
- `include_pinned` for prune is a separately checkable flag with inline copy warning that it is "rarely correct."
- Schema validation at transport layer: any unexpected server payload fails loudly in the UI (Zod error → `ErrorBox`), instead of silently rendering garbage.

## Deliberate deferrals (not needed for MVP)

| Feature | Why deferred |
|---|---|
| React Flow lineage visualiser | HTML indented tree (`LineageList`) is sufficient for current tree sizes and avoids an extra dependency. |
| TanStack Table | Native tables with Tailwind are adequate; no sort/filter gymnastics needed yet. |
| `TurnCard`, `LineageInspector`, `TurnDiffTable`, `BundleEnvelope` components | Inline renderings in `TurnTimeline` / `DiffPage` / `BundleProvenanceCard` already cover the operator flows without separate components. |
| Moving `pages/` → `routes/` | `pages/` is already the established directory; renaming would be cosmetic churn with no operator-facing benefit. Documented in `ui_mvp_gap_assessment.md`. |
| Direct file upload transport | Bundles are staged in `ui_exports/` / `ui_imports/` per backend design; UI exposes filenames and pin/rm actions rather than browser-side multipart. |
| Auth, multi-tenant, cloud telemetry, multimodal | Out of scope by operator-only spec. |

## Known limitations

- Session-scoped diff is depth-1 only (backend constraint); the `WorkspaceDiffPage` surfaces a banner explaining this.
- Import re-rooting is an explicit checkbox; there is no preview of the ancestry edit before apply.
- Prune does not stream long-running progress; it returns a single structured result.
- No delete action on `WorkspaceDetail` — pinning/unpinning is the only lifecycle control from the detail page. Deletion flows through prune classes on `/maintenance`.

## Verdict

The UI is a **usable internal operator MVP**: a single operator can run the entire archival-replay loop through the browser, receive schema-validated responses, confirm destructive actions with signed tokens, and surface all errors as structured messages. Further visualisation (graph lineage, rich diff viewer) is additive, not required.
