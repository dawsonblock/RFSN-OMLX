import { Link } from 'react-router-dom';
import type { WorkspaceDetail } from '../../types';

export default function WorkspaceActionBar({
  detail,
  onPinToggle,
  pinPending,
}: {
  detail: WorkspaceDetail;
  onPinToggle: (next: boolean) => void;
  pinPending: boolean;
}) {
  const enc = (s: string) => encodeURIComponent(s);
  return (
    <div className="flex flex-wrap gap-2">
      <Link
        className="btn"
        to={`/w/${enc(detail.model_name)}/${enc(detail.session_id)}/lineage`}
      >
        Lineage
      </Link>
      <Link
        className="btn"
        to={`/w/${enc(detail.model_name)}/${enc(detail.session_id)}/diff`}
      >
        Diff
      </Link>
      <Link
        className="btn"
        to={`/w/${enc(detail.model_name)}/${enc(detail.session_id)}/fork`}
      >
        Fork
      </Link>
      {detail.pinned ? (
        <button
          className="btn-danger"
          onClick={() => onPinToggle(false)}
          disabled={pinPending}
        >
          Unpin
        </button>
      ) : (
        <button
          className="btn-primary"
          onClick={() => onPinToggle(true)}
          disabled={pinPending}
        >
          Pin
        </button>
      )}
    </div>
  );
}
