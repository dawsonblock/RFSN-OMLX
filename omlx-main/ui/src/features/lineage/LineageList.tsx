import { Link } from 'react-router-dom';
import type { LineageResponse } from '../../types';
import StatusPill from '../workspace/StatusPill';

export default function LineageList({ data }: { data: LineageResponse }) {
  const nodes = [...data.ancestors.slice().reverse(), ...data.descendants];
  return (
    <div className="rounded border border-neutral-200 bg-white p-3">
      {data.dangling_parent && (
        <div className="mb-3 rounded border border-orange-300 bg-orange-50 p-2 text-xs">
          Dangling parent:{' '}
          <span className="font-mono">{data.dangling_parent.join(' / ')}</span>
        </div>
      )}
      <ol className="space-y-1">
        {nodes.map((n, i) => (
          <li key={`${n.session_id}-${i}`} className="flex items-center gap-2 text-sm">
            <span
              className="inline-block w-8 text-right text-xs text-neutral-500"
              title={`role=${n.role} depth=${n.depth}`}
            >
              {n.role === 'self' ? '•' : n.depth > 0 ? '↳' : '↑'}
            </span>
            <span
              className="inline-block"
              style={{ paddingLeft: Math.abs(n.depth) * 12 }}
            />
            <Link
              to={`/w/${encodeURIComponent(n.model_name)}/${encodeURIComponent(n.session_id)}`}
              className="font-mono text-xs text-blue-700 hover:underline"
            >
              {n.session_id}
            </Link>
            <StatusPill grade={n.integrity_grade} />
            {n.pinned && <span title="pinned">📌</span>}
            {n.label && <span className="text-neutral-600">{n.label}</span>}
            {n.branch_reason && (
              <span className="truncate text-xs text-neutral-500">— {n.branch_reason}</span>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}
