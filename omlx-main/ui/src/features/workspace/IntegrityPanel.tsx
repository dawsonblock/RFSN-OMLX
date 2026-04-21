import type { WorkspaceDetail } from '../../types';
import StatusPill from './StatusPill';

export default function IntegrityPanel({ detail }: { detail: WorkspaceDetail }) {
  return (
    <div className="card">
      <div className="label mb-1">Integrity</div>
      <div className="flex items-center gap-3">
        <StatusPill grade={detail.integrity_grade} />
        <span className="text-sm text-neutral-600">
          exportable: {detail.exportable ? 'yes' : 'no'}
        </span>
        <span className="text-sm text-neutral-600">
          pinned: {detail.pinned ? 'yes' : 'no'}
        </span>
      </div>
    </div>
  );
}
