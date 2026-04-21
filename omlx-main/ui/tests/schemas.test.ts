import { describe, it, expect } from 'vitest';
import {
  workspaceSummary,
  workspaceDetail,
  lineageResponse,
  sessionDiff,
  validationResult,
  bundleInfo,
  prunePlan,
  maintenanceStats,
  environmentInfo,
  healthCheckResult,
} from '../src/lib/schemas';

describe('zod schemas', () => {
  it('accepts a minimal WorkspaceSummary', () => {
    const row = workspaceSummary.parse({
      model_name: 'm',
      session_id: 's',
      head_turn_id: 't0',
      turn_count: 0,
      updated_at: 0,
      pinned: false,
      integrity_grade: 'healthy',
      branch_count: 0,
      has_parent: false,
      exportable: true,
      model_compat: { model_name: 'm' },
    });
    expect(row.model_compat.schema).toBe('2');
    expect(row.label).toBeUndefined();
  });

  it('rejects WorkspaceSummary missing required fields', () => {
    expect(() =>
      workspaceSummary.parse({ model_name: 'm', session_id: 's' }),
    ).toThrow();
  });

  it('accepts WorkspaceDetail with optional replay=null', () => {
    const d = workspaceDetail.parse({
      model_name: 'm',
      session_id: 's',
      lineage: {
        session_id: 's',
        created_at: 0,
        updated_at: 0,
        head_turn_id: 't0',
        model_compat: { model_name: 'm' },
        turn_count: 0,
      },
      turns: [],
      pinned: false,
      integrity_grade: 'healthy',
      exportable: true,
      replay: null,
      children_count: 0,
    });
    expect(d.replay).toBeNull();
  });

  it('accepts LineageResponse with dangling_parent tuple', () => {
    const r = lineageResponse.parse({
      focus: ['m', 's'],
      ancestors: [],
      descendants: [],
      dangling_parent: ['m', 'gone'],
    });
    expect(r.dangling_parent).toEqual(['m', 'gone']);
  });

  it('accepts SessionDiff with null turn ids', () => {
    const d = sessionDiff.parse({
      session_a: ['m', 'a'],
      session_b: ['m', 'b'],
      common_ancestor: null,
      turn_count_a: 1,
      turn_count_b: 1,
      shared_turn_count: 0,
      per_turn: [
        {
          turn_id_a: null,
          turn_id_b: 't0',
          block_count_a: 0,
          block_count_b: 1,
          common_prefix_blocks: 0,
          diverged: true,
        },
      ],
    });
    expect(d.per_turn[0].diverged).toBe(true);
  });

  it('accepts ValidationResult', () => {
    expect(
      validationResult.parse({
        model_name: 'm',
        session_id: 's',
        integrity_grade: 'healthy',
        replay: {
          session_id: 's',
          model_name: 'm',
          head_turn_id: 't0',
          total_blocks: 0,
          present_blocks: 0,
          missing_blocks: [],
          replayable: true,
          grade: 'healthy',
        },
        manifest_schema_version: '2',
        schema_ok: true,
        exportable: true,
        reported_at: 0,
      }).schema_ok,
    ).toBe(true);
  });

  it('accepts BundleInfo with envelope record', () => {
    const b = bundleInfo.parse({
      path: '/x.tar.gz',
      size_bytes: 1,
      mtime: 0,
      pinned: false,
      envelope: { source_session_id: 's' },
    });
    expect(b.envelope?.source_session_id).toBe('s');
  });

  it('accepts PrunePlan and indexes by_reason', () => {
    const candidate = {
      kind: 'workspace' as const,
      reason: 'stale',
      action: 'eligible' as const,
      model_name: 'm',
      session_id: 's',
      path: '/p',
      age_seconds: 10,
      pinned: false,
    };
    const p = prunePlan.parse({
      now: 0,
      include_pinned: false,
      requested_classes: ['stale'],
      candidates: [candidate],
      by_reason: { stale: [candidate] },
      plan_signature: 'sig',
    });
    expect(p.by_reason.stale).toHaveLength(1);
  });

  it('rejects PruneCandidate with invalid action', () => {
    expect(() =>
      prunePlan.parse({
        now: 0,
        include_pinned: false,
        requested_classes: [],
        candidates: [
          {
            kind: 'workspace',
            reason: 'stale',
            action: 'nope',
            model_name: 'm',
            session_id: 's',
            path: '/p',
            age_seconds: 0,
            pinned: false,
          },
        ],
        by_reason: {},
        plan_signature: 'sig',
      }),
    ).toThrow();
  });

  it('accepts MaintenanceStats', () => {
    expect(
      maintenanceStats.parse({
        counters: { x: 1 },
        archive_root: '/r',
        total_workspaces: 0,
        total_bytes: 0,
        total_bundles: 0,
      }).counters.x,
    ).toBe(1);
  });

  it('accepts EnvironmentInfo', () => {
    const e = environmentInfo.parse({
      omlx_version: '1',
      python_version: '3',
      platform: { os: 'mac' },
      manifest_schema_version: '2',
      supported_manifest_versions: ['2'],
      bundle_version: '1',
      cache_layout: 'v1',
      archive_root: '/r',
      ssd_cache_dir: '/c',
      base_path: '/b',
      bundle_export_dir: '/e',
      bundle_import_dir: '/i',
    });
    expect(e.platform.os).toBe('mac');
  });

  it('accepts HealthCheckResult', () => {
    const h = healthCheckResult.parse({
      ok: true,
      checks: { archive: { ok: true, detail: 'writable' } },
      reported_at: 0,
    });
    expect(h.checks.archive.ok).toBe(true);
  });
});
