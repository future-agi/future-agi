import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen } from '@testing-library/react';
import ProductionMoveCard from './ProductionMoveCard';
import { setSkillTracesOverride } from './skillTracesFlag';
import { MOVE, NOW, SKILL, failure, pending, renderRuntime, success } from './testFixtures';

const hooks = vi.hoisted(() => ({ comparison: vi.fn(), series: vi.fn() }));
vi.mock('src/api/skills/runtime', async (importOriginal) => ({
  ...(await importOriginal()),
  useSkillComparison: hooks.comparison,
  useSkillRuntimeSeries: hooks.series,
}));
vi.mock('src/components/iconify', () => ({ default: () => null }));
vi.mock('notistack', () => ({ enqueueSnackbar: vi.fn() }));

const show = (data, props = {}) => {
  hooks.comparison.mockReturnValue(data && data.isPending !== undefined ? data : success(data));
  return renderRuntime(<ProductionMoveCard skillId={SKILL.id} eventId="event-1" {...props} />);
};
const sectionOrder = () => [...document.querySelectorAll('[data-section]')].map((node) => node.dataset.section);

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(NOW);
  setSkillTracesOverride(true);
  hooks.series.mockReturnValue(success({ series: [] }));
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  setSkillTracesOverride(null);
});

describe('ProductionMoveCard', () => {
  it('asks for the event and window named in the URL', () => {
    show(MOVE, { window: '6h' });
    expect(hooks.comparison).toHaveBeenCalledWith(SKILL.id, { eventId: 'event-1', window: '6h' });
    expect(screen.getByText('Production moved from v3 to v4 · 24 hours each side')).toBeInTheDocument();
  });

  it('while collecting, shows counts and no change or arrows', () => {
    show({ ...MOVE, verdict: 'watching', soFar: false, primaryAction: null, collecting: { hoursElapsed: 6, hoursTotal: 24, tracesSoFar: 212 } });
    expect(screen.getByText('Collecting: 6 of 24 hours, 212 traces so far')).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/[↑↓]/);
    expect(screen.queryByRole('link', { name: /Review/ })).not.toBeInTheDocument();
  });

  it('once the sample rule is met on an open window, marks changes as so far', () => {
    show(MOVE);
    expect(screen.getAllByText('so far · 6 of 24 h').length).toBeGreaterThan(0);
    expect(screen.getAllByText(/↑ 1\.3 pts · so far · 6 of 24 h/).length).toBeGreaterThan(0);
    expect(screen.getByText('Worse so far after the move')).toBeInTheDocument();
  });

  it('after the window closes, shows the final verdict without "so far"', () => {
    show({ ...MOVE, complete: true, soFar: false, verdict: 'worse' });
    expect(screen.getByText('Worse after the move')).toBeInTheDocument();
    expect(screen.queryByText(/so far/)).not.toBeInTheDocument();
  });

  it('orders same agents, then the control, then all agents, with the triggering metric first', () => {
    show(MOVE);
    expect(sectionOrder().slice(0, 4)).toEqual(['same-agents', 'control', 'all-agents', 'by-agent']);
    const same = document.querySelector('[data-section="same-agents"]');
    expect([...same.querySelectorAll('[data-metric]')].map((row) => row.dataset.metric)).toEqual(['failed_requests', 'latency_p95']);
    expect(screen.getAllByText('v3 in the same period, 2 agents').length).toBeGreaterThan(0);
    expect(screen.getAllByText('All agents').length).toBeGreaterThan(0);
  });

  it('says why each agent has or has not moved, in the six wordings', () => {
    show(MOVE);
    [
      'Got v4',
      'Still on v3: the app asks for v3',
      'Still on v3: pinned in support-agent',
      'Still on v3: waiting for a support-agent release',
      'Got v3 after the move, from a cached copy',
      'Not seen after this move',
    ].forEach((text) => expect(screen.getByText(text)).toBeInTheDocument());
    expect(screen.getAllByRole('link', { name: 'Open context' })).toHaveLength(2);
  });

  it('offers one primary action, Review failed requests, only when worse', () => {
    const onRollback = vi.fn();
    const view = show(MOVE, { onRollback });
    const review = screen.getByRole('link', { name: 'Review 31 failed requests' });
    expect(review.getAttribute('href')).toMatch(/^\/dashboard\/observe\/p1\/llm-tracing\?/);
    fireEvent.click(screen.getByRole('button', { name: 'Roll back to v3' }));
    expect(onRollback).toHaveBeenCalledWith(3);
    view.unmount();
    show({ ...MOVE, complete: true, soFar: false, verdict: 'no_change', primaryAction: null });
    expect(screen.queryByRole('link', { name: /Review/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Roll back/ })).not.toBeInTheDocument();
  });

  it('notes a before-window cut by an earlier move', () => {
    show({ ...MOVE, beforeCutAt: '2026-11-14T10:00:00Z' });
    expect(screen.getByText(/The before period starts at the earlier move to v3 on/)).toBeInTheDocument();
  });

  it('shows a skeleton while loading and an inline error with Retry', () => {
    const view = show(pending());
    expect(screen.getByRole('progressbar', { name: 'Loading this move' })).toBeInTheDocument();
    view.unmount();
    show(failure('runtime_read_unavailable'));
    expect(screen.getByRole('alert')).toHaveTextContent("Couldn't load this move.");
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
  });
});
