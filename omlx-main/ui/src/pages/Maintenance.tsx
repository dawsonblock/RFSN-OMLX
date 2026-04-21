import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import * as api from '../lib/api';
import { ErrorBox, Empty, Section, formatBytes, formatTs } from '../components/ui';
import ConfirmModal from '../components/ConfirmModal';
import type { PrunePlan } from '../lib/schemas';

const CLASSES = [
  'stale',
  'invalid',
  'orphaned',
  'exports',
  'empty',
  'unreadable',
] as const;

export default function Maintenance() {
  const [classes, setClasses] = useState<string[]>([]);
  const [model, setModel] = useState('');
  const [includePinned, setIncludePinned] = useState(false);
  const [plan, setPlan] = useState<PrunePlan | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [execResult, setExecResult] = useState<unknown>(null);

  const stats = useQuery({ queryKey: ['mstats'], queryFn: api.maintenanceStats });

  const dry = useMutation({
    mutationFn: () =>
      api.pruneDryRun({
        classes,
        model_name: model || null,
        include_pinned: includePinned,
      }),
    onSuccess: (d) => setPlan(d),
  });

  const exec = useMutation({
    mutationFn: () => {
      if (!plan) throw new Error('no plan to execute');
      return api.pruneExecute({
        classes,
        model_name: model || null,
        include_pinned: includePinned,
        now: plan.now,
        plan_signature: plan.plan_signature,
        confirm: true,
      });
    },
    onSuccess: (d) => {
      setExecResult(d);
      setConfirmOpen(false);
      setPlan(null);
    },
  });

  const eligible = plan?.candidates.filter((c) => c.action === 'eligible') ?? [];

  return (
    <>
      <Section title="Archive stats">
        {stats.isPending && <div>Loading…</div>}
        {stats.error && <ErrorBox error={stats.error} />}
        {stats.data && (
          <div className="card grid grid-cols-1 gap-3 md:grid-cols-4">
            <Stat label="Archive root" value={stats.data.archive_root} mono />
            <Stat label="Workspaces" value={stats.data.total_workspaces} />
            <Stat label="Bundles" value={stats.data.total_bundles} />
            <Stat label="Total size" value={formatBytes(stats.data.total_bytes)} />
          </div>
        )}
      </Section>

      <Section title="Prune (dry-run by default)">
        <div className="card space-y-3">
          <div>
            <label className="label">Prune classes (select at least one)</label>
            <div className="mt-1 flex flex-wrap gap-3">
              {CLASSES.map((c) => (
                <label key={c} className="flex items-center gap-1 text-sm">
                  <input
                    type="checkbox"
                    checked={classes.includes(c)}
                    onChange={(e) => {
                      setClasses((prev) =>
                        e.target.checked ? [...prev, c] : prev.filter((x) => x !== c),
                      );
                      setPlan(null);
                    }}
                  />
                  <span className="font-mono text-xs">{c}</span>
                </label>
              ))}
            </div>
          </div>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
            <div>
              <label className="label">Model (optional)</label>
              <input className="input" value={model} onChange={(e) => { setModel(e.target.value); setPlan(null); }} />
            </div>
            <label className="flex items-end gap-2 text-sm">
              <input
                type="checkbox"
                checked={includePinned}
                onChange={(e) => { setIncludePinned(e.target.checked); setPlan(null); }}
              />
              Include pinned (rarely correct)
            </label>
          </div>
          <div className="flex gap-2">
            <button
              className="btn-primary"
              disabled={classes.length === 0 || dry.isPending}
              onClick={() => dry.mutate()}
            >
              Dry-run
            </button>
            {plan && eligible.length > 0 && (
              <button className="btn-danger" onClick={() => setConfirmOpen(true)}>
                Execute ({eligible.length})
              </button>
            )}
          </div>
          {dry.error && <ErrorBox error={dry.error} />}
        </div>
      </Section>

      {plan && (
        <Section title="Plan">
          <div className="card">
            <div className="mb-3 grid grid-cols-1 gap-2 text-sm md:grid-cols-4">
              <Stat label="Total" value={plan.candidates.length} />
              <Stat label="Eligible" value={eligible.length} />
              <Stat label="Protected" value={plan.candidates.length - eligible.length} />
              <Stat label="Signature" value={plan.plan_signature.slice(0, 12) + '…'} mono />
            </div>
            {plan.candidates.length === 0 ? (
              <Empty>No candidates.</Empty>
            ) : (
              <div className="overflow-x-auto rounded border border-neutral-200">
                <table className="min-w-full divide-y divide-neutral-200 text-sm">
                  <thead className="bg-neutral-50 text-left text-xs uppercase text-neutral-500">
                    <tr>
                      <th className="px-3 py-2">Kind</th>
                      <th className="px-3 py-2">Action</th>
                      <th className="px-3 py-2">Reason</th>
                      <th className="px-3 py-2">Model / Session</th>
                      <th className="px-3 py-2">Age</th>
                      <th className="px-3 py-2">Pinned</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-neutral-100">
                    {plan.candidates.map((c, i) => (
                      <tr key={i} className={c.action === 'eligible' ? 'bg-red-50' : ''}>
                        <td className="px-3 py-2">{c.kind}</td>
                        <td className="px-3 py-2">{c.action}</td>
                        <td className="px-3 py-2 font-mono text-xs">{c.reason}</td>
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
            )}
          </div>
        </Section>
      )}

      {execResult !== null && (
        <Section title="Execute result">
          <pre className="card max-h-80 overflow-auto text-xs">
            {JSON.stringify(execResult, null, 2)}
          </pre>
        </Section>
      )}
      {exec.error && <ErrorBox error={exec.error} />}

      <ConfirmModal
        open={confirmOpen}
        title="Execute prune"
        destructive
        confirmText="Execute prune"
        onCancel={() => setConfirmOpen(false)}
        onConfirm={() => exec.mutate()}
        requireTyping={plan?.plan_signature.slice(0, 6) ?? ''}
      >
        <p>
          This will permanently delete {eligible.length} eligible item(s). The plan signature
          <span className="mx-1 font-mono">{plan?.plan_signature.slice(0, 12)}</span>
          will be re-verified server-side.
        </p>
        <p className="mt-2">
          Type the first 6 characters of the signature to confirm.
        </p>
      </ConfirmModal>
    </>
  );
}

function Stat({ label, value, mono }: { label: string; value: string | number; mono?: boolean }) {
  return (
    <div>
      <div className="label">{label}</div>
      <div className={`text-sm ${mono ? 'font-mono' : ''} break-words`}>{value}</div>
    </div>
  );
}

// Silence formatTs TS6133 when not used (kept for future pages).
void formatTs;
