import type { WorkspaceDetail } from '../../types';
import StatusPill from './StatusPill';
import { formatTs } from '../../components/ui';

function Field({
  label,
  children,
  mono,
  full,
}: {
  label: string;
  children: React.ReactNode;
  mono?: boolean;
  full?: boolean;
}) {
  return (
    <div className={full ? 'md:col-span-4' : ''}>
      <div className="label">{label}</div>
      <div className={`text-sm ${mono ? 'font-mono' : ''} break-words`}>{children}</div>
    </div>
  );
}

export default function WorkspaceHeader({ detail }: { detail: WorkspaceDetail }) {
  return (
    <div className="card grid grid-cols-1 gap-3 md:grid-cols-4">
      <Field label="Model" mono>{detail.model_name}</Field>
      <Field label="Session" mono>{detail.session_id}</Field>
      <Field label="Head turn" mono>{detail.lineage.head_turn_id || '—'}</Field>
      <Field label="Turns">{detail.lineage.turn_count}</Field>
      <Field label="Integrity"><StatusPill grade={detail.integrity_grade} /></Field>
      <Field label="Pinned">{detail.pinned ? 'yes' : 'no'}</Field>
      <Field label="Exportable">{detail.exportable ? 'yes' : 'no'}</Field>
      <Field label="Updated">{formatTs(detail.lineage.updated_at)}</Field>
      <Field label="Parent">
        {detail.lineage.parent
          ? `${detail.lineage.parent[0]} @ ${detail.lineage.parent[1].slice(0, 12)}`
          : '—'}
      </Field>
      <Field label="Branch reason" full>
        {detail.branch_reason ?? '—'}
      </Field>
      <Field label="Description" full>
        {detail.lineage.description ?? '—'}
      </Field>
    </div>
  );
}
