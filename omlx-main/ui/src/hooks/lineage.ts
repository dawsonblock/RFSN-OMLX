import { useQuery } from '@tanstack/react-query';
import * as api from '../lib/api';
import { qk } from '../lib/keys';

export function useLineage(model: string | undefined, session: string | undefined) {
  return useQuery({
    enabled: Boolean(model && session),
    queryKey: qk.lineage(model ?? '', session ?? ''),
    queryFn: () => api.getLineage(model!, session!),
  });
}
