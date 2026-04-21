# Operator UI — MVP Gap Assessment

Date: pass 2 (MVP wiring).
Audience: internal operators / reviewers of the `omlx-main/ui/` shell.

## Starting point (pre-pass-2)

- Backend bridge `omlx/ui_api/` — **done**, mounted at `/ui/api`, 20 routes, 25 pytest passing.
- Frontend scaffold under `omlx-main/ui/` — **exists**, Vite + React 18 + TS + Tailwind, `npm install` clean, `npm run typecheck` + `npm run build` green.
- Pages under `ui/src/pages/`: `WorkspaceList`, `WorkspaceDetail`, `ForkPage`, `DiffPage`, `Transfers`, `Maintenance`, `SettingsPage` — all wired to real `/ui/api` data via `ui/src/lib/api.ts`.
- Shared primitives under `ui/src/components/`: `ui.tsx` (Grade, ErrorBox, Loading, Empty, Section, formatBytes, formatTs), `ConfirmModal.tsx` (supports `destructive` + `requireTyping`).

## What was missing before this pass

### Endpoints already wired (no gap)

All 20 endpoints exposed by the bridge are reachable from the UI:

| Group        | Endpoint                                                 | Used by page                       |
| ------------ | -------------------------------------------------------- | ---------------------------------- |
| workspaces   | `GET /workspaces`                                        | WorkspaceList                      |
| workspaces   | `GET /workspaces/{m}/{s}`                                | WorkspaceDetail                    |
| workspaces   | `POST /workspaces` (create)                              | WorkspaceList (inline create form) |
| workspaces   | `PUT /workspaces/{m}/{s}/metadata`                       | WorkspaceDetail (MetadataEditor)   |
| workspaces   | `POST /workspaces/{m}/{s}/fork`                          | ForkPage                           |
| workspaces   | `POST /workspaces/{m}/{s}/pin`                           | WorkspaceDetail                    |
| workspaces   | `DELETE /workspaces/{m}/{s}/pin`                         | WorkspaceDetail                    |
| workspaces   | `POST /workspaces/{m}/{s}/validate`                      | WorkspaceDetail (ValidateTab)      |
| lineage+diff | `GET /workspaces/{m}/{s}/lineage`                        | WorkspaceDetail (LineageTab)       |
| lineage+diff | `GET /diff`                                              | DiffPage                           |
| transfers    | `POST /transfers/export`                                 | Transfers (ExportCard)             |
| transfers    | `GET /transfers/bundles`                                 | Transfers                          |
| transfers    | `POST /transfers/inspect`                                | Transfers (ImportCard Inspect)     |
| transfers    | `POST /transfers/import`                                 | Transfers (ImportCard Import)      |
| transfers    | `POST /transfers/bundles/pin`, `DELETE .../pin`          | Transfers (PinAction)              |
| maintenance  | `POST /maintenance/prune/dry-run`                        | Maintenance                        |
| maintenance  | `POST /maintenance/prune/execute`                        | Maintenance                        |
| maintenance  | `GET /maintenance/stats`                                 | Maintenance                        |
| env          | `GET /env`                                               | SettingsPage                       |
| env          | `POST /env/health`                                       | SettingsPage                       |

Finding: there is no endpoint gap. Every bridge route is already consumed.

### Structural gaps vs. the spec

- **No `app/` providers/router module.** `main.tsx` inlined the `QueryClient` and Router; no central place for the app shell.
- **No `routes/` folder.** Pages lived in `pages/` (kept in place — see "Decision: keep pages/" below).
- **No `hooks/`.** Every page called `useQuery`/`useMutation` against `api.*` inline. No `useWorkspaces`, `useWorkspace`, `useLineage`, `useValidation`, `useMaintenanceStats`, `useEnvironmentInfo`, `useCreateWorkspace`, `useForkWorkspace`, `useValidateWorkspace`, `useExportWorkspace`, `useImportBundle`, `usePruneDryRun`, `usePruneExecute`, `usePinWorkspace`.
- **No `types/`.** Types were only reachable via `import type { … } from '../lib/schemas'` (the Zod-inferred union file). No per-domain type modules.
- **No centralized query-key factory.** Each page wrote string-array query keys inline (`['workspaces', params]`, `['ws', model, session]`, `['lineage', model, session]`, `['bundles']`, `['mstats']`, `['env']`) — easy to drift and cache-miss.
- **No `fetcher.ts`.** The `request()` wrapper and `ApiError` class lived inside `lib/api.ts`, mixed with endpoint functions.
- **No `queryClient.ts`.** `QueryClient` was created inline in `main.tsx`.
- **No persistent app shell.** `App.tsx` was a single top nav bar; no sidebar, no top bar with env/health indicators, no diagnostics side panel.
- **No dedicated `/workspace/:model/:session/lineage` route.** Lineage rendered as a tab inside the detail page only.
- **No dedicated `/workspace/:model/:session/diff` route.** Diff was a single top-level `/diff` screen requiring the operator to type both sides manually.

### Named operator components missing as discrete files

Per spec, these were either inlined into page files, or not extracted at all:

| Component                | Status pre-pass-2                                           |
| ------------------------ | ----------------------------------------------------------- |
| `StatusPill`             | Existed as inlined `Grade` in `components/ui.tsx`; no discrete file. |
| `WorkspaceHeader`        | Inlined in `WorkspaceDetail.tsx` as `<Section title=…>` + field grid. |
| `WorkspaceActionBar`     | Inlined in `WorkspaceDetail.tsx` `actions` prop.            |
| `TurnTimeline`           | Inlined as a plain HTML table inside `WorkspaceDetail` turns tab. |
| `TurnCard`               | Missing — spec lists it but not strictly required for MVP (table is sufficient). |
| `IntegrityPanel`         | Inlined as `<Field>` items in WorkspaceDetail header grid.  |
| `CompatibilityPanel`     | Not present as a panel — surfaced only as `model_compat.block_size` via raw field. |
| `ReplayValidationPanel`  | Inlined as `ValidateTab` in WorkspaceDetail.                |
| `BundleProvenanceCard`   | Inlined as `<pre>{JSON.stringify(inspect.data)}</pre>` in Transfers ImportCard. |
| `ConflictPolicyDialog`   | Missing — conflict policy was a bare `<select>` with no typed confirmation for `overwrite`. |
| `PruneReasonGroup`       | Missing — Maintenance renders one flat table grouped only by row color. |
| `DangerConfirmDialog`    | Available as generic `ConfirmModal` with `destructive` + `requireTyping`; no dedicated alias. |
| `EnvironmentCard`        | Inlined as the `<dl>` block in SettingsPage.                |
| `SchemaInfoCard`         | Not separated — schema rows were in the same `<dl>` block as other env rows. |
| `LineageGraph`           | Missing — React Flow was never added. See "Deferred" below. |
| `LineageNodeCard`        | Inlined as `<li>` rows in WorkspaceDetail LineageTab.       |
| `LineageInspector`       | Missing.                                                    |

### Safety / UX rules the scaffold already honoured

- Fork requires `branch_reason` ≥4 chars (ForkPage).
- Prune defaults to dry-run; execute requires a signed plan + first-6-chars-of-signature typed confirmation.
- Destructive surfaces use red styling (btn-danger) and `ConfirmModal` with `destructive`.
- Conflict policy default is `fail` (ImportCard).
- No delete button on workspace detail (maintenance-only).
- No public auth, cloud sync, multimodal, or hybrid restore surfaces.

### Safety / UX rules that needed adding

- Import `overwrite` → no typed confirmation was required (just a dropdown).
- Bundle provenance was dumped as raw JSON instead of a structured card — operator could easily miss a mismatched `expected_model_name`.
- Prune plan was a single table; protected-vs-eligible distinction was color-only, not reason-grouped.
- App shell exposed no persistent environment/health indicator — operator could be on the wrong archive root without realizing.

## What "internal MVP" actually requires

The success criterion is a single operator, on their own machine, completing this flow using only the UI:

    create workspace → inspect → fork → diff → validate → export → inspect/import → prune dry-run → resume

For that to be honest, the UI must:

1. Surface the archive root + OMLX version + health status on every page (top bar).
2. Offer navigation that matches the flow (sidebar → Workspaces / Transfers / Maintenance / Settings).
3. Let the operator jump from a workspace to its lineage and to a diff pre-filled against itself without typing model/session by hand (dedicated per-workspace routes).
4. Show bundle provenance as a readable card **before** the destructive import button is enabled.
5. Group prune candidates by reason (`stale`, `invalid`, `orphaned`, `exports`, `empty`, `unreadable`) so the operator chooses per-class, not all-at-once.
6. Keep every destructive action gated by explicit confirmation and typed confirmation for overwrite/prune-execute.

## Decision: keep `pages/` folder name

The spec allowed `routes/`. Since all seven page files are already wired and importing them from `routes/` would force a mass rename with no behavior change, `pages/` stays. New route-level files (`WorkspaceLineagePage`, `WorkspaceDiffPage`) land in the same folder. The `app/` folder holds shell + providers, and `features/` holds the extracted operator components.

## Deferred to post-MVP (explicitly, not silently)

- **`LineageGraph` via React Flow** — spec permits deferral; MVP uses `LineageList` (indented, role-coded). Adding React Flow = new dependency, layout tuning, and node-interaction work out of scope for this pass.
- **`TanStack Table`** — current HTML tables with Tailwind are sufficient for < a few hundred rows, which is the realistic workspace/bundle/candidate volume per archive root.
- **`TurnCard` and `LineageInspector`** — list views cover the MVP; card-detail surfaces are polish.
- **Live-edit `MetadataEditor`** — kept because backend `PUT /metadata` is real (verified in `omlx/ui_api/service.py`). No change needed.
- **Command palette / keyboard shortcuts** — not required for the success condition.

## Required changes for this pass

1. Split `api.ts` → `lib/fetcher.ts` (transport + ApiError) + `lib/api.ts` (endpoints only).
2. Add `lib/queryClient.ts`, `lib/keys.ts`.
3. Add `types/` re-exports per domain (workspace, lineage, diff, bundle, prune, env).
4. Add `hooks/` with one file per feature group + an `index.ts` barrel.
5. Add `app/AppShell.tsx` + `SidebarNav.tsx` + `TopBar.tsx` + `DiagnosticsPanel.tsx`.
6. Rewrite `App.tsx` to use the shell and add the two dedicated routes.
7. Extract named operator components under `features/`.
8. Update pages to consume hooks + extracted components (shrinks page files, prevents drift).
9. Typecheck + build clean.
10. Run the MVP flow, record in `docs/ui_mvp_validation.md`.
