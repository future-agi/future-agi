/* eslint-disable react/prop-types */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen, within } from '@testing-library/react';
import ContextRuntimeView from './ContextRuntimeView';
import { setSkillTracesOverride } from './skillTracesFlag';
import { MOVE, NOW, ROWS, RUNTIME, V3, V4, failure, pending, renderRuntime, success } from './__tests__/fixtures';

const hooks = vi.hoisted(() => ({ context: vi.fn(), comparison: vi.fn(), agent: vi.fn() }));
vi.mock('src/api/skills/runtime', async (importOriginal) => ({
  ...(await importOriginal()),
  useContextRuntime: hooks.context,
  useContextComparison: hooks.comparison,
  useRuntimeAgent: hooks.agent,
}));
vi.mock('src/sections/skills/governance/AdoptionPanel', () => ({
  AuthorizedProjectPicker: ({ label }) => <div role="group" aria-label={label} />,
}));
vi.mock('src/components/iconify', () => ({ default: () => null }));
vi.mock('notistack', async (load) => ({ ...(await load()), enqueueSnackbar: vi.fn() }));

const CONTEXT = { id: 'ctx-1', name: 'support-agent' };
const withSkill = (row) => ({ ...row, skillId: 'skill-a', skillName: 'refund-policy' });
const DATA = {
  ...RUNTIME,
  rows: ROWS.map(withSkill),
  loadedNotLinked: [{ skillId: 'skill-a', name: 'refund-policy', versionId: V3, version: 3, traces: 400 }],
  linkedNotLoaded: [{ skillId: 'skill-b', name: 'escalation-tone', linkedVersion: 2 }],
  linksSummary: [],
  move: { eventId: 'release-1' },
};
const RELEASE = { ...MOVE, eventId: 'release-1', changedSkills: [{ skillId: 'skill-a', name: 'refund-policy', fromVersion: 3, toVersion: 4, change: 'changed' }, { skillId: 'skill-c', name: 'tone', fromVersion: null, toVersion: 1, change: 'added' }] };
const show = (options) => renderRuntime(<ContextRuntimeView context={CONTEXT} versionId="ctx-v2" />, { route: '/dashboard/skills/contexts/ctx-1?tab=runtime', ...options });

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(NOW);
  setSkillTracesOverride(true);
  hooks.context.mockReturnValue(success(DATA));
  hooks.comparison.mockReturnValue(success(RELEASE));
  hooks.agent.mockReturnValue(success({ agent: null, rows: [] }));
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  setSkillTracesOverride(null);
});

describe('ContextRuntimeView', () => {
  it('shows one setup action rather than an empty project picker when the workspace has no tracing project', () => {
    hooks.context.mockReturnValue(success({ ...DATA, rows: [], failedLoads: null, mismatch: null, diagnosis: { state: 'no_tracing_project' } }));
    show();
    expect(screen.getAllByRole('link', { name: 'Set up tracing' })).toHaveLength(1);
    expect(screen.queryByRole('group', { name: 'Projects' })).toBeNull();
    expect(screen.queryByRole('combobox', { name: 'Window' })).toBeNull();
    const links = document.querySelector('[data-section="linked-no-loads"]');
    expect(within(links).getByText('escalation-tone')).toBeVisible();
    expect(links.textContent).not.toMatch(/tracing project/i);
  });

  it('reads the viewed context version with one window and Projects, and no version selector of its own', () => {
    show();
    expect(hooks.context).toHaveBeenLastCalledWith('ctx-1', { window: '7d', projectIds: [], versionId: 'ctx-v2', cursor: undefined });
    expect(screen.getAllByRole('combobox')).toHaveLength(1);
    expect(screen.getByRole('group', { name: 'Projects' })).toBeInTheDocument();
  });

  it('orders failed loads, lead, strip, the release card, Skills loaded at runtime, then Linked, no loads recorded', () => {
    show();
    const order = [
      document.querySelector('[data-line="failed-loads"]'),
      document.querySelector('[data-line="lead"]'),
      document.querySelector('[data-card="context-release"]'),
      screen.getByRole('table', { name: 'Skills loaded at runtime' }),
      document.querySelector('[data-section="linked-no-loads"]'),
    ];
    order.forEach((node) => expect(node).not.toBeNull());
    order.slice(1).forEach((node, index) => expect(Boolean(order[index].compareDocumentPosition(node) & Node.DOCUMENT_POSITION_FOLLOWING)).toBe(true));
  });

  it('lists the release card with the skills that changed', () => {
    show();
    expect(hooks.comparison).toHaveBeenCalledWith('ctx-1', { eventId: 'release-1', window: '24h', projectIds: [], cursor: undefined });
    const card = document.querySelector('[data-card="context-release"]');
    expect(within(card).getByText('Released v4 (was v3) · 24 hours each side')).toBeInTheDocument();
    expect(within(card).getByText('refund-policy: v3 → v4')).toBeInTheDocument();
    expect(within(card).getByText('tone: added at v1')).toBeInTheDocument();
  });

  it('marks each loaded version linked or not linked here, and expands agents', () => {
    show();
    const table = screen.getByRole('table', { name: 'Skills loaded at runtime' });
    const rows = within(table).getAllByRole('row').filter((row) => row.dataset.row === 'skill-version');
    expect(within(rows.find((row) => within(row).queryByText('v4'))).getByText('linked')).toBeInTheDocument();
    expect(within(rows.find((row) => within(row).queryByText('v3'))).getByText('not linked here')).toBeInTheDocument();
    expect(within(table).queryByText('refund-agent')).not.toBeInTheDocument();
    fireEvent.click(rows.find((row) => within(row).queryByText('v4')));
    fireEvent.click(within(table).getByText('refund-agent').closest('tr'));
    expect(screen.getByRole('dialog', { name: 'refund-agent' })).toBeInTheDocument();
  });

  it('links numbers to Observe with the server links', () => {
    show();
    const link = screen.getByRole('link', { name: 'refund-policy v3 traces: open traces' });
    expect(JSON.parse(new URL(link.getAttribute('href'), 'https://x.invalid').searchParams.get('observeLinkFilter'))[0].filter_config.filter_value).toBe(V3);
    expect(V4).toBeTruthy();
  });

  it('explains each linked skill with no loads, never as unused', () => {
    show();
    const section = document.querySelector('[data-section="linked-no-loads"]');
    expect(within(section).getByText('Linked, no loads recorded')).toBeInTheDocument();
    expect(within(section).getByRole('link', { name: 'escalation-tone' })).toHaveAttribute('href', '/dashboard/skills/skill-b?tab=runtime');
    expect(within(section).getByText('Other skills in this context recorded loads in this window. No app read this one.')).toBeInTheDocument();
    expect(section.textContent).not.toMatch(/unused/i);
  });

  it('shows the diagnosis when nothing loaded', () => {
    hooks.context.mockReturnValue(success({ ...DATA, rows: [], move: null, failedLoads: null, diagnosis: { state: 'never_fetched', consumers: [] } }));
    show();
    expect(screen.getByText('Nobody has fetched this agent context yet.')).toBeInTheDocument();
    expect(screen.queryByRole('table', { name: 'Skills loaded at runtime' })).not.toBeInTheDocument();
  });

  it.each([
    ['loading', pending(), 'Loading runtime data'],
    ['error', failure('runtime_read_unavailable'), 'Could not load runtime data. Your agent context and versions are unchanged.'],
    ['denied', failure('project_not_readable', 403), "You can't open the projects where this agent context is loaded. Ask a workspace admin for access."],
  ])('has a %s state', (_name, value, text) => {
    hooks.context.mockReturnValue(value);
    show();
    expect(screen.getAllByText((_, node) => node?.getAttribute?.('aria-label') === text || node?.textContent === text).length).toBeGreaterThan(0);
  });
});

it('pages both context rows and comparison agents independently', () => {
  hooks.context.mockReturnValue(success({ ...DATA, nextCursor: 'context-two' }));
  hooks.comparison.mockReturnValue(success({ ...RELEASE, nextCursor: 'compare-two' }));
  show();
  const buttons = screen.getAllByRole('button', { name: 'Next page' });
  fireEvent.click(buttons[0]);
  expect(hooks.comparison.mock.calls.at(-1)[1].cursor).toBe('compare-two');
  expect(hooks.context.mock.calls.at(-1)[1].cursor).toBeUndefined();
  fireEvent.click(buttons[1]);
  expect(hooks.context.mock.calls.at(-1)[1].cursor).toBe('context-two');
});
