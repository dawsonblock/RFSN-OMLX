import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useWorkspace, usePinWorkspace, useUpdateMetadata } from '../hooks';
import { ErrorBox, Loading, Section } from '../components/ui';
import ConfirmModal from '../components/ConfirmModal';
import WorkspaceHeader from '../features/workspace/WorkspaceHeader';
import WorkspaceActionBar from '../features/workspace/WorkspaceActionBar';
import TurnTimeline from '../features/workspace/TurnTimeline';
import ReplayValidationPanel from '../features/workspace/ReplayValidationPanel';
import IntegrityPanel from '../features/workspace/IntegrityPanel';
import CompatibilityPanel from '../features/workspace/CompatibilityPanel';

export default function WorkspaceDetail() {
  const { model = '', session = '' } = useParams();
  const [unpinOpen, setUnpinOpen] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);

  const detail = useWorkspace(model, session, { include_raw: showAdvanced });
  const pin = usePinWorkspace(model, session);

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
            title={detail.data.lineage.label ?? session}
            actions={
              <WorkspaceActionBar
                detail={detail.data}
                pinPending={pin.isPending}
                onPinToggle={(next) => {
                  if (!next) {
                    setUnpinOpen(true);
                  } else {
                    pin.mutate(true);
                  }
                }}
              />
            }
          >
            <WorkspaceHeader detail={detail.data} />
          </Section>

          <Section title="Status">
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              <IntegrityPanel detail={detail.data} />
              <CompatibilityPanel detail={detail.data} />
            </div>
          </Section>

          <Section title="Validate / replay">
            <ReplayValidationPanel model={model} session={session} />
          </Section>

          <Section title="Metadata">
            <MetadataEditor
              model={model}
              session={session}
              initial={{
                label: detail.data.lineage.label ?? '',
                description: detail.data.lineage.description ?? '',
                task_tag: detail.data.lineage.task_tag ?? '',
              }}
            />
          </Section>

          <Section title={`Turns (${detail.data.turns.length})`}>
            <TurnTimeline detail={detail.data} />
          </Section>

          <Section title="Advanced">
            <div className="card">
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={showAdvanced}
                  onChange={(e) => setShowAdvanced(e.target.checked)}
                />
                Show raw manifest
              </label>
              {showAdvanced && detail.data.raw && (
                <pre className="mt-2 max-h-96 overflow-auto rounded bg-neutral-50 p-3 text-[11px]">
{JSON.stringify(detail.data.raw, null, 2)}
                </pre>
              )}
            </div>
          </Section>

          <Section title="Resume">
            <div className="card text-sm text-neutral-700">
              <p>
                Resume is informational: the workspace can be continued from its head
                turn using the OMLX runtime or CLI. The UI does not commit or
                checkpoint itself — it only surfaces status and compatibility.
              </p>
              <ul className="mt-2 list-disc pl-5 text-sm">
                <li>
                  Head turn:{' '}
                  <span className="font-mono">
                    {detail.data.lineage.head_turn_id || '—'}
                  </span>
                </li>
                <li>
                  Exportable: {detail.data.exportable ? 'yes' : 'no'}
                </li>
                <li>
                  Integrity: {detail.data.integrity_grade}
                </li>
              </ul>
            </div>
          </Section>
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
  const m = useUpdateMetadata(model, session);
  return (
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
        <button
          className="btn-primary"
          onClick={() =>
            m.mutate({
              label: form.label || null,
              description: form.description || null,
              task_tag: form.task_tag || null,
            })
          }
          disabled={m.isPending}
        >
          Save
        </button>
        {m.isSuccess && <span className="text-sm text-green-700">Saved.</span>}
      </div>
      {m.error && <div className="mt-3"><ErrorBox error={m.error} /></div>}
    </div>
  );
}
