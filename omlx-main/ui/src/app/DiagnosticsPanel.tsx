import { useIsFetching, useIsMutating } from '@tanstack/react-query';
import { useEnvironmentInfo, useHealthCheck } from '../hooks';
import { ErrorBox } from '../components/ui';

export default function DiagnosticsPanel() {
  const env = useEnvironmentInfo();
  const health = useHealthCheck();
  const fetching = useIsFetching();
  const mutating = useIsMutating();

  return (
    <aside className="flex h-full w-80 flex-col gap-3 overflow-y-auto border-l border-neutral-200 bg-neutral-50 p-3 text-sm">
      <div>
        <div className="label">Queries</div>
        <div className="font-mono text-xs">
          fetching: {fetching} · mutating: {mutating}
        </div>
      </div>

      <div>
        <div className="label">Environment</div>
        {env.data ? (
          <ul className="space-y-0.5 font-mono text-[11px] text-neutral-700">
            <li>omlx: {env.data.omlx_version}</li>
            <li>python: {env.data.python_version}</li>
            <li>manifest: {env.data.manifest_schema_version}</li>
            <li>bundle: {env.data.bundle_version}</li>
            <li className="truncate" title={env.data.archive_root}>
              archive: {env.data.archive_root}
            </li>
            <li className="truncate" title={env.data.ssd_cache_dir}>
              ssd: {env.data.ssd_cache_dir}
            </li>
          </ul>
        ) : env.error ? (
          <ErrorBox error={env.error} />
        ) : (
          <div className="text-xs text-neutral-500">loading…</div>
        )}
      </div>

      <div>
        <div className="label">Health</div>
        <button
          className="btn mb-2"
          onClick={() => health.mutate()}
          disabled={health.isPending}
        >
          Run health check
        </button>
        {health.error && <ErrorBox error={health.error} />}
        {health.data && (
          <ul className="space-y-0.5 text-xs">
            <li className={health.data.ok ? 'text-green-700' : 'text-red-700'}>
              overall: {health.data.ok ? 'ok' : 'fail'}
            </li>
            {Object.entries(health.data.checks).map(([k, v]) => (
              <li key={k} className={v.ok ? 'text-green-700' : 'text-red-700'}>
                {v.ok ? '✔' : '✘'} {k} {v.detail && <span className="text-neutral-500">— {v.detail}</span>}
              </li>
            ))}
          </ul>
        )}
      </div>
    </aside>
  );
}
