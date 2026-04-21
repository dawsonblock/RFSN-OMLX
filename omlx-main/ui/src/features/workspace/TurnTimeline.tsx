import type { WorkspaceDetail } from '../../types';
import { formatTs, Empty } from '../../components/ui';

export default function TurnTimeline({ detail }: { detail: WorkspaceDetail }) {
  if (detail.turns.length === 0) return <Empty>No turns yet.</Empty>;
  return (
    <div className="overflow-x-auto rounded border border-neutral-200 bg-white">
      <table className="min-w-full divide-y divide-neutral-200 text-sm">
        <thead className="bg-neutral-50 text-left text-xs uppercase text-neutral-500">
          <tr>
            <th className="px-3 py-2">Turn</th>
            <th className="px-3 py-2">Committed</th>
            <th className="px-3 py-2">Blocks</th>
            <th className="px-3 py-2">Branch reason</th>
            <th className="px-3 py-2">Note</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-neutral-100">
          {detail.turns.map((t) => (
            <tr key={t.turn_id}>
              <td className="px-3 py-2 font-mono text-xs">{t.turn_id.slice(0, 14)}</td>
              <td className="px-3 py-2 text-xs">{formatTs(t.committed_at)}</td>
              <td className="px-3 py-2">{t.block_count}</td>
              <td className="px-3 py-2">{t.branch_reason ?? '—'}</td>
              <td className="px-3 py-2">{t.note ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
