import type { EnvironmentInfo } from '../../types';

export default function EnvironmentCard({ env }: { env: EnvironmentInfo }) {
  const rows: [string, string][] = [
    ['OMLX version', env.omlx_version],
    ['Python', env.python_version],
    ['Platform', `${env.platform.system} ${env.platform.machine} (${env.platform.release})`],
    ['Archive root', env.archive_root],
    ['SSD cache dir', env.ssd_cache_dir],
    ['Base path', env.base_path],
    ['Bundle export dir', env.bundle_export_dir],
    ['Bundle import dir', env.bundle_import_dir],
    ['mlx-lm pin', env.mlx_lm_pinned ?? '—'],
  ];
  return (
    <div className="card">
      <div className="label mb-2">Environment</div>
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
