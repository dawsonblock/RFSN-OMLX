import type { ReactNode } from 'react';

export function Grade({ grade }: { grade: string }) {
  const map: Record<string, string> = {
    healthy: 'bg-green-100 text-green-800',
    stale: 'bg-yellow-100 text-yellow-800',
    missing_blocks: 'bg-red-100 text-red-800',
    partially_exportable: 'bg-blue-100 text-blue-800',
    invalid_manifest: 'bg-red-100 text-red-800',
    unreadable: 'bg-neutral-300 text-neutral-800',
    incompatible_model: 'bg-orange-100 text-orange-800',
  };
  return (
    <span className={`badge ${map[grade] ?? 'bg-neutral-200 text-neutral-800'}`}>{grade}</span>
  );
}

export function ErrorBox({ error }: { error: unknown }) {
  const msg = error instanceof Error ? error.message : String(error);
  return (
    <div className="rounded border border-red-300 bg-red-50 p-3 text-sm text-red-900">
      <div className="font-semibold">Error</div>
      <div className="whitespace-pre-wrap font-mono text-xs">{msg}</div>
    </div>
  );
}

export function Loading({ children = 'Loading…' }: { children?: ReactNode }) {
  return <div className="p-4 text-sm text-neutral-500">{children}</div>;
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="rounded border border-dashed border-neutral-300 p-6 text-center text-sm text-neutral-500">
      {children}
    </div>
  );
}

export function Section({
  title,
  actions,
  children,
}: {
  title: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="mb-6">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-lg font-semibold">{title}</h2>
        {actions}
      </div>
      {children}
    </section>
  );
}

export function formatBytes(n: number): string {
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let v = n;
  let u = 0;
  while (v >= 1024 && u < units.length - 1) {
    v /= 1024;
    u += 1;
  }
  return `${v.toFixed(u === 0 ? 0 : 1)} ${units[u]}`;
}

export function formatTs(t: number | null | undefined): string {
  if (t == null) return '—';
  return new Date(t * 1000).toLocaleString();
}
