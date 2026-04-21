import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import * as api from '../lib/api';
import { ErrorBox, Section } from '../components/ui';

export default function DiffPage() {
  const [l, setL] = useState({ model: '', session: '' });
  const [r, setR] = useState({ model: '', session: '' });
  const m = useMutation({
    mutationFn: () => api.diff(l.model, l.session, r.model, r.session),
  });
  const canGo = l.model && l.session && r.model && r.session;
  return (
    <Section title="Diff two sessions">
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <Side title="Left" value={l} onChange={setL} />
        <Side title="Right" value={r} onChange={setR} />
      </div>
      <div className="mt-3">
        <button className="btn-primary" disabled={!canGo || m.isPending} onClick={() => m.mutate()}>
          Compute diff
        </button>
      </div>
      {m.error && <div className="mt-3"><ErrorBox error={m.error} /></div>}
      {m.data && (
        <div className="mt-4 space-y-3">
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
  );
}

function Side({
  title,
  value,
  onChange,
}: {
  title: string;
  value: { model: string; session: string };
  onChange: (v: { model: string; session: string }) => void;
}) {
  return (
    <div className="card">
      <h3 className="mb-2 font-semibold">{title}</h3>
      <div className="space-y-2">
        <div>
          <label className="label">Model</label>
          <input className="input" value={value.model} onChange={(e) => onChange({ ...value, model: e.target.value })} />
        </div>
        <div>
          <label className="label">Session</label>
          <input className="input" value={value.session} onChange={(e) => onChange({ ...value, session: e.target.value })} />
        </div>
      </div>
    </div>
  );
}
