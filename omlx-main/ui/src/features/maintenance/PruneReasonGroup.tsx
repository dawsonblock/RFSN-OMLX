import type { PrunePlan } from '../../types';

type Candidate = PrunePlan['candidates'][number];

export default function PruneReasonGroup({
  reason,
  candidates,
}: {
  reason: string;
  candidates: Candidate[];
}) {
  const eligible = candidates.filter((c) => c.action === 'eligible').length;
  const protectedCount = candidates.length - eligible;
  return (
    <details className="rounded border border-neutral-200 bg-white" open={eligible > 0}>
      <summary className="cursor-pointer px-3 py-2 text-sm">
        <span className="font-mono">{reason}</span>
        <span className="ml-2 text-neutral-500">
          {candidates.length} total · {eligible} eligible · {protectedCount} protected
        </span>
      </summary>
      <div className="overflow-x-auto border-t border-neutral-200">
        <table className="min-w-full divide-y divide-neutral-200 text-sm">
          <thead className="bg-neutral-50 text-left text-xs uppercase text-neutral-500">
            <tr>
              <th className="px-3 py-2">Kind</th>
              <th className="px-3 py-2">Action</th>
              <th className="px-3 py-2">Model / Session</th>
              <th className="px-3 py-2">Age</th>
              <th className="px-3 py-2">Pinned</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-neutral-100">
            {candidates.map((c, i) => (
              <tr key={i} className={c.action === 'eligible' ? 'bg-red-50' : ''}>
                <td className="px-3 py-2">{c.kind}</td>
                <td className="px-3 py-2">{c.action}</td>
                <td className="px-3 py-2 font-mono text-xs">
                  {c.model_name} / {c.session_id}
                </td>
                <td className="px-3 py-2 text-xs">
                  {(c.age_seconds / 86400).toFixed(1)}d
                </td>
                <td className="px-3 py-2">{c.pinned ? '📌' : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}
