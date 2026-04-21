import { Link } from 'react-router-dom';
import { useEnvironmentInfo } from '../hooks';

export default function TopBar({ onToggleDiagnostics }: { onToggleDiagnostics: () => void }) {
  const env = useEnvironmentInfo();
  const root = env.data?.archive_root ?? '—';
  const version = env.data?.omlx_version ?? '…';
  return (
    <header className="flex items-center justify-between border-b border-neutral-200 bg-white px-4 py-2">
      <div className="flex items-center gap-4">
        <Link to="/" className="text-base font-semibold text-neutral-900">
          OMLX · Operator
        </Link>
        <span className="rounded bg-neutral-100 px-2 py-0.5 font-mono text-[11px] text-neutral-600">
          v{version}
        </span>
      </div>
      <div className="flex items-center gap-3">
        <span
          className="max-w-[40ch] truncate font-mono text-[11px] text-neutral-500"
          title={root}
        >
          archive: {root}
        </span>
        <button
          className="rounded border border-neutral-300 px-2 py-0.5 text-xs text-neutral-700 hover:bg-neutral-100"
          onClick={onToggleDiagnostics}
          aria-label="Toggle diagnostics panel"
          title="Toggle diagnostics"
        >
          diag
        </button>
      </div>
    </header>
  );
}
