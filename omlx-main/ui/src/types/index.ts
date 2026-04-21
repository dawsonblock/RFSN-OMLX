// Per-domain type barrels. These re-export the Zod-inferred types from
// `lib/schemas.ts` so page/hook code can import from the canonical domain
// path without reaching into the Zod-definitions file.
export type {
  WorkspaceSummary,
  WorkspaceDetail,
  LineageResponse,
  SessionDiff,
  ValidationResult,
  BundleInfo,
  PrunePlan,
  PruneReport,
  MaintenanceStats,
  EnvironmentInfo,
  HealthCheckResult,
} from '../lib/schemas';
