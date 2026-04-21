import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ConflictPolicyDialog from '../src/features/transfers/ConflictPolicyDialog';

describe('ConflictPolicyDialog', () => {
  it('fail policy: confirm enabled immediately, no typing required', async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    render(
      <ConflictPolicyDialog
        open
        policy="fail"
        session_id="s1"
        onCancel={() => {}}
        onConfirm={onConfirm}
      />,
    );
    const confirm = screen.getByRole('button', { name: 'Import' });
    expect(confirm).toBeEnabled();
    expect(screen.queryByRole('textbox')).toBeNull();
    await user.click(confirm);
    expect(onConfirm).toHaveBeenCalledOnce();
  });

  it('rename policy: confirm enabled immediately', () => {
    render(
      <ConflictPolicyDialog
        open
        policy="rename"
        session_id="s1"
        onCancel={() => {}}
        onConfirm={() => {}}
      />,
    );
    expect(screen.getByRole('button', { name: 'Import' })).toBeEnabled();
    expect(screen.queryByRole('textbox')).toBeNull();
  });

  it('overwrite policy: confirm gated by typing exact "overwrite {session_id}"', async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    render(
      <ConflictPolicyDialog
        open
        policy="overwrite"
        session_id="abc"
        onCancel={() => {}}
        onConfirm={onConfirm}
      />,
    );
    const confirm = screen.getByRole('button', { name: 'Overwrite' });
    expect(confirm).toBeDisabled();

    const input = screen.getByRole('textbox');
    await user.type(input, 'overwrite wrong');
    expect(confirm).toBeDisabled();
    expect(onConfirm).not.toHaveBeenCalled();

    await user.clear(input);
    await user.type(input, 'overwrite abc');
    expect(confirm).toBeEnabled();
    await user.click(confirm);
    expect(onConfirm).toHaveBeenCalledOnce();
  });

  it('does not render when closed', () => {
    render(
      <ConflictPolicyDialog
        open={false}
        policy="overwrite"
        session_id="s1"
        onCancel={() => {}}
        onConfirm={() => {}}
      />,
    );
    expect(screen.queryByRole('button', { name: 'Overwrite' })).toBeNull();
  });
});
