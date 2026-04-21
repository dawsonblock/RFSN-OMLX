import { useEnvironmentInfo, useHealthCheck } from '../hooks';
import { ErrorBox, Section } from '../components/ui';
import EnvironmentCard from '../features/settings/EnvironmentCard';
import SchemaInfoCard from '../features/settings/SchemaInfoCard';

export default function SettingsPage() {
  const env = useEnvironmentInfo();
  const health = useHealthCheck();

  return (
    <>
      <Section title="Environment">
        {env.isPending && <div>Loading…</div>}
        {env.error && <ErrorBox error={env.error} />}
        {env.data && (
          <div className="grid grid-cols-1 gap-3">
            <EnvironmentCard env={env.data} />
            <SchemaInfoCard env={env.data} />
          </div>
        )}
      </Section>

      <Section
        title="Health"
        actions={
          <button
            className="btn-primary"
            onClick={() => health.mutate()}
            disabled={health.isPending}
          >
            Run health check
          </button>
        }
      >
        {health.error && <ErrorBox error={health.error} />}
        {health.data && (
          <div className="card space-y-2">
            <div
              className={`font-semibold ${health.data.ok ? 'text-green-700' : 'text-red-700'}`}
            >
              Overall: {health.data.ok ? 'OK' : 'FAIL'}
            </div>
            <ul className="space-y-1 text-sm">
              {Object.entries(health.data.checks).map(([k, v]) => (
                <li key={k} className="flex items-start gap-2">
                  <span className={v.ok ? 'text-green-700' : 'text-red-700'}>
                    {v.ok ? '✔' : '✘'}
                  </span>
                  <span className="font-mono text-xs">{k}</span>
                  {v.detail && (
                    <span className="text-xs text-neutral-500">— {v.detail}</span>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}
      </Section>
    </>
  );
}
