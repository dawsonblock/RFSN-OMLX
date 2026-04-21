import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useMutation } from '@tanstack/react-query';
import * as api from '../lib/api';
import { ErrorBox, Section } from '../components/ui';

export default function WorkspaceDiffPage() {
  const { model = '', session = '' } = useParams();
  const [right, setRight] = useState({ model: '', session: '' });
  const m = useMutation({
    mutationFn: () => api.diff(model, session, right.model, right.session),
  });
  const canGo = right.model && right.session;
  return (
    <>
      <div className="mb-4">
        <Link
          to={`/w/${encodeURIComponent(model)}/${encodeURIComponent(session)}`}
          className="text-sm text-blue-700 hover:underline"
        >
          ← {session}
        </Link>
      </div>
      <Section title={`Diff · ${session} ↔ …`}>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          <div className="card">
            <h3 className="mb-2 font-semibold">Left (this workspace)</h3>
            <div className="space-y-1 text-sm">
              <div>
                <span className="label">Model</span>
                <div className="font-mono text-xs">{model}</div>
              </div>
              <div>
                <span className="label">Session</span>
                <div className="font-mono text-xs">{session}</div>
              </div>
            </div>
          </div>
          <div className="card">
            <h3 className="mb-2 font-semibold">Right</h3>
            <div className="space-y-2">
              <div>
                <label className="label">Model</label>
                <input
                  className="input"
                  value={right.model}
                  onChange={(e) => setRight({ ...right, model: e.target.value })}
                />
              </div>
              <div>
                <label className="label">Session</label>
                <input
                  className="input"
                  value={right.session}
                  onChange={(e) => setRight({ ...right, session: e.target.value })}
                />
              </div>
            </div>
          </div>
        </div>
        <div className="mt-3">
          <button
            className="btn-primary"
            disabled={!canGo || m.isPending}
            onClick={() => m.mutate()}
          >
            Compute diff
          </button>
        </div>
        {m.error && <div className="mt-3"><ErrorBox error={m.error} /></div>}
        {m.data && (
          <div className="mt-4 space-y-3">
            <div className="rounded border border-amber-300 bg-amber-50 p-2 text-xs text-amber-900">
              Structural diff only. Common ancestor detection is depth-1; turn/block
              content is compared by ids, not bytes.
            </div>
            <div className="card">
              <div className="grid grid-cols-1 gap-2 text-sm md:grid-cols-3">
                <div>
                  <div className="label">Common ancestor</div>
                  <div className="font-mono text-xs">
                    {m.data.common_ancestor ? m.data.common_ancestor.join(' / ') : '—'}
                  </div>
                </div>
                <div>
                  <div className="label">Turns (A / B)</div>
                  <div>{m.data.turn_count_a} / {m.data.turn_count_b}</div>
                </div>
                <div>
                  <div className="label">Shared turns</div>
                  <div>{m.data.shared_turn_count}</div>
                </div>
              </div>
            </div>
            <div className="overflow-x-auto rounded border border-neutral-200 bg-white">
              <table className="min-w-full divide-y divide-neutral-200 text-sm">
                <thead className="bg-neutral-50 text-left text-xs uppercase text-neutral-500">
                  <tr>
                    <th className="px-3 py-2">#</th>
                    <th className="px-3 py-2">Turn A</th>
                    <th className="px-3 py-2">Turn B</th>
                    <th className="px-3 py-2">Blocks A</th>
                    <th className="px-3 py-2">Blocks B</th>
                    <th className="px-3 py-2">Common prefix</th>
                    <th className="px-3 py-2">Diverged</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-neutral-100">
                  {m.data.per_turn.map((t, i) => (
                    <tr key={i}>
                      <td className="px-3 py-2">{i + 1}</td>
                      <td className="px-3 py-2 font-mono text-xs">{t.turn_id_a?.slice(0, 12) ?? '—'}</td>
                      <td className="px-3 py-2 font-mono text-xs">{t.turn_id_b?.slice(0, 12) ?? '—'}</td>
                      <td className="px-3 py-2">{t.block_count_a}</td>
                      <td className="px-3 py-2">{t.block_count_b}</td>
                      <td className="px-3 py-2">{t.common_prefix_blocks}</td>
                      <td className="px-3 py-2">{t.diverged ? '⚠️' : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </Section>
    </>
  );
}
