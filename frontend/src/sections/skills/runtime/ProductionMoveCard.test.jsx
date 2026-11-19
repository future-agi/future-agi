import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen, within } from '@testing-library/react';
import ProductionMoveCard, { MoveSeries } from './ProductionMoveCard';
import { setSkillTracesOverride } from './skillTracesFlag';
import { MOVE, NOW, SKILL, V4, links, failure, pending, renderRuntime, success } from './__tests__/fixtures';

const hooks = vi.hoisted(() => ({ comparison: vi.fn(), series: vi.fn() }));
vi.mock('src/api/skills/runtime', async (importOriginal) => ({
  ...(await importOriginal()),
  useSkillComparison: hooks.comparison,
  useSkillRuntimeSeries: hooks.series,
}));
vi.mock('src/components/iconify', () => ({ default: () => null }));
vi.mock('notistack', async (load) => ({ ...(await load()), enqueueSnackbar: vi.fn() }));

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
    expect(hooks.comparison).toHaveBeenCalledWith(SKILL.id, { eventId: 'event-1', window: '6h', projectIds: [], cursor: undefined });
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
      'Loaded multiple versions, including v4',
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

  it('does not invent zero before-and-after measurements for a first release', () => {
    show({ ...MOVE, fromVersion: null, sameAgents: { ...MOVE.sameAgents, agents: 0 } });
    expect(screen.getByText(/There is no same-agent comparison yet/)).toBeVisible();
    expect(screen.queryByRole('table', { name: 'Same agents before and after' })).toBeNull();
  });

  it('shows only measured after-window values for the old-version control', () => {
    show(MOVE);
    const control = within(screen.getByRole('table', { name: 'v3 in the same period, 2 agents' }));
    expect(control.getByRole('columnheader', { name: 'After' })).toBeVisible();
    expect(control.queryByRole('columnheader', { name: 'Before' })).toBeNull();
    expect(control.queryByRole('columnheader', { name: 'Change' })).toBeNull();
  });

  it.each([['latency_p95', 'Review slowest requests'], ['eval:eval-1', 'Review failed evaluations']])('opens the after-window evidence for %s, even with no failed requests', (metric, name) => {
    const cohort = links('p2', V4);
    const target = metric === 'latency_p95' ? cohort.slowest : cohort.eval_failed['eval-1'];
    show({ ...MOVE, trigger: { metric }, allAgents: { metrics: [{ metric: 'failed_requests', afterCount: 0 }] }, sameAgents: { ...MOVE.sameAgents, links: { after: { [metric]: target } } } });
    expect(screen.queryByRole('link', { name: 'Review 0 failed requests' })).toBeNull();
    const url = new URL(screen.getByRole('link', { name }).getAttribute('href'), 'http://localhost');
    expect(url.pathname).toContain('/observe/p2/');
    if (metric === 'latency_p95') expect(JSON.parse(url.searchParams.get('observeLinkSort'))).toEqual({ column_id: 'latency', direction: 'desc' });
    else expect(JSON.parse(url.searchParams.get('observeLinkFilter'))).toContainEqual(expect.objectContaining({ column_id: 'eval.eval-1' }));
  });

  it('offers the exact project-specific trace sets for a multi-project latency regression', () => {
    const projects = ['one', 'two'].map((projectId, index) => ({ projectId, projectName: `Workspace ${projectId}`, traces: 40 + index,
      link: { ...links(projectId, V4).slowest, filters: [{ column_id: 'skill.trace_cohort', filter_config: { col_type: 'SKILL', filter_type: 'text', filter_op: 'equals', filter_value: String(index + 1).repeat(64) } }] } }));
    show({ ...MOVE, trigger: { metric: 'latency_p95' }, sameAgents: { ...MOVE.sameAgents, links: { after: { latency_p95: { project_id: null, projects } } } } });
    fireEvent.click(screen.getByRole('button', { name: 'Review slowest requests' }));
    expect(screen.getByRole('menuitem', { name: 'Workspace one · 40 traces' })).toBeVisible();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Workspace two · 41 traces' }));
    const target = new URL(screen.getByTestId('location').textContent, 'http://localhost');
    expect(target.pathname).toContain('/observe/two/');
    expect(JSON.parse(target.searchParams.get('observeLinkFilter'))).toEqual(projects[1].link.filters);
    expect(JSON.parse(target.searchParams.get('observeLinkSort'))).toEqual({ column_id: 'latency', direction: 'desc' });
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

it('pages comparison agents while keeping the event and window', () => {
  show({ ...MOVE, nextCursor: 'comparison-two' });
  fireEvent.click(screen.getByRole('button', { name: 'Next page' }));
  expect(hooks.comparison.mock.calls.at(-1)[1]).toMatchObject({ eventId: 'event-1', window: '24h', cursor: 'comparison-two' });
});


describe('version series with sparse observations', () => {
  const time = '2026-11-17T12:00:00Z';
  const line = (version, points) => ({ version, versionId: `version-${version}`, points });
  const point = (value, traces, t = time) => ({ value, t, traces });
  it('shows the actual zero and sample count for one time bucket without drawing a line', () => {
    renderRuntime(<MoveSeries metric="failed_requests" movedAt={time} series={{ series: [line(1, [point(0, 18)]), line(2, [point(null, 0)])] }} />);
    expect(screen.getByText('v1: 0% · one interval · 18 traces')).toBeVisible();
    expect(screen.queryByText(/v2:/)).toBeNull();
    expect(screen.queryByRole('img', { name: 'Failed requests by version' })).toBeNull();
  });
  it('keeps a measured trend beside a version that has only one interval', () => {
    const { container } = renderRuntime(<MoveSeries metric="latency_p95" movedAt={time} series={{ series: [line(1, [point(407, 12)]), line(2, [point(600, 20), point(700, 16, '2026-11-18T12:00:00Z')])] }} />);
    expect(screen.getByText('v1: 407 ms · one interval · 12 traces')).toBeVisible();
    expect(container.querySelectorAll('polyline')).toHaveLength(1);
  });
});
