import { useState, type ReactNode } from 'react';

export default function ConfirmModal({
  open,
  title,
  children,
  confirmText = 'Confirm',
  onCancel,
  onConfirm,
  destructive = false,
  requireTyping,
}: {
  open: boolean;
  title: string;
  children: ReactNode;
  confirmText?: string;
  onCancel: () => void;
  onConfirm: () => void;
  destructive?: boolean;
  /** If provided, operator must type this string exactly before confirm enables. */
  requireTyping?: string;
}) {
  const [typed, setTyped] = useState('');
  if (!open) return null;
  const armed = !requireTyping || typed === requireTyping;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-lg rounded-lg bg-white p-5 shadow-lg">
        <h3 className="mb-3 text-base font-semibold">{title}</h3>
        <div className="mb-4 text-sm text-neutral-700">{children}</div>
        {requireTyping && (
          <div className="mb-4">
            <label className="label mb-1">
              Type <span className="font-mono">{requireTyping}</span> to confirm
            </label>
            <input
              className="input font-mono"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              autoFocus
            />
          </div>
        )}
        <div className="flex justify-end gap-2">
          <button className="btn" onClick={onCancel}>
            Cancel
          </button>
          <button
            className={destructive ? 'btn-danger' : 'btn-primary'}
            disabled={!armed}
            onClick={() => {
              setTyped('');
              onConfirm();
            }}
          >
            {confirmText}
          </button>
        </div>
      </div>
    </div>
  );
}
