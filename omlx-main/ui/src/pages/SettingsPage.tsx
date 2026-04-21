import { useMutation, useQuery } from '@tanstack/react-query';
import * as api from '../lib/api';
import { ErrorBox, Section } from '../components/ui';

export default function SettingsPage() {
  const env = useQuery({ queryKey: ['env'], queryFn: api.envInfo });
  const health = useMutation({ mutationFn: api.health });

  return (
    <>
      <Section title="Environment">
        {env.isPending && <div>Loading…</div>}
        {env.error && <ErrorBox error={env.error} />}
        {env.data && (
          <div className="card">
            <dl className="grid grid-cols-1 gap-3 text-sm md:grid-cols-2">
              {Object.entries({
                'OMLX version': env.data.omlx_version,
                'Python': env.data.python_version,
                'Platform': `${env.data.platform.system} ${env.data.platform.machine} (${env.data.platform.release})`,
                'Manifest schema': env.data.manifest_schema_version,
                'Supported schema versions': env.data.supported_manifest_versions.join(', '),
                'Bundle version': env.data.bundle_version,
                'Cache layout': env.data.cache_layout,
                'Archive root': env.data.archive_root,
                'SSD cache dir': env.data.ssd_cache_dir,
                'Base path': env.data.base_path,
                'Bundle export dir': env.data.bundle_export_dir,
                'Bundle import dir': env.data.bundle_import_dir,
                'mlx-lm pin': env.data.mlx_lm_pinned ?? '—',
              }).map(([k, v]) => (
                <div key={k}>
                  <dt className="label">{k}</dt>
                  <dd className="font-mono break-words text-xs">{v}</dd>
                </div>
              ))}
            </dl>
          </div>
        )}
      </Section>

      <Section
        title="Health"
        actions={
          <button className="btn-primary" onClick={() => health.mutate()} disabled={health.isPending}>
            Run health check
          </button>
        }
      >
        {health.error && <ErrorBox error={health.error} />}
        {health.data && (
          <div className="card space-y-2">
            <div className={`font-semibold ${health.data.ok ? 'text-green-700' : 'text-red-700'}`}>
              Overall: {health.data.ok ? 'OK' : 'FAIL'}
            </div>
            <ul className="space-y-1 text-sm">
              {Object.entries(health.data.checks).map(([k, v]) => (
                <li key={k} className="flex items-start gap-2">
                  <span className={v.ok ? 'text-green-700' : 'text-red-700'}>{v.ok ? '✔' : '✘'}</span>
                  <span className="font-mono text-xs">{k}</span>
                  {v.detail && <span className="text-xs text-neutral-500">— {v.detail}</span>}
                </li>
              ))}
            </ul>
          </div>
        )}
      </Section>
    </>
  );
}
