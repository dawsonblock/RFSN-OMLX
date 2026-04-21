import { useMutation, useQuery } from '@tanstack/react-query';
import * as api from '../lib/api';
import { qk } from '../lib/keys';

export function useEnvironmentInfo() {
  return useQuery({ queryKey: qk.env(), queryFn: api.envInfo });
}

export function useHealthCheck() {
  return useMutation({ mutationFn: api.health });
}
