/* eslint-disable react/prop-types */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen, within } from '@testing-library/react';
import RuntimeTab from './RuntimeTab';
import { setSkillTracesOverride } from './skillTracesFlag';
import { MOVE, NOW, RUNTIME, SKILL, failure, pending, renderRuntime, success } from './__tests__/fixtures';

const hooks = vi.hoisted(() => ({ runtime: vi.fn(), comparison: vi.fn(), series: vi.fn(), agent: vi.fn() }));
vi.mock('src/api/skills/runtime', async (importOriginal) => ({
  ...(await importOriginal()),
  useSkillRuntime: hooks.runtime,
  useSkillComparison: hooks.comparison,
  useSkillRuntimeSeries: hooks.series,
  useRuntimeAgent: hooks.agent,
}));
vi.mock('src/sections/skills/governance/AdoptionPanel', () => ({
  AuthorizedProjectPicker: ({ label }) => <div role="group" aria-label={label} />,
}));
vi.mock('src/components/iconify', () => ({ default: () => null }));
vi.mock('notistack', async (load) => ({ ...(await load()), enqueueSnackbar: vi.fn() }));

const show = (options) => renderRuntime(<RuntimeTab skill={SKILL} productionVersion={4} viewingVersion={4} />, options);
const before = (a, b) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(NOW);
  setSkillTracesOverride(true);
  hooks.runtime.mockReturnValue(success(RUNTIME));
  hooks.comparison.mockReturnValue(success(MOVE));
  hooks.series.mockReturnValue(success({ series: [] }));
  hooks.agent.mockReturnValue(success({ agent: null, rows: [] }));
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  setSkillTracesOverride(null);
});

describe('RuntimeTab', () => {
  it('shows failed loads, the contents warning, the lead, the strip, the table and About this data, in that order', () => {
    show();
    const failed = document.querySelector('[data-line="failed-loads"]');
    const contents = document.querySelector('[data-line="contents-mismatch"]');
    const lead = document.querySelector('[data-line="lead"]');
    const table = screen.getByRole('table', { name: 'Loaded by' });
    const about = screen.getByRole('button', { name: 'About this data' });
    expect(failed).toHaveTextContent('14 loads of refund-policy failed in the last 7 days (12 integrity, 2 authorization).');
    expect(failed).toHaveTextContent('Matched by the name the app reported.');
    expect(contents).toHaveTextContent("31 loads had contents that don't match the published version. They're left out of the numbers below.");
    expect(lead).toHaveTextContent('refund-policy is loaded by 3 agents in 2 projects. On the 1 agent that ran both, failed requests are 2.1% on v4 and 0.8% on v3.');
    expect([failed, contents, lead, table, about].every((node, index, all) => index === 0 || before(all[index - 1], node))).toBe(true);
    expect(screen.getByText('Cost per trace', { selector: 'p' })).toBeInTheDocument();
  });

  it('has one window control, defaulting to 7 days, and no Version filter', () => {
    show();
    expect(screen.getAllByRole('combobox')).toHaveLength(1);
    expect(screen.getByRole('combobox', { name: 'Window' })).toHaveTextContent('7 days');
    expect(screen.getByRole('group', { name: 'Projects' })).toBeInTheDocument();
    expect(hooks.runtime).toHaveBeenLastCalledWith(SKILL.id, { window: '7d', projectIds: [], cursor: undefined });
    fireEvent.mouseDown(screen.getByRole('combobox', { name: 'Window' }));
    fireEvent.click(screen.getByRole('option', { name: '24 hours' }));
    expect(hooks.runtime).toHaveBeenLastCalledWith(SKILL.id, { window: '24h', projectIds: [], cursor: undefined });
  });

  it('opens the move card named in the URL, before the table', () => {
    show({ route: '/dashboard/skills/skill-a?tab=runtime&move=event-1&window=6h' });
    expect(hooks.comparison).toHaveBeenCalledWith(SKILL.id, { eventId: 'event-1', window: '6h', projectIds: [], cursor: undefined });
    const card = document.querySelector('[data-card="move"]');
    expect(before(card, screen.getByRole('table', { name: 'Loaded by' }))).toBe(true);
  });

  it('shows the latest production move in the window without a URL', () => {
    hooks.runtime.mockReturnValue(success({ ...RUNTIME, move: { eventId: 'event-9', verdict: 'watching' } }));
    show();
    expect(hooks.comparison).toHaveBeenCalledWith(SKILL.id, { eventId: 'event-9', window: '24h', projectIds: [], cursor: undefined });
  });

  it('replaces the lead, strip and table with the diagnosis when nothing loaded', () => {
    hooks.runtime.mockReturnValue(success({ ...RUNTIME, rows: [], failedLoads: null, mismatch: null, diagnosis: { state: 'awaiting_loads', consumers: [{ label: 'k', channel: 'sdk', canRecord: true }] } }));
    show();
    expect(screen.getByText('No loads recorded')).toBeInTheDocument();
    expect(screen.getByText('Consumers can record loads, but none arrived in this window.')).toBeInTheDocument();
    expect(screen.queryByRole('table', { name: 'Loaded by' })).not.toBeInTheDocument();
    expect(document.querySelector('[data-line="lead"]')).toBeNull();
  });

  it('opens the agent panel from an agent row', () => {
    show();
    fireEvent.click(screen.getByText('refund-agent').closest('tr'));
    expect(screen.getByRole('dialog', { name: 'refund-agent' })).toBeInTheDocument();
    expect(hooks.agent).toHaveBeenLastCalledWith(expect.objectContaining({ projectId: 'p1', agent: 'refund-agent', window: '7d', enabled: true }));
  });

  it('reports unreadable projects as a count only', () => {
    show();
    expect(screen.getByText("1 project you can't open isn't included.")).toBeInTheDocument();
  });

  it.each([
    ['loading', pending(), () => expect(screen.getByRole('progressbar', { name: 'Loading runtime data' })).toBeInTheDocument()],
    ['error', failure('runtime_read_unavailable'), () => {
      expect(screen.getByText('Could not load runtime data. Your skill and versions are unchanged.')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /Retry/ })).toBeInTheDocument();
    }],
    ['slow', failure('runtime_read_budget_exceeded'), () => expect(screen.getByText('This took too long. Try a shorter window.')).toBeInTheDocument()],
    ['denied', failure('project_not_readable', 403), () => expect(screen.getByText("You can't open the projects where this skill is loaded. Ask a workspace admin for access.")).toBeInTheDocument()],
  ])('has a %s state', (_name, value, check) => {
    hooks.runtime.mockReturnValue(value);
    show();
    check();
    expect(screen.queryByRole('table', { name: 'Loaded by' })).not.toBeInTheDocument();
  });

  it('renders in dark mode with theme tokens', () => {
    show({ mode: 'dark' });
    expect(within(screen.getByRole('table', { name: 'Loaded by' })).getByText('refund-agent')).toBeInTheDocument();
  });
});

it('pages Runtime rows and restarts on a different window', () => {
  hooks.runtime.mockReturnValue(success({ ...RUNTIME, nextCursor: 'page-two' }));
  show();
  fireEvent.click(screen.getByRole('button', { name: 'Next page' }));
  expect(hooks.runtime.mock.calls.at(-1)[1].cursor).toBe('page-two');
  fireEvent.mouseDown(screen.getByRole('combobox', { name: 'Window' }));
  fireEvent.click(screen.getByRole('option', { name: '24 hours' }));
  expect(hooks.runtime.mock.calls.at(-1)[1].cursor).toBeUndefined();
  expect(screen.getByText('Page 1')).toBeInTheDocument();
});
