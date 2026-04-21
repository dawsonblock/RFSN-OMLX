import ConfirmModal from '../../components/ConfirmModal';

export default function ConflictPolicyDialog({
  open,
  policy,
  session_id,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  policy: 'fail' | 'rename' | 'overwrite';
  session_id: string;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  if (policy === 'overwrite') {
    return (
      <ConfirmModal
        open={open}
        title="Overwrite existing workspace"
        destructive
        confirmText="Overwrite"
        requireTyping={`overwrite ${session_id}`}
        onCancel={onCancel}
        onConfirm={onConfirm}
      >
        <p>
          This will permanently replace the existing workspace{' '}
          <span className="font-mono">{session_id}</span> with the bundle contents.
          Existing blocks will be overwritten.
        </p>
      </ConfirmModal>
    );
  }
  return (
    <ConfirmModal
      open={open}
      title={policy === 'fail' ? 'Import (fail on conflict)' : 'Import (rename on conflict)'}
      confirmText="Import"
      onCancel={onCancel}
      onConfirm={onConfirm}
    >
      {policy === 'fail' ? (
        <p>
          Conflict policy is <span className="font-mono">fail</span>: the import will
          abort with an error if a workspace named{' '}
          <span className="font-mono">{session_id}</span> already exists. This is the
          safest option.
        </p>
      ) : (
        <p>
          Conflict policy is <span className="font-mono">rename</span>: the bundle will
          be imported under a renamed session id if a conflict is detected. The
          existing workspace is left untouched.
        </p>
      )}
    </ConfirmModal>
  );
}
