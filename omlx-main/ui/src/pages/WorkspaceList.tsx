import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import * as api from '../lib/api';
import { ErrorBox, Grade, Loading, Section, Empty, formatTs } from '../components/ui';

const GRADES = [
  'healthy',
  'stale',
  'missing_blocks',
  'partially_exportable',
  'invalid_manifest',
  'unreadable',
  'incompatible_model',
] as const;

export default function WorkspaceList() {
  const qc = useQueryClient();
  const [status, setStatus] = useState('');
  const [pinned, setPinned] = useState('');
  const [exportable, setExportable] = useState('');
  const [model, setModel] = useState('');
  const [probe, setProbe] = useState(false);
  const [showCreate, setShowCreate] = useState(false);

  const params = new URLSearchParams();
  if (status) params.set('status', status);
  if (pinned) params.set('pinned', pinned);
  if (model) params.set('model_family', model);
  if (exportable) params.set('exportable', exportable);
  if (probe) params.set('probe_exportable', 'true');

  const q = useQuery({
    queryKey: ['workspaces', params.toString()],
    queryFn: () => api.listWorkspaces(params),
  });

  return (
    <>
      <Section
        title="Workspaces"
        actions={
          <button className="btn-primary" onClick={() => setShowCreate((v) => !v)}>
            {showCreate ? 'Close' : 'Create workspace'}
          </button>
        }
      >
        {showCreate && <CreateForm onDone={() => { setShowCreate(false); qc.invalidateQueries({ queryKey: ['workspaces'] }); }} />}
        <div className="card mb-4">
          <div className="grid grid-cols-1 gap-3 md:grid-cols-5">
            <div>
              <label className="label">Status</label>
              <select className="input" value={status} onChange={(e) => setStatus(e.target.value)}>
                <option value="">(any)</option>
                {GRADES.map((g) => (
                  <option key={g} value={g}>
                    {g}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="label">Pinned</label>
              <select className="input" value={pinned} onChange={(e) => setPinned(e.target.value)}>
                <option value="">(any)</option>
                <option value="true">pinned</option>
                <option value="false">unpinned</option>
              </select>
            </div>
            <div>
              <label className="label">Exportable</label>
              <select className="input" value={exportable} onChange={(e) => setExportable(e.target.value)}>
                <option value="">(any)</option>
                <option value="true">exportable</option>
                <option value="false">not exportable</option>
              </select>
            </div>
            <div>
              <label className="label">Model</label>
              <input className="input" value={model} onChange={(e) => setModel(e.target.value)} placeholder="exact name" />
            </div>
            <div className="flex items-end gap-2">
              <label className="text-sm">
                <input
                  type="checkbox"
                  className="mr-1"
                  checked={probe}
                  onChange={(e) => setProbe(e.target.checked)}
                />
                Probe exportable
              </label>
            </div>
          </div>
        </div>

        {q.isPending && <Loading />}
        {q.error && <ErrorBox error={q.error} />}
        {q.data && q.data.length === 0 && <Empty>No workspaces under the archive root.</Empty>}
        {q.data && q.data.length > 0 && (
          <div className="overflow-x-auto rounded-lg border border-neutral-200 bg-white">
            <table className="min-w-full divide-y divide-neutral-200 text-sm">
              <thead className="bg-neutral-50 text-left text-xs uppercase tracking-wide text-neutral-500">
                <tr>
                  <th className="px-3 py-2">Model</th>
                  <th className="px-3 py-2">Session</th>
                  <th className="px-3 py-2">Label</th>
                  <th className="px-3 py-2">Turns</th>
                  <th className="px-3 py-2">Grade</th>
                  <th className="px-3 py-2">Pinned</th>
                  <th className="px-3 py-2">Exportable</th>
                  <th className="px-3 py-2">Updated</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-neutral-100">
                {q.data.map((w) => (
                  <tr key={`${w.model_name}/${w.session_id}`} className="hover:bg-neutral-50">
                    <td className="px-3 py-2 font-mono text-xs">{w.model_name}</td>
                    <td className="px-3 py-2">
                      <Link
                        to={`/w/${encodeURIComponent(w.model_name)}/${encodeURIComponent(w.session_id)}`}
                        className="font-mono text-xs text-blue-700 hover:underline"
                      >
                        {w.session_id}
                      </Link>
                    </td>
                    <td className="px-3 py-2">{w.label ?? '—'}</td>
                    <td className="px-3 py-2">{w.turn_count}</td>
                    <td className="px-3 py-2"><Grade grade={w.integrity_grade} /></td>
                    <td className="px-3 py-2">{w.pinned ? '📌' : '—'}</td>
                    <td className="px-3 py-2">{w.exportable ? '✅' : '—'}</td>
                    <td className="px-3 py-2 text-xs text-neutral-500">{formatTs(w.updated_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>
    </>
  );
}

function CreateForm({ onDone }: { onDone: () => void }) {
  const [data, setData] = useState({
    model_name: '',
    session_id: '',
    label: '',
    description: '',
    task_tag: '',
    block_size: '',
  });
  const m = useMutation({
    mutationFn: () =>
      api.createWorkspace({
        model_name: data.model_name,
        session_id: data.session_id,
        label: data.label || null,
        description: data.description || null,
        task_tag: data.task_tag || null,
        block_size: data.block_size ? Number(data.block_size) : null,
      }),
    onSuccess: () => onDone(),
  });
  return (
    <div className="card mb-4">
      <h3 className="mb-2 font-semibold">Create workspace</h3>
      <div className="grid grid-cols-2 gap-3">
        {(['model_name', 'session_id', 'label', 'description', 'task_tag', 'block_size'] as const).map((k) => (
          <div key={k}>
            <label className="label">{k}</label>
            <input
              className="input"
              value={(data as Record<string, string>)[k]}
              onChange={(e) => setData({ ...data, [k]: e.target.value })}
            />
          </div>
        ))}
      </div>
      <div className="mt-3 flex gap-2">
        <button
          className="btn-primary"
          disabled={!data.model_name || !data.session_id || m.isPending}
          onClick={() => m.mutate()}
        >
          Create
        </button>
      </div>
      {m.error && <div className="mt-3"><ErrorBox error={m.error} /></div>}
    </div>
  );
}
