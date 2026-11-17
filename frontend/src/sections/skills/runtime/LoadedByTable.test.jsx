import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen, within } from '@testing-library/react';
import LoadedByTable from './LoadedByTable';
import { setSkillTracesOverride } from './skillTracesFlag';
import { ROWS, SKILL, V3, V4, renderRuntime } from './__tests__/fixtures';

vi.mock('src/components/iconify', () => ({ default: () => null }));
vi.mock('notistack', async (load) => ({ ...(await load()), enqueueSnackbar: vi.fn() }));

const show = (props = {}) => renderRuntime(<LoadedByTable skill={SKILL} rows={ROWS} productionVersion={4} {...props} />);
const href = (element) => new URL(element.getAttribute('href'), 'https://example.invalid');

beforeEach(() => setSkillTracesOverride(true));
afterEach(() => {
  cleanup();
  setSkillTracesOverride(null);
});

describe('LoadedByTable', () => {
  it('shows the default columns, with the most-run eval and no totals row', () => {
    show();
    const headers = screen.getAllByRole('columnheader').map((cell) => cell.textContent);
    expect(headers.slice(0, 8)).toEqual(['Agent', 'Version', 'Traces', 'Failed requests', 'p95', 'Cost per trace', 'Groundedness', 'Last loaded']);
    expect(screen.queryByText(/total/i)).not.toBeInTheDocument();
  });

  it('adds chooser columns without dropping the defaults', () => {
    show();
    fireEvent.click(screen.getByRole('button', { name: 'Columns' }));
    fireEvent.click(screen.getByRole('menuitemcheckbox', { name: /p50/ }));
    fireEvent.click(screen.getByRole('menuitemcheckbox', { name: /Asked for/ }));
    fireEvent.keyDown(screen.getByRole('menu'), { key: 'Escape' });
    const headers = screen.getAllByRole('columnheader', { hidden: true }).map((cell) => cell.textContent);
    expect(headers).toEqual(expect.arrayContaining(['Traces', 'p50', 'Asked for']));
    expect(screen.getAllByText('Asked for production 96% · asked for v3 4%').length).toBeGreaterThan(0);
  });

  it('opens the production version to its agents and keeps other versions folded until clicked', () => {
    show();
    expect(screen.getByText('refund-agent')).toBeInTheDocument();
    expect(screen.queryByText('billing-agent')).not.toBeInTheDocument();
    const v3Row = screen.getAllByRole('row').find((row) => row.dataset.row === 'version' && within(row).queryByText('v3'));
    fireEvent.click(v3Row);
    expect(screen.getByText('billing-agent')).toBeInTheDocument();
  });

  it('makes every counted number a same-tab link to exactly its cohort', () => {
    show();
    const traces = screen.getByRole('link', { name: 'refund-agent v4 traces: open traces' });
    expect(href(traces).pathname).toBe('/dashboard/observe/p1/llm-tracing');
    expect(traces).not.toHaveAttribute('target');
    expect(JSON.parse(href(traces).searchParams.get('observeLinkFilter'))).toEqual(ROWS[1].links.all.filters);
    const failed = screen.getByRole('link', { name: 'refund-agent v4 failed requests: open traces' });
    expect(JSON.parse(href(failed).searchParams.get('observeLinkFilter'))).toEqual(ROWS[1].links.failed.filters);
    const p95 = screen.getByRole('link', { name: 'refund-agent v4 p95: open traces' });
    expect(JSON.parse(href(p95).searchParams.get('observeLinkSort'))).toEqual({ column_id: 'latency', direction: 'desc' });
    const evalCell = screen.getByRole('link', { name: 'refund-agent v4 Groundedness: open traces' });
    expect(evalCell).toHaveTextContent('0.82 · 40% evaluated');
    expect(JSON.parse(href(evalCell).searchParams.get('observeLinkFilter'))).toEqual(ROWS[1].links.eval_failed['eval-1'].filters);
  });

  it('takes the project from the links container or the agent when a sub-link has none', () => {
    const bare = (value) => ({ ...value, project_id: undefined });
    const row = { ...ROWS[1], links: { project_id: undefined, all: bare(ROWS[1].links.all), failed: bare(ROWS[1].links.failed), slowest: bare(ROWS[1].links.slowest), eval_failed: {} } };
    show({ rows: [ROWS[0], row] });
    expect(href(screen.getByRole('link', { name: 'refund-agent v4 traces: open traces' })).pathname).toBe('/dashboard/observe/p1/llm-tracing');
    expect(href(screen.getByRole('link', { name: 'refund-agent v4 failed requests: open traces' })).pathname).toBe('/dashboard/observe/p1/llm-tracing');
  });

  it('never says "0 agents" for a version row without agent rows', () => {
    show({ rows: [ROWS[3]] });
    expect(screen.queryByText(/\b0 agents\b/)).not.toBeInTheDocument();
  });

  it('opens a project menu, not a dialog, for a version row across several projects', () => {
    show();
    fireEvent.click(screen.getByRole('button', { name: 'Choose a project for v4 traces' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    const option = screen.getByRole('menuitem', { name: 'acme-support-production · 1,000 traces · 30 failed' });
    expect(screen.getByRole('menuitem', { name: 'acme-sales · 204 traces · 1 failed' })).toBeInTheDocument();
    fireEvent.click(option);
    expect(screen.getByTestId('location').textContent).toMatch(/^\/dashboard\/observe\/p1\/llm-tracing\?/);
  });

  it('shows a low-sample cell as its count in the secondary colour with no change', () => {
    show();
    const lowRow = screen.getAllByRole('row').find((row) => within(row).queryByText('Unnamed agent in acme-sales'));
    expect(within(lowRow).getByText('2 of 12')).toBeInTheDocument();
    expect(within(lowRow).getByRole('button', { name: 'Name it' })).toBeInTheDocument();
  });

  it('folds versions with no loads into one line', () => {
    show({ allVersions: [{ id: V4, number: 4 }, { id: V3, number: 3 }, { id: 'v2', number: 2 }, { id: 'v1', number: 1 }] });
    expect(screen.getByText('2 versions with no loads recorded')).toBeInTheDocument();
  });

  it('opens the agent panel from an agent row, and the numbers do not', () => {
    const onOpenAgent = vi.fn();
    show({ onOpenAgent });
    fireEvent.click(screen.getByRole('link', { name: 'refund-agent v4 traces: open traces' }));
    expect(onOpenAgent).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText('refund-agent').closest('tr'));
    expect(onOpenAgent).toHaveBeenCalledWith(ROWS[1]);
  });

  it('keeps Open skill version and Copy link in the row menu', () => {
    show();
    fireEvent.click(screen.getByRole('button', { name: 'Actions for v4' }));
    expect(screen.getByRole('menuitem', { name: 'Open skill version' })).toHaveAttribute('href', '/dashboard/skills/skill-a?version=4');
    expect(screen.getByRole('menuitem', { name: 'Copy link' })).toBeInTheDocument();
  });

  it('with skillTraces off, only the trace count stays a link', () => {
    setSkillTracesOverride(false);
    show();
    expect(screen.getByRole('link', { name: 'refund-agent v4 traces: open traces' })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'refund-agent v4 failed requests: open traces' })).not.toBeInTheDocument();
  });
});
