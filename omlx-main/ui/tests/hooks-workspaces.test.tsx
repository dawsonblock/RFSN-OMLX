import React from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useWorkspaces, useCreateWorkspace } from '../src/hooks/workspaces';
import { ApiError } from '../src/lib/fetcher';

// Minimal valid payloads matching the Zod schemas.
const summary = {
  model_name: 'm',
  session_id: 's',
  label: null,
  head_turn_id: 't0',
  turn_count: 1,
  updated_at: 0,
  last_used_at: null,
  pinned: false,
  integrity_grade: 'A',
  branch_count: 0,
  has_parent: false,
  exportable: true,
  model_compat: { model_name: 'm', block_size: null, schema: '2' },
  task_tag: null,
};

const detail = {
  model_name: 'm',
  session_id: 's',
  lineage: {
    session_id: 's',
    label: null,
    description: null,
    created_at: 0,
    updated_at: 0,
    head_turn_id: 't0',
    parent: null,
    model_compat: { model_name: 'm', block_size: null, schema: '2' },
    turn_count: 1,
    task_tag: null,
  },
  turns: [{ turn_id: 't0', committed_at: 0, block_count: 1, note: null, branch_reason: null }],
  pinned: false,
  last_used_at: null,
  integrity_grade: 'A',
  exportable: true,
  replay: null,
  branch_reason: null,
  children_count: 0,
  raw: null,
};

function wrapper() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: 0 }, mutations: { retry: 0 } },
  });
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
}

function mockFetchOk(body: unknown) {
  return vi.fn(async () =>
    new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } }),
  );
}

function mockFetchErr(status: number, detailMsg: string) {
  return vi.fn(async () =>
    new Response(JSON.stringify({ detail: detailMsg }), {
      status,
      headers: { 'content-type': 'application/json' },
    }),
  );
}

describe('useWorkspaces + useCreateWorkspace', () => {
  const originalFetch = global.fetch;
  beforeEach(() => {
    vi.restoreAllMocks();
  });
  afterEach(() => {
    global.fetch = originalFetch;
  });

  it('useWorkspaces returns parsed summaries on 200', async () => {
    global.fetch = mockFetchOk([summary]) as unknown as typeof fetch;
    const { result } = renderHook(() => useWorkspaces(new URLSearchParams()), {
      wrapper: wrapper(),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual([summary]);
  });

  it('useWorkspaces surfaces ApiError on 500', async () => {
    global.fetch = mockFetchErr(500, 'boom') as unknown as typeof fetch;
    const { result } = renderHook(() => useWorkspaces(new URLSearchParams()), {
      wrapper: wrapper(),
    });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(ApiError);
    expect((result.current.error as ApiError).status).toBe(500);
  });

  it('useCreateWorkspace posts and returns parsed detail', async () => {
    const fetchSpy = mockFetchOk(detail);
    global.fetch = fetchSpy as unknown as typeof fetch;
    const { result } = renderHook(() => useCreateWorkspace(), { wrapper: wrapper() });
    result.current.mutate({ model_name: 'm', session_id: 's' });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(detail);
    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/ui/api/workspaces');
    expect(init.method).toBe('POST');
  });
});
