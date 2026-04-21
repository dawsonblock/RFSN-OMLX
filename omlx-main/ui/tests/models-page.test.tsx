import React from 'react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import ModelsPage from '../src/pages/ModelsPage';

const catalogBody = {
  models: [
    {
      id: 'qwen',
      repo_id: 'mlx-community/Qwen2.5-0.5B-Instruct-4bit',
      display_name: 'Qwen2.5 0.5B Instruct (4-bit)',
      family: 'Qwen',
      size_label: '~400 MB',
      params: '0.5B',
      quantization: '4-bit',
      description: 'Tiny test model',
      tags: ['chat', 'tiny'],
    },
  ],
};
const installedEmpty = { models: [] };
const tasksEmpty = { tasks: [] };
const downloadResp = {
  task: {
    task_id: 't1',
    repo_id: 'mlx-community/Qwen2.5-0.5B-Instruct-4bit',
    status: 'pending',
    progress: 0,
    total_size: 0,
    downloaded_size: 0,
    error: null,
    created_at: 0,
    started_at: null,
    completed_at: null,
    retry_count: 0,
  },
};

function jsonResp(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

function routeFor(path: string) {
  if (path.endsWith('/models/catalog')) return jsonResp(catalogBody);
  if (path.endsWith('/models/installed')) return jsonResp(installedEmpty);
  if (path.endsWith('/models/tasks')) return jsonResp(tasksEmpty);
  if (path.endsWith('/models/download')) return jsonResp(downloadResp, 201);
  return jsonResp({ detail: 'unhandled' }, 500);
}

function wrap(children: React.ReactNode) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: 0 }, mutations: { retry: 0 } },
  });
  return (
    <QueryClientProvider client={qc}>
      <MemoryRouter>{children}</MemoryRouter>
    </QueryClientProvider>
  );
}

describe('ModelsPage', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('renders catalog entries from the API', async () => {
    global.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString();
      return routeFor(url);
    }) as typeof fetch;

    render(wrap(<ModelsPage />));

    await waitFor(() =>
      expect(screen.getByText('Qwen2.5 0.5B Instruct (4-bit)')).toBeTruthy(),
    );
    expect(screen.getByText('mlx-community/Qwen2.5-0.5B-Instruct-4bit')).toBeTruthy();
  });

  it('POSTs /models/download when the download button is clicked', async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    global.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString();
      calls.push({ url, init });
      return routeFor(url);
    }) as typeof fetch;

    render(wrap(<ModelsPage />));

    await waitFor(() =>
      expect(screen.getByText('Qwen2.5 0.5B Instruct (4-bit)')).toBeTruthy(),
    );
    const buttons = await screen.findAllByRole('button', { name: /^download$/i });
    const enabled = buttons.find((b) => !(b as HTMLButtonElement).disabled);
    expect(enabled).toBeTruthy();
    fireEvent.click(enabled!);

    await waitFor(() => {
      const dl = calls.find((c) => c.url.endsWith('/models/download'));
      expect(dl).toBeTruthy();
      expect(dl!.init?.method).toBe('POST');
      expect(String(dl!.init?.body ?? '')).toContain('mlx-community/Qwen2.5-0.5B-Instruct-4bit');
    });
  });
});
