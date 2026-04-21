// Semantic wrapper around ConfirmModal that always renders the destructive style
// and requires typed confirmation. Spec-named.
import ConfirmModal from '../../components/ConfirmModal';
import type { ReactNode } from 'react';

export default function DangerConfirmDialog({
  open,
  title,
  children,
  confirmText,
  requireTyping,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  title: string;
  children: ReactNode;
  confirmText: string;
  requireTyping: string;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <ConfirmModal
      open={open}
      title={title}
      destructive
      confirmText={confirmText}
      requireTyping={requireTyping}
      onCancel={onCancel}
      onConfirm={onConfirm}
    >
      {children}
    </ConfirmModal>
  );
}
