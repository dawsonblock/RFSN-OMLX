import { useMemo, useState } from 'react';
import {
  useCatalog,
  useInstalledModels,
  useDownloadTasks,
  useStartDownload,
  useCancelDownload,
  useRetryDownload,
  useRemoveDownloadTask,
} from '../hooks';
import { ErrorBox, Empty, Section, formatBytes } from '../components/ui';
import type { CatalogModel, DownloadTask } from '../lib/schemas';

function statusBadge(status: DownloadTask['status']) {
  const map: Record<DownloadTask['status'], string> = {
    pending: 'bg-neutral-200 text-neutral-800',
    downloading: 'bg-blue-100 text-blue-800',
    completed: 'bg-green-100 text-green-800',
    failed: 'bg-red-100 text-red-800',
    cancelled: 'bg-yellow-100 text-yellow-800',
  };
  return <span className={`badge ${map[status]}`}>{status}</span>;
}

function ProgressBar({ value }: { value: number }) {
  const pct = Math.max(0, Math.min(100, Math.round(value * 100)));
  return (
    <div className="h-2 w-full overflow-hidden rounded bg-neutral-200">
      <div className="h-full bg-blue-600 transition-all" style={{ width: `${pct}%` }} />
    </div>
  );
}

function CatalogCard({
  model,
  disabled,
  busy,
  onDownload,
}: {
  model: CatalogModel;
  disabled: boolean;
  busy: boolean;
  onDownload: () => void;
}) {
  return (
    <div className="card flex flex-col gap-2">
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="text-sm font-semibold">{model.display_name}</div>
          <div className="text-xs text-neutral-500">{model.family} · {model.size_label} · {model.params}</div>
        </div>
        <span className="badge bg-neutral-100 text-neutral-700">{model.quantization}</span>
      </div>
      <p className="text-xs text-neutral-600">{model.description}</p>
      <div className="flex flex-wrap gap-1">
        {model.tags.map((t) => (
          <span key={t} className="badge bg-neutral-100 text-neutral-700">{t}</span>
        ))}
      </div>
      <div className="font-mono text-[11px] text-neutral-500">{model.repo_id}</div>
      <button
        type="button"
        className="btn btn-primary mt-1 text-xs disabled:opacity-50"
        disabled={disabled || busy}
        onClick={onDownload}
      >
        {disabled ? 'Already installed' : busy ? 'Starting…' : 'Download'}
      </button>
    </div>
  );
}

export default function ModelsPage() {
  const catalog = useCatalog();
  const installed = useInstalledModels();
  const tasks = useDownloadTasks();
  const startDl = useStartDownload();
  const cancelDl = useCancelDownload();
  const retryDl = useRetryDownload();
  const removeTask = useRemoveDownloadTask();

  const [customRepo, setCustomRepo] = useState('');
  const [hfToken, setHfToken] = useState('');

  const installedRepoIds = useMemo(() => {
    const s = new Set<string>();
    for (const m of installed.data?.models ?? []) {
      if (m.id) s.add(m.id);
    }
    return s;
  }, [installed.data]);

  const activeRepoIds = useMemo(() => {
    const s = new Set<string>();
    for (const t of tasks.data?.tasks ?? []) {
      if (t.status === 'pending' || t.status === 'downloading') s.add(t.repo_id);
    }
    return s;
  }, [tasks.data]);

  const onDownloadRepo = (repo_id: string) => {
    startDl.mutate({ repo_id, hf_token: hfToken || undefined });
  };

  const activeTasks = tasks.data?.tasks ?? [];

  return (
    <>
      <Section title="HuggingFace access (optional)">
        <div className="card">
          <label className="label">HF token (leave empty for public models)</label>
          <input
            type="password"
            className="input mt-1 w-full font-mono text-xs"
            placeholder="hf_…"
            value={hfToken}
            onChange={(e) => setHfToken(e.target.value)}
          />
          <p className="mt-2 text-xs text-neutral-500">
            Stored only in this browser tab. Required only for gated repos.
          </p>
        </div>
      </Section>

      <Section title="Installed models">
        {installed.isPending && <div>Loading…</div>}
        {installed.error && <ErrorBox error={installed.error} />}
        {installed.data && installed.data.models.length === 0 && (
          <Empty>No models on disk yet. Pick one from the catalog below.</Empty>
        )}
        {installed.data && installed.data.models.length > 0 && (
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
            {installed.data.models.map((m) => (
              <div key={`${m.id ?? m.model_path}`} className="card">
                <div className="flex items-center justify-between">
                  <div className="text-sm font-semibold">{m.id ?? '(unknown)'}</div>
                  <span
                    className={`badge ${m.loaded ? 'bg-green-100 text-green-800' : 'bg-neutral-200 text-neutral-800'}`}
                  >
                    {m.loaded ? 'loaded' : m.is_loading ? 'loading' : 'on disk'}
                  </span>
                </div>
                <div className="mt-1 font-mono text-[11px] text-neutral-500">{m.model_path}</div>
                <div className="mt-2 text-xs text-neutral-600">
                  {m.model_type} · {formatBytes(m.estimated_size)}
                  {m.pinned ? ' · pinned' : ''}
                </div>
              </div>
            ))}
          </div>
        )}
      </Section>

      <Section title="Catalog">
        {catalog.isPending && <div>Loading…</div>}
        {catalog.error && <ErrorBox error={catalog.error} />}
        {catalog.data && (
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
            {catalog.data.models.map((m) => (
              <CatalogCard
                key={m.id}
                model={m}
                disabled={installedRepoIds.has(m.repo_id)}
                busy={activeRepoIds.has(m.repo_id)}
                onDownload={() => onDownloadRepo(m.repo_id)}
              />
            ))}
          </div>
        )}
        {startDl.error && (
          <div className="mt-3">
            <ErrorBox error={startDl.error} />
          </div>
        )}
      </Section>

      <Section title="Custom repo_id">
        <div className="card flex flex-col gap-2 md:flex-row md:items-end">
          <div className="flex-1">
            <label className="label">HuggingFace repo (org/name)</label>
            <input
              type="text"
              className="input mt-1 w-full font-mono text-xs"
              placeholder="mlx-community/YourModel-4bit"
              value={customRepo}
              onChange={(e) => setCustomRepo(e.target.value)}
            />
          </div>
          <button
            type="button"
            className="btn btn-primary text-xs disabled:opacity-50"
            disabled={!customRepo.includes('/') || startDl.isPending}
            onClick={() => {
              if (customRepo.includes('/')) {
                onDownloadRepo(customRepo.trim());
                setCustomRepo('');
              }
            }}
          >
            Download
          </button>
        </div>
      </Section>

      <Section title="Active downloads">
        {tasks.isPending && <div>Loading…</div>}
        {tasks.error && <ErrorBox error={tasks.error} />}
        {tasks.data && activeTasks.length === 0 && <Empty>No downloads.</Empty>}
        {activeTasks.length > 0 && (
          <div className="space-y-2">
            {activeTasks.map((t) => (
              <div key={t.task_id} className="card">
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <div className="text-sm font-semibold">{t.repo_id}</div>
                    <div className="font-mono text-[11px] text-neutral-500">{t.task_id}</div>
                  </div>
                  {statusBadge(t.status)}
                </div>
                <div className="mt-2">
                  <ProgressBar value={t.progress} />
                  <div className="mt-1 text-xs text-neutral-600">
                    {formatBytes(t.downloaded_size)} / {formatBytes(t.total_size)} (
                    {Math.round((t.progress ?? 0) * 100)}%)
                    {t.retry_count > 0 ? ` · retries: ${t.retry_count}` : ''}
                  </div>
                </div>
                {t.error && (
                  <div className="mt-2 whitespace-pre-wrap rounded border border-red-300 bg-red-50 p-2 font-mono text-xs text-red-900">
                    {t.error}
                  </div>
                )}
                <div className="mt-2 flex gap-2">
                  {(t.status === 'pending' || t.status === 'downloading') && (
                    <button
                      type="button"
                      className="btn text-xs"
                      onClick={() => cancelDl.mutate(t.task_id)}
                      disabled={cancelDl.isPending}
                    >
                      Cancel
                    </button>
                  )}
                  {(t.status === 'failed' || t.status === 'cancelled') && (
                    <button
                      type="button"
                      className="btn text-xs"
                      onClick={() => retryDl.mutate({ task_id: t.task_id, hf_token: hfToken || '' })}
                      disabled={retryDl.isPending}
                    >
                      Retry
                    </button>
                  )}
                  {(t.status === 'completed' || t.status === 'failed' || t.status === 'cancelled') && (
                    <button
                      type="button"
                      className="btn text-xs"
                      onClick={() => removeTask.mutate(t.task_id)}
                      disabled={removeTask.isPending}
                    >
                      Remove
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </Section>
    </>
  );
}
