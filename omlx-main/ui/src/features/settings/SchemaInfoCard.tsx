import type { EnvironmentInfo } from '../../types';

export default function SchemaInfoCard({ env }: { env: EnvironmentInfo }) {
  const rows: [string, string][] = [
    ['Manifest schema', env.manifest_schema_version],
    ['Supported schema versions', env.supported_manifest_versions.join(', ')],
    ['Bundle version', env.bundle_version],
    ['Cache layout', env.cache_layout],
  ];
  return (
    <div className="card">
      <div className="label mb-2">Schema &amp; versions</div>
      <dl className="grid grid-cols-1 gap-3 text-sm md:grid-cols-2">
        {rows.map(([k, v]) => (
          <div key={k}>
            <dt className="label">{k}</dt>
            <dd className="break-words font-mono text-xs">{v}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
