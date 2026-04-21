import type { WorkspaceDetail } from '../../types';

export default function CompatibilityPanel({ detail }: { detail: WorkspaceDetail }) {
  const c = detail.lineage.model_compat;
  return (
    <div className="card">
      <div className="label mb-1">Model compatibility</div>
      <dl className="grid grid-cols-1 gap-1 text-sm md:grid-cols-3">
        <div>
          <dt className="label">Model</dt>
          <dd className="font-mono text-xs">{c.model_name}</dd>
        </div>
        <div>
          <dt className="label">Block size</dt>
          <dd className="font-mono text-xs">{c.block_size ?? '—'}</dd>
        </div>
        <div>
          <dt className="label">Schema</dt>
          <dd className="font-mono text-xs">{c.schema}</dd>
        </div>
      </dl>
    </div>
  );
}
