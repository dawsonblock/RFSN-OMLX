import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import * as api from '../lib/api';
import { qk } from '../lib/keys';

export function useCatalog() {
  return useQuery({
    queryKey: qk.modelsCatalog(),
    queryFn: api.listCatalog,
    staleTime: 5 * 60 * 1000,
  });
}

export function useInstalledModels() {
  return useQuery({
    queryKey: qk.modelsInstalled(),
    queryFn: api.listInstalled,
    refetchInterval: 10_000,
  });
}

export function useDownloadTasks() {
  return useQuery({
    queryKey: qk.modelsTasks(),
    queryFn: api.listDownloadTasks,
    refetchInterval: (query) => {
      const data = query.state.data;
      if (!data) return 3000;
      const active = data.tasks.some(
        (t) => t.status === 'pending' || t.status === 'downloading',
      );
      return active ? 1500 : 5000;
    },
  });
}

export function useStartDownload() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.startDownload,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.modelsTasks() });
    },
  });
}

export function useCancelDownload() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.cancelDownload,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.modelsTasks() });
    },
  });
}

export function useRetryDownload() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { task_id: string; hf_token?: string }) =>
      api.retryDownload(args.task_id, args.hf_token ?? ''),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.modelsTasks() });
    },
  });
}

export function useRemoveDownloadTask() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.removeDownloadTask,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.modelsTasks() });
      qc.invalidateQueries({ queryKey: qk.modelsInstalled() });
    },
  });
}
