import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import DangerConfirmDialog from '../src/features/maintenance/DangerConfirmDialog';

describe('DangerConfirmDialog', () => {
  it('does not render when closed', () => {
    render(
      <DangerConfirmDialog
        open={false}
        title="Execute prune"
        confirmText="Execute prune"
        requireTyping="abc123"
        onCancel={() => {}}
        onConfirm={() => {}}
      >
        body
      </DangerConfirmDialog>,
    );
    expect(screen.queryByText('Execute prune')).toBeNull();
  });

  it('disables confirm until exact required text is typed', async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    render(
      <DangerConfirmDialog
        open
        title="Execute prune"
        confirmText="Execute prune"
        requireTyping="abc123"
        onCancel={() => {}}
        onConfirm={onConfirm}
      >
        <span>body</span>
      </DangerConfirmDialog>,
    );
    const confirm = screen.getByRole('button', { name: 'Execute prune' });
    expect(confirm).toBeDisabled();

    await user.type(screen.getByRole('textbox'), 'wrong');
    expect(confirm).toBeDisabled();

    await user.clear(screen.getByRole('textbox'));
    await user.type(screen.getByRole('textbox'), 'abc123');
    expect(confirm).toBeEnabled();
    await user.click(confirm);
    expect(onConfirm).toHaveBeenCalledOnce();
  });

  it('cancel always works', async () => {
    const user = userEvent.setup();
    const onCancel = vi.fn();
    render(
      <DangerConfirmDialog
        open
        title="t"
        confirmText="ok"
        requireTyping="x"
        onCancel={onCancel}
        onConfirm={() => {}}
      >
        body
      </DangerConfirmDialog>,
    );
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onCancel).toHaveBeenCalledOnce();
  });
});
