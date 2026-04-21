import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import * as api from '../lib/api';
import {
  ErrorBox,
  Grade,
  Loading,
  Section,
  formatTs,
} from '../components/ui';
import ConfirmModal from '../components/ConfirmModal';

export default function WorkspaceDetail() {
  const params = useParams();
  const model = params.model!;
  const session = params.session!;
  const [tab, setTab] = useState<'lineage' | 'turns' | 'validate'>('lineage');
  const [unpinOpen, setUnpinOpen] = useState(false);

  const qc = useQueryClient();
  const detail = useQuery({
    queryKey: ['ws', model, session],
    queryFn: () => api.getWorkspace(model, session, { validate: false }),
  });

  const pin = useMutation({
    mutationFn: (next: boolean) => api.setPinned(model, session, next),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['ws', model, session] });
      qc.invalidateQueries({ queryKey: ['workspaces'] });
    },
  });

  return (
    <>
      <div className="mb-4">
        <Link to="/" className="text-sm text-blue-700 hover:underline">
          ← Workspaces
        </Link>
      </div>

      {detail.isPending && <Loading />}
      {detail.error && <ErrorBox error={detail.error} />}
      {detail.data && (
        <>
          <Section
            title={`${detail.data.lineage.label ?? session}`}
            actions={
              <div className="flex gap-2">
                <Link
                  className="btn"
                  to={`/w/${encodeURIComponent(model)}/${encodeURIComponent(session)}/fork`}
                >
                  Fork
                </Link>
                {detail.data.pinned ? (
                  <button className="btn-danger" onClick={() => setUnpinOpen(true)}>
                    Unpin
                  </button>
                ) : (
                  <button className="btn-primary" onClick={() => pin.mutate(true)} disabled={pin.isPending}>
                    Pin
                  </button>
                )}
              </div>
            }
          >
            <div className="card grid grid-cols-1 gap-3 md:grid-cols-4">
              <Field label="Model" mono>{detail.data.model_name}</Field>
              <Field label="Session" mono>{detail.data.session_id}</Field>
              <Field label="Head turn" mono>{detail.data.lineage.head_turn_id || '—'}</Field>
              <Field label="Turns">{detail.data.lineage.turn_count}</Field>
              <Field label="Integrity"><Grade grade={detail.data.integrity_grade} /></Field>
              <Field label="Pinned">{detail.data.pinned ? 'yes' : 'no'}</Field>
              <Field label="Exportable">{detail.data.exportable ? 'yes' : 'no'}</Field>
              <Field label="Updated">{formatTs(detail.data.lineage.updated_at)}</Field>
              <Field label="Parent">
                {detail.data.lineage.parent
                  ? `${detail.data.lineage.parent[0]} @ ${detail.data.lineage.parent[1].slice(0, 12)}`
                  : '—'}
              </Field>
              <Field label="Branch reason" full>
                {detail.data.branch_reason ?? '—'}
              </Field>
              <Field label="Description" full>
                {detail.data.lineage.description ?? '—'}
              </Field>
            </div>
          </Section>

          <MetadataEditor model={model} session={session} initial={{
            label: detail.data.lineage.label ?? '',
            description: detail.data.lineage.description ?? '',
            task_tag: detail.data.lineage.task_tag ?? '',
          }} />

          <div className="mb-3 flex gap-2 border-b border-neutral-200">
            {(['lineage', 'turns', 'validate'] as const).map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={`-mb-px border-b-2 px-3 py-1.5 text-sm font-medium ${
                  tab === t ? 'border-blue-600 text-blue-700' : 'border-transparent text-neutral-600 hover:text-neutral-900'
                }`}
              >
                {t}
              </button>
            ))}
          </div>
          {tab === 'lineage' && <LineageTab model={model} session={session} />}
          {tab === 'turns' && (
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
                  {detail.data.turns.map((t) => (
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
          )}
          {tab === 'validate' && <ValidateTab model={model} session={session} />}
        </>
      )}

      <ConfirmModal
        open={unpinOpen}
        title="Unpin workspace"
        destructive
        confirmText="Unpin"
        onCancel={() => setUnpinOpen(false)}
        onConfirm={() => {
          setUnpinOpen(false);
          pin.mutate(false);
        }}
      >
        Unpinning removes prune protection. The workspace itself is not deleted.
      </ConfirmModal>
    </>
  );
}

function Field({
  label,
  children,
  mono,
  full,
}: {
  label: string;
  children: React.ReactNode;
  mono?: boolean;
  full?: boolean;
}) {
  return (
    <div className={full ? 'md:col-span-4' : ''}>
      <div className="label">{label}</div>
      <div className={`text-sm ${mono ? 'font-mono' : ''} break-words`}>{children}</div>
    </div>
  );
}

function MetadataEditor({
  model,
  session,
  initial,
}: {
  model: string;
  session: string;
  initial: { label: string; description: string; task_tag: string };
}) {
  const [form, setForm] = useState(initial);
  const qc = useQueryClient();
  const m = useMutation({
    mutationFn: () =>
      api.updateMetadata(model, session, {
        label: form.label || null,
        description: form.description || null,
        task_tag: form.task_tag || null,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['ws', model, session] }),
  });
  return (
    <Section title="Metadata">
      <div className="card">
        <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
          {(['label', 'description', 'task_tag'] as const).map((k) => (
            <div key={k}>
              <label className="label">{k}</label>
              <input
                className="input"
                value={form[k]}
                onChange={(e) => setForm({ ...form, [k]: e.target.value })}
              />
            </div>
          ))}
        </div>
        <div className="mt-3 flex gap-2">
          <button className="btn-primary" onClick={() => m.mutate()} disabled={m.isPending}>
            Save
          </button>
          {m.isSuccess && <span className="text-sm text-green-700">Saved.</span>}
        </div>
        {m.error && <div className="mt-3"><ErrorBox error={m.error} /></div>}
      </div>
    </Section>
  );
}

function LineageTab({ model, session }: { model: string; session: string }) {
  const q = useQuery({
    queryKey: ['lineage', model, session],
    queryFn: () => api.getLineage(model, session),
  });
  if (q.isPending) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  if (!q.data) return null;
  const nodes = [...q.data.ancestors.slice().reverse(), ...q.data.descendants];
  return (
    <div className="rounded border border-neutral-200 bg-white p-3">
      {q.data.dangling_parent && (
        <div className="mb-3 rounded border border-orange-300 bg-orange-50 p-2 text-xs">
          Dangling parent: <span className="font-mono">{q.data.dangling_parent.join(' / ')}</span>
        </div>
      )}
      <ol className="space-y-1">
        {nodes.map((n, i) => (
          <li key={`${n.session_id}-${i}`} className="flex items-center gap-2 text-sm">
            <span
              className="inline-block w-8 text-right text-xs text-neutral-500"
              title={`depth=${n.depth}`}
            >
              {n.depth > 0 ? '↳' : n.depth < 0 ? '↑' : '•'}
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
            <Grade grade={n.integrity_grade} />
            {n.pinned && <span>📌</span>}
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

function ValidateTab({ model, session }: { model: string; session: string }) {
  const m = useMutation({
    mutationFn: () => api.validateWorkspace(model, session),
  });
  return (
    <div className="card">
      <button className="btn-primary" onClick={() => m.mutate()} disabled={m.isPending}>
        Run validate
      </button>
      {m.error && <div className="mt-3"><ErrorBox error={m.error} /></div>}
      {m.data && (
        <div className="mt-4 space-y-2 text-sm">
          <div><span className="label">Integrity</span> <Grade grade={m.data.integrity_grade} /></div>
          <div><span className="label">Schema</span> {m.data.manifest_schema_version} ({m.data.schema_ok ? 'ok' : 'unsupported'})</div>
          <div><span className="label">Exportable</span> {m.data.exportable ? 'yes' : 'no'}</div>
          <div><span className="label">Blocks</span> {m.data.replay.present_blocks} / {m.data.replay.total_blocks}</div>
          {m.data.replay.missing_blocks.length > 0 && (
            <details>
              <summary className="cursor-pointer text-neutral-700">
                {m.data.replay.missing_blocks.length} missing blocks
              </summary>
              <ul className="mt-2 max-h-48 overflow-y-auto text-xs font-mono">
                {m.data.replay.missing_blocks.map((b) => (
                  <li key={b}>{b}</li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
    </div>
  );
}
