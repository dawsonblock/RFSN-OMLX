import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import PruneReasonGroup from '../src/features/maintenance/PruneReasonGroup';
import type { PrunePlan } from '../src/types';

type Candidate = PrunePlan['candidates'][number];

const mk = (overrides: Partial<Candidate> = {}): Candidate => ({
  kind: 'workspace',
  reason: 'stale',
  action: 'eligible',
  model_name: 'm',
  session_id: 's',
  path: '/p',
  age_seconds: 86400,
  pinned: false,
  ...overrides,
});

describe('PruneReasonGroup', () => {
  it('auto-opens the <details> when at least one candidate is eligible', () => {
    const { container } = render(
      <PruneReasonGroup reason="stale" candidates={[mk({ action: 'eligible' })]} />,
    );
    const details = container.querySelector('details');
    expect(details).not.toBeNull();
    expect(details!.open).toBe(true);
  });

  it('starts closed when every candidate is protected', () => {
    const { container } = render(
      <PruneReasonGroup
        reason="stale"
        candidates={[mk({ action: 'protected' }), mk({ action: 'protected' })]}
      />,
    );
    expect(container.querySelector('details')!.open).toBe(false);
  });

  it('renders per-reason counts: total, eligible, protected', () => {
    render(
      <PruneReasonGroup
        reason="orphaned"
        candidates={[
          mk({ action: 'eligible' }),
          mk({ action: 'eligible' }),
          mk({ action: 'protected' }),
        ]}
      />,
    );
    expect(screen.getByText('orphaned')).toBeInTheDocument();
    expect(
      screen.getByText((txt) => txt.includes('3 total') && txt.includes('2 eligible') && txt.includes('1 protected')),
    ).toBeInTheDocument();
  });

  it('styles eligible rows distinctly from protected rows', () => {
    const { container } = render(
      <PruneReasonGroup
        reason="stale"
        candidates={[mk({ action: 'eligible' }), mk({ action: 'protected' })]}
      />,
    );
    const rows = container.querySelectorAll('tbody tr');
    expect(rows).toHaveLength(2);
    expect(rows[0].className).toContain('bg-red-50');
    expect(rows[1].className).not.toContain('bg-red-50');
  });
});
