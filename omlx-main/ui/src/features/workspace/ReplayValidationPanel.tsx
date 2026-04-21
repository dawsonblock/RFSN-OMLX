import { useValidateWorkspace } from '../../hooks';
import { ErrorBox } from '../../components/ui';
import StatusPill from './StatusPill';

export default function ReplayValidationPanel({
  model,
  session,
}: {
  model: string;
  session: string;
}) {
  const m = useValidateWorkspace(model, session);
  return (
    <div className="card">
      <div className="mb-2 flex items-center justify-between">
        <div className="label">Replay / validation</div>
        <button className="btn-primary" onClick={() => m.mutate()} disabled={m.isPending}>
          Run validate
        </button>
      </div>
      {m.error && <ErrorBox error={m.error} />}
      {m.data && (
        <div className="mt-2 space-y-2 text-sm">
          <div className="flex items-center gap-2">
            <span className="label">Integrity</span>
            <StatusPill grade={m.data.integrity_grade} />
          </div>
          <div>
            <span className="label">Schema</span>{' '}
            {m.data.manifest_schema_version} ({m.data.schema_ok ? 'ok' : 'unsupported'})
          </div>
          <div>
            <span className="label">Exportable</span> {m.data.exportable ? 'yes' : 'no'}
          </div>
          <div>
            <span className="label">Blocks</span>{' '}
            {m.data.replay.present_blocks} / {m.data.replay.total_blocks}
          </div>
          {m.data.replay.missing_blocks.length > 0 && (
            <details>
              <summary className="cursor-pointer text-neutral-700">
                {m.data.replay.missing_blocks.length} missing blocks
              </summary>
              <ul className="mt-2 max-h-48 overflow-y-auto font-mono text-xs">
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
