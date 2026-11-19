import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import ContextVersionCompare from './ContextVersionCompare';

const state = vi.hoisted(() => ({ inspections: {}, files: {}, links: {}, linkQueries: [], loader: vi.fn(), fileCalls: [] }));
vi.mock('src/api/skills/context-production', () => ({
  loadAcceptedDependencyComparison: (...args) => state.loader(...args),
  useContextInspection: (_context, number) => state.inspections[number] || { isLoading: true },
  useContextInspectionFile: (...args) => { state.fileCalls.push(args); const [, number, path] = args; return (path && state.files[`${number}:${path}`]) || { isLoading: false }; },
}));
vi.mock('src/api/skills/testing', () => ({ useSkillV11Query: (options) => { state.linkQueries.push(options); return state.links; } }));
afterEach(cleanup);
const sha = (c) => `sha256:${c.repeat(64)}`;
const file = (path, c) => ({ path, sha256: sha(c), sizeBytes: 4, origin: { kind: 'owned' } });
const inspection = (number, files) => ({ data: { version: { id: `id-${number}`, number, digest: sha(String(number)) }, files }, refetch: vi.fn() });
const ready = () => {
  state.inspections = {
    1: inspection(1, [file('AGENTS.md', 'a'), file('docs/policy.md', 'b'), file('docs/old.md', 'c'), file('.futureagi/context.json', 'd'), file('same.md', 'e')]),
    2: inspection(2, [file('AGENTS.md', 'a'), file('docs/policy.md', 'f'), file('docs/new.md', 'g'), file('.futureagi/context.json', 'h'), file('same.md', 'e')]),
  };
  state.links = { data: [{ mount: 'skills/refund-policy/', before: { version: 1 }, after: { version: 2 }, change: 'Changed' }], isError: false, isLoading: false, refetch: vi.fn() };
};
beforeEach(() => { state.inspections = {}; state.files = {}; state.links = {}; state.linkQueries = []; state.fileCalls = []; state.loader = vi.fn(); });
const mount = (props = {}) => render(<ContextVersionCompare contextId="ctx" from={1} to={2} onClose={vi.fn()} {...props} />);

it('shows a skeleton on the first load and no result before both versions arrive', () => {
  state.inspections = { 1: inspection(1, []) };
  mount();
  expect(screen.queryByRole('status')).not.toBeInTheDocument();
  expect(screen.queryByText('No linked-skill changes.')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Close compare' })).toBeVisible();
});
it('leads with one sentence, lists linked-skill moves and groups files by Added, Removed and Changed', () => {
  ready();
  mount();
  expect(screen.getByRole('status')).toHaveTextContent('v2 changes 1 linked skill and 4 files compared with v1.');
  expect(screen.getByText('refund-policy v1 → v2')).toBeVisible();
  const added = screen.getByRole('group', { name: 'Added files' });
  expect(within(added).getByRole('button', { name: 'docs/new.md' })).toBeVisible();
  expect(within(screen.getByRole('group', { name: 'Removed files' })).getByRole('button', { name: 'docs/old.md' })).toBeVisible();
  const changed = screen.getByRole('group', { name: 'Changed files' });
  expect(within(changed).getByRole('button', { name: 'docs/policy.md' })).toBeVisible();
  // The generated manifest is labelled for people, with the path kept in the title.
  expect(within(changed).getByRole('button', { name: 'Context settings (generated)' })).toHaveAttribute('title', '.futureagi/context.json');
  expect(screen.queryByRole('button', { name: 'AGENTS.md' })).not.toBeInTheDocument();
});
it('keeps unchanged files behind a disclosure and compares by the full sha256, not the path', () => {
  ready();
  mount();
  const disclosure = screen.getByRole('button', { name: 'Show 2 unchanged files' });
  expect(disclosure).toHaveAttribute('aria-expanded', 'false');
  expect(screen.queryByText('same.md')).not.toBeInTheDocument();
  fireEvent.click(disclosure);
  expect(screen.getByText('same.md')).toBeVisible();
  expect(screen.getByText('AGENTS.md')).toBeVisible();
});
it('says plainly when nothing changed', () => {
  state.inspections = { 1: inspection(1, [file('AGENTS.md', 'a')]), 2: inspection(2, [file('AGENTS.md', 'a')]) };
  state.links = { data: [], isError: false, refetch: vi.fn() };
  mount();
  expect(screen.getByRole('status')).toHaveTextContent('v2 has no changes compared with v1.');
  expect(screen.getByText('No linked-skill changes.')).toBeVisible();
  expect(screen.getByText('No file changes.')).toBeVisible();
});
it('fences the linked-skill comparison to the newer version digest and only runs once it is known', async () => {
  ready();
  state.loader.mockResolvedValue([]);
  mount();
  const options = state.linkQueries.at(-1);
  expect(options.enabled).toBe(true);
  await options.load({ signal: 'signal' });
  expect(state.loader).toHaveBeenCalledWith('ctx', 1, 2, sha('2'), { signal: 'signal' });
});
it('keeps the file result when the linked-skill comparison fails, and retries only that part', () => {
  ready();
  const refetch = vi.fn();
  state.links = { isError: true, refetch };
  mount();
  expect(screen.getByRole('status')).toHaveTextContent('v2 changes 4 files compared with v1.');
  expect(screen.getByText("Couldn't load the linked-skill comparison.")).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  expect(refetch).toHaveBeenCalledTimes(1);
  expect(screen.getByRole('button', { name: 'docs/policy.md' })).toBeVisible();
});
it('reports a version that failed to load, with Retry for both reads', () => {
  ready();
  const refetch = vi.fn();
  state.inspections[2] = { isError: true, refetch };
  mount();
  expect(screen.getByText("Couldn't load the comparison.")).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  expect(refetch).toHaveBeenCalledTimes(1);
  expect(state.inspections[1].refetch).toHaveBeenCalledTimes(1);
});
it('opens a changed file as a diff of both versions, each read at its own identity', async () => {
  ready();
  state.files = { '1:docs/policy.md': { data: { text: 'old line\n' } }, '2:docs/policy.md': { data: { text: 'new line\n' } } };
  const user = userEvent.setup();
  mount();
  await user.click(screen.getByRole('button', { name: 'docs/policy.md' }));
  expect(screen.getByRole('button', { name: 'docs/policy.md' })).toHaveAttribute('aria-pressed', 'true');
  expect(screen.getAllByText('docs/policy.md').length).toBeGreaterThan(0);
  expect(screen.getByRole('table', { name: 'File changes' })).toBeVisible();
  expect(state.fileCalls).toEqual(expect.arrayContaining([['ctx', 1, 'docs/policy.md', 'id-1', sha('1')], ['ctx', 2, 'docs/policy.md', 'id-2', sha('2')]]));
});
it('loads only the side that has an added or removed file', async () => {
  ready();
  state.files = { '2:docs/new.md': { data: { text: 'hello\n' } } };
  const user = userEvent.setup();
  mount();
  await user.click(screen.getByRole('button', { name: 'docs/new.md' }));
  const calls = state.fileCalls.filter(([, , path]) => path === 'docs/new.md');
  expect(calls.map(([, number]) => number)).toEqual([2]);
  expect(state.fileCalls.some(([, number, path]) => number === 1 && path)).toBe(false);
});
it('closes with the Close compare button', async () => {
  ready();
  const onClose = vi.fn();
  mount({ onClose });
  await userEvent.setup().click(screen.getByRole('button', { name: 'Close compare' }));
  expect(onClose).toHaveBeenCalledTimes(1);
});
