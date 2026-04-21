import { useMemo, useState } from 'react';
import { useMaintenanceStats, usePruneDryRun, usePruneExecute } from '../hooks';
import { ErrorBox, Empty, Section, formatBytes } from '../components/ui';
import PruneReasonGroup from '../features/maintenance/PruneReasonGroup';
import DangerConfirmDialog from '../features/maintenance/DangerConfirmDialog';
import type { PrunePlan } from '../types';

const CLASSES = ['stale', 'invalid', 'orphaned', 'exports', 'empty', 'unreadable'] as const;

export default function Maintenance() {
  const [classes, setClasses] = useState<string[]>([]);
  const [model, setModel] = useState('');
  const [includePinned, setIncludePinned] = useState(false);
  const [plan, setPlan] = useState<PrunePlan | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [execResult, setExecResult] = useState<unknown>(null);

  const stats = useMaintenanceStats();
  const dry = usePruneDryRun();
  const exec = usePruneExecute();

  const eligible = plan?.candidates.filter((c) => c.action === 'eligible') ?? [];
  const groups = useMemo(() => {
    if (!plan) return [] as [string, PrunePlan['candidates']][];
    return Object.entries(plan.by_reason);
  }, [plan]);

  const runDryRun = () =>
    dry.mutate(
      { classes, model_name: model || null, include_pinned: includePinned },
      { onSuccess: (d) => setPlan(d) },
    );

  const runExecute = () => {
    if (!plan) return;
    exec.mutate(
      {
        classes,
        model_name: model || null,
        include_pinned: includePinned,
        now: plan.now,
        plan_signature: plan.plan_signature,
        confirm: true,
      },
      {
        onSuccess: (d) => {
          setExecResult(d);
          setConfirmOpen(false);
          setPlan(null);
        },
      },
    );
  };

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
              <input
                className="input"
                value={model}
                onChange={(e) => { setModel(e.target.value); setPlan(null); }}
              />
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
              onClick={runDryRun}
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
              <Stat
                label="Signature"
                value={plan.plan_signature.slice(0, 12) + '…'}
                mono
              />
            </div>
            {plan.candidates.length === 0 ? (
              <Empty>No candidates.</Empty>
            ) : (
              <div className="space-y-2">
                {groups.map(([reason, candidates]) => (
                  <PruneReasonGroup
                    key={reason}
                    reason={reason}
                    candidates={candidates}
                  />
                ))}
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

      <DangerConfirmDialog
        open={confirmOpen}
        title="Execute prune"
        confirmText="Execute prune"
        requireTyping={plan?.plan_signature.slice(0, 6) ?? ''}
        onCancel={() => setConfirmOpen(false)}
        onConfirm={runExecute}
      >
        <p>
          This will permanently delete {eligible.length} eligible item(s). The plan
          signature{' '}
          <span className="mx-1 font-mono">
            {plan?.plan_signature.slice(0, 12)}
          </span>
          will be re-verified server-side.
        </p>
        <p className="mt-2">Type the first 6 characters of the signature to confirm.</p>
      </DangerConfirmDialog>
    </>
  );
}

function Stat({
  label,
  value,
  mono,
}: {
  label: string;
  value: string | number;
  mono?: boolean;
}) {
  return (
    <div>
      <div className="label">{label}</div>
      <div className={`text-sm ${mono ? 'font-mono' : ''} break-words`}>{value}</div>
    </div>
  );
}
