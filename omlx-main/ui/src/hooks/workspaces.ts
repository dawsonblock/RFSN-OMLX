import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import * as api from '../lib/api';
import { qk } from '../lib/keys';

export function useWorkspaces(params: URLSearchParams) {
  const key = params.toString();
  return useQuery({
    queryKey: qk.workspaces(key),
    queryFn: () => api.listWorkspaces(params),
  });
}

export function useWorkspace(
  model: string | undefined,
  session: string | undefined,
  opts: { validate?: boolean; include_raw?: boolean } = {},
) {
  return useQuery({
    enabled: Boolean(model && session),
    queryKey: [...qk.workspace(model ?? '', session ?? ''), opts] as const,
    queryFn: () => api.getWorkspace(model!, session!, opts),
  });
}

export function useCreateWorkspace() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.createWorkspace,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.workspaces() });
    },
  });
}

export function useUpdateMetadata(model: string, session: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: {
      label?: string | null;
      description?: string | null;
      task_tag?: string | null;
    }) => api.updateMetadata(model, session, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.workspace(model, session) });
      qc.invalidateQueries({ queryKey: qk.workspaces() });
    },
  });
}

export function useForkWorkspace(model: string, session: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: Parameters<typeof api.forkWorkspace>[2]) =>
      api.forkWorkspace(model, session, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.workspaces() });
      qc.invalidateQueries({ queryKey: qk.workspace(model, session) });
    },
  });
}

export function usePinWorkspace(model: string, session: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (pinned: boolean) => api.setPinned(model, session, pinned),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.workspace(model, session) });
      qc.invalidateQueries({ queryKey: qk.workspaces() });
    },
  });
}

export function useValidateWorkspace(model: string, session: string) {
  return useMutation({ mutationFn: () => api.validateWorkspace(model, session) });
}
