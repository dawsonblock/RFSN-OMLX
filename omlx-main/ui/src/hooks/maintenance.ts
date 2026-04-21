import { useMutation, useQuery } from '@tanstack/react-query';
import * as api from '../lib/api';
import { qk } from '../lib/keys';

export function useMaintenanceStats() {
  return useQuery({ queryKey: qk.maintenanceStats(), queryFn: api.maintenanceStats });
}

export function usePruneDryRun() {
  return useMutation({ mutationFn: api.pruneDryRun });
}

export function usePruneExecute() {
  return useMutation({ mutationFn: api.pruneExecute });
}
