import type { BundleInfo } from '../../types';
import { formatBytes, formatTs } from '../../components/ui';

export default function BundleProvenanceCard({ bundle }: { bundle: BundleInfo }) {
  const env = bundle.envelope ?? {};
  const man = bundle.manifest ?? {};
  const safe = (v: unknown): string => {
    if (v == null) return '—';
    if (typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean')
      return String(v);
    return JSON.stringify(v);
  };
  return (
    <div className="card space-y-3">
      <div>
        <div className="label">Bundle</div>
        <div className="font-mono text-xs">{bundle.path}</div>
        <div className="text-xs text-neutral-500">
          {formatBytes(bundle.size_bytes)} · modified {formatTs(bundle.mtime)}
          {bundle.pinned && ' · 📌 pinned'}
        </div>
      </div>

      <div>
        <div className="label mb-1">Envelope</div>
        <dl className="grid grid-cols-1 gap-1 text-xs md:grid-cols-2">
          <div><dt className="label">bundle_version</dt><dd className="font-mono">{safe(env.bundle_version)}</dd></div>
          <div><dt className="label">source_session_id</dt><dd className="font-mono">{safe(env.source_session_id)}</dd></div>
          <div><dt className="label">model_name</dt><dd className="font-mono">{safe(env.model_name)}</dd></div>
          <div><dt className="label">block_size</dt><dd className="font-mono">{safe(env.block_size)}</dd></div>
          <div><dt className="label">exported_at</dt><dd className="font-mono">{safe(env.exported_at)}</dd></div>
          <div><dt className="label">grade</dt><dd className="font-mono">{safe(env.grade)}</dd></div>
        </dl>
      </div>

      <div>
        <div className="label mb-1">Manifest</div>
        <dl className="grid grid-cols-1 gap-1 text-xs md:grid-cols-2">
          <div><dt className="label">schema</dt><dd className="font-mono">{safe(man.schema)}</dd></div>
          <div><dt className="label">head_turn_id</dt><dd className="font-mono">{safe(man.head_turn_id)}</dd></div>
          <div><dt className="label">turn_count</dt><dd className="font-mono">{safe(man.turn_count)}</dd></div>
          <div><dt className="label">label</dt><dd className="font-mono">{safe(man.label)}</dd></div>
        </dl>
      </div>

      <details>
        <summary className="cursor-pointer text-xs text-neutral-500">
          Raw envelope + manifest (advanced)
        </summary>
        <pre className="mt-2 max-h-64 overflow-auto rounded bg-neutral-50 p-3 text-[11px]">
{JSON.stringify({ envelope: env, manifest: man }, null, 2)}
        </pre>
      </details>
    </div>
  );
}
