import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useWorkspaces, useCreateWorkspace } from '../hooks';
import { ErrorBox, Grade, Loading, Section, Empty, formatTs } from '../components/ui';
import { listModels } from '../lib/chat';

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
  const [status, setStatus] = useState('');
  const [pinned, setPinned] = useState('');
  const [exportable, setExportable] = useState('');
  const [model, setModel] = useState('');
  const [probe, setProbe] = useState(false);
  const [showCreate, setShowCreate] = useState(false);

  const params = useMemo(() => {
    const p = new URLSearchParams();
    if (status) p.set('status', status);
    if (pinned) p.set('pinned', pinned);
    if (model) p.set('model_family', model);
    if (exportable) p.set('exportable', exportable);
    if (probe) p.set('probe_exportable', 'true');
    return p;
  }, [status, pinned, model, exportable, probe]);

  const q = useWorkspaces(params);

  return (
    <Section
      title="Workspaces"
      actions={
        <button className="btn-primary" onClick={() => setShowCreate((v) => !v)}>
          {showCreate ? 'Close' : 'Create workspace'}
        </button>
      }
    >
      {showCreate && <CreateForm onDone={() => setShowCreate(false)} />}
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
            <select
              className="input"
              value={exportable}
              onChange={(e) => setExportable(e.target.value)}
            >
              <option value="">(any)</option>
              <option value="true">exportable</option>
              <option value="false">not exportable</option>
            </select>
          </div>
          <div>
            <label className="label">Model</label>
            <input
              className="input"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder="exact name"
            />
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
                  <td className="px-3 py-2">
                    <Grade grade={w.integrity_grade} />
                  </td>
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
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  useEffect(() => {
    let alive = true;
    listModels()
      .then((ids) => {
        if (alive) setAvailableModels(ids);
      })
      .catch(() => {
        /* no loaded models yet; user can type one in */
      });
    return () => {
      alive = false;
    };
  }, []);
  const m = useCreateWorkspace();
  const update = (k: keyof typeof data, v: string) => setData((d) => ({ ...d, [k]: v }));
  const submit = () =>
    m.mutate(
      {
        model_name: data.model_name.trim(),
        session_id: data.session_id.trim(),
        label: data.label.trim() || null,
        description: data.description.trim() || null,
        task_tag: data.task_tag.trim() || null,
        block_size: data.block_size ? Number(data.block_size) : null,
      },
      { onSuccess: onDone },
    );
  return (
    <div className="card mb-4">
      <h3 className="mb-1 font-semibold">Create workspace</h3>
      <p className="mb-3 text-xs text-neutral-500">
        A workspace is a persistent (model, session) pair whose KV cache is archived on
        disk. Only the first two fields are required. See the{' '}
        <a href="/ui/help" className="underline">Help tab</a> for details.
      </p>

      <div className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-neutral-500">
        Required
      </div>
      <div className="mb-4 grid grid-cols-1 gap-3 md:grid-cols-2">
        <div>
          <label className="label">Model <span className="text-red-600">*</span></label>
          {availableModels.length > 0 ? (
            <select
              className="input"
              value={data.model_name}
              onChange={(e) => update('model_name', e.target.value)}
            >
              <option value="">— pick a loaded model —</option>
              {availableModels.map((mm) => (
                <option key={mm} value={mm}>
                  {mm}
                </option>
              ))}
            </select>
          ) : (
            <input
              className="input"
              value={data.model_name}
              placeholder="e.g. llama"
              onChange={(e) => update('model_name', e.target.value)}
            />
          )}
          <p className="mt-1 text-xs text-neutral-500">
            The runtime id you see in the Chat model dropdown (not the HuggingFace
            repo id).
          </p>
        </div>
        <div>
          <label className="label">Session ID <span className="text-red-600">*</span></label>
          <input
            className="input"
            value={data.session_id}
            placeholder="e.g. notes, debug-1, ticket-4821"
            onChange={(e) => update('session_id', e.target.value)}
          />
          <p className="mt-1 text-xs text-neutral-500">
            Unique within this model. Treat it like a filename.
          </p>
        </div>
      </div>

      <div className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-neutral-500">
        Optional
      </div>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <div>
          <label className="label">Label</label>
          <input
            className="input"
            value={data.label}
            placeholder="Human-readable title"
            onChange={(e) => update('label', e.target.value)}
          />
        </div>
        <div>
          <label className="label">Description</label>
          <input
            className="input"
            value={data.description}
            placeholder="What is this workspace for?"
            onChange={(e) => update('description', e.target.value)}
          />
        </div>
        <div>
          <label className="label">Task tag</label>
          <input
            className="input"
            value={data.task_tag}
            placeholder="e.g. eval, demo, keep"
            onChange={(e) => update('task_tag', e.target.value)}
          />
          <p className="mt-1 text-xs text-neutral-500">
            Used by pruning policies and export filters.
          </p>
        </div>
        <div>
          <label className="label">KV block size (tokens)</label>
          <input
            className="input"
            type="number"
            min="1"
            value={data.block_size}
            placeholder="default (256)"
            onChange={(e) => update('block_size', e.target.value)}
          />
          <p className="mt-1 text-xs text-neutral-500">
            Smaller = finer fork points, more disk. Leave blank unless you have a
            reason.
          </p>
        </div>
      </div>

      <div className="mt-4 flex gap-2">
        <button
          className="btn-primary"
          disabled={!data.model_name.trim() || !data.session_id.trim() || m.isPending}
          onClick={submit}
        >
          {m.isPending ? 'Creating…' : 'Create'}
        </button>
      </div>
      {m.error && (
        <div className="mt-3">
          <ErrorBox error={m.error} />
        </div>
      )}
    </div>
  );
}
