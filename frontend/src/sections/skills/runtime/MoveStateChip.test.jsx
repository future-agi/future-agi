import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, screen } from '@testing-library/react';
import HeaderMoveChip, { MoveStateChip, moveTiming } from './MoveStateChip';
import { MOVE, NOW, SKILL, failure, pending, renderRuntime, success } from './__tests__/fixtures';

const hooks = vi.hoisted(() => ({ comparison: vi.fn() }));
vi.mock('src/api/skills/runtime', async (importOriginal) => ({ ...(await importOriginal()), useSkillComparison: hooks.comparison }));

const chip = (move) => renderRuntime(<MoveStateChip move={move} skillId={SKILL.id} now={NOW} />);
const HOUR = 3600 * 1000;

afterEach(cleanup);

describe('MoveStateChip', () => {
  it('reads "Watching v4 · 18 h left" while the window is open and the rule has not fired', () => {
    chip({ ...MOVE, verdict: 'watching', trigger: null });
    expect(screen.getByText('Watching v4 · 18 h left')).toBeInTheDocument();
  });

  it('names the triggering metric and agent count so far, and links to the move card', () => {
    chip(MOVE);
    const link = screen.getByRole('link', { name: 'v4: failed requests up so far on 5 agents · 6 of 24 h' });
    expect(link).toHaveAttribute('href', '/dashboard/skills/skill-a?tab=runtime&move=event-1&window=24h');
  });

  it('says latency goes up and an eval pass rate goes down', () => {
    const view = chip({ ...MOVE, verdict: 'worse', complete: true, trigger: { metric: 'latency_p95', agents: 2 } });
    expect(screen.getByText('v4: p95 latency up on 2 agents')).toBeInTheDocument();
    view.unmount();
    chip({ ...MOVE, verdict: 'worse', complete: true, trigger: { metric: 'eval:eval-1', name: 'Groundedness', agents: 1 } });
    expect(screen.getByText('v4: Groundedness pass rate down on 1 agent')).toBeInTheDocument();
  });

  it('keeps "worse" for 7 days after the window closes, then hides it', () => {
    const closed = { ...MOVE, verdict: 'worse', complete: true, endsAt: new Date(NOW - 6 * 24 * HOUR).toISOString(), movedAt: new Date(NOW - 7 * 24 * HOUR).toISOString() };
    const view = chip(closed);
    expect(screen.getByText(/failed requests up on 5 agents/)).toBeInTheDocument();
    view.unmount();
    chip({ ...closed, endsAt: new Date(NOW - 8 * 24 * HOUR).toISOString(), movedAt: new Date(NOW - 9 * 24 * HOUR).toISOString() });
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
  });

  it.each(['better', 'no_change', 'not_enough_data'])('shows nothing for %s', (verdict) => {
    chip({ ...MOVE, verdict, complete: true });
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
  });

  it('computes timing from the move when the server sends no collecting figures', () => {
    expect(moveTiming(MOVE, NOW)).toMatchObject({ hoursElapsed: 6, hoursTotal: 24, hoursRemaining: 18 });
  });

  it('is hidden in the header while loading, on error and when no move exists', () => {
    hooks.comparison.mockReturnValue(pending());
    const a = renderRuntime(<HeaderMoveChip skillId={SKILL.id} />);
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
    a.unmount();
    hooks.comparison.mockReturnValue(failure('project_not_readable', 403));
    const b = renderRuntime(<HeaderMoveChip skillId={SKILL.id} />);
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
    b.unmount();
    hooks.comparison.mockReturnValue(success(null));
    renderRuntime(<HeaderMoveChip skillId={SKILL.id} />);
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
    expect(hooks.comparison).toHaveBeenCalledWith(SKILL.id, { label: 'production' });
  });
});
