import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import * as api from '../lib/api';
import { qk } from '../lib/keys';

export function useBundles() {
  return useQuery({ queryKey: qk.bundles(), queryFn: api.listBundles });
}

export function useExportWorkspace() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.exportBundle,
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.bundles() }),
  });
}

export function useInspectBundle() {
  return useMutation({ mutationFn: (filename: string) => api.inspectBundle(filename) });
}

export function useImportBundle() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.importBundle,
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.workspaces() }),
  });
}

export function usePinBundle() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { filename: string; pinned: boolean }) =>
      api.pinBundle(args.filename, args.pinned),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.bundles() }),
  });
}
