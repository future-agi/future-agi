/* eslint-disable react/prop-types -- Test doubles mirror the real component props. */
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { setSkillTracesOverride } from '../runtime/skillTracesFlag';
import ContextDetailView from './ContextDetailView';

// SKILL-TRACES §16 UXF-14 (the release request has one name), UXF-16 ("update rules") and the
// stable tab URL values the shell owns. Stream A surfaces render only with `skillTraces` on (end of file).

const state = vi.hoisted(() => ({ upstream: {} }));
vi.mock('src/api/skills/context-skill-inventory', () => ({ useLinkedSkillUpstream: () => state.upstream }));
vi.mock('src/components/iconify', () => ({ default: () => null }));
vi.mock('../utils/access', () => ({ useSkillsAccess: () => ({ ready: true, sameScope: true, isViewer: false, canWrite: true, canAdmin: false }) }));
// The "Loaded (7 days)" column comes from lane ST10; this suite checks only that the shell
// passes it to the Linked skills table and lists loads outside the links under it.
const loads = vi.hoisted(() => ({ hook: vi.fn() }));
vi.mock('../runtime/LinkedLoadsCell', () => ({
  useLinkedLoadsColumn: (...args) => {
    loads.hook(...args);
    return { column: { header: 'Loaded (7 days)', cell: ({ link }) => <span>{`${link.skillId} loads`}</span> }, loadedNotLinked: [{ skillId: 'tone', name: 'tone', version: 1 }] };
  },
  LoadedNotLinkedList: ({ items }) => (items.length ? <section aria-label="Loaded but not linked here">{items.map((item) => item.name).join(', ')}</section> : null),
}));
afterEach(() => { cleanup(); setSkillTracesOverride(null); });
beforeEach(() => { state.upstream = {}; loads.hook.mockClear(); try { localStorage.clear(); } catch { /* optional */ } });

const scopeKey = 'scope';
const v1 = { id: 'v1', number: 1, digest: `sha256:${'a'.repeat(64)}` };
const v2 = { id: 'v2', number: 2, digest: `sha256:${'b'.repeat(64)}` };
const links = [{ skillId: 'follow', requested: { label: 'production' }, selector: { label: 'production' }, version: 1, versionId: 'old', digest: v1.digest, mount: 'skills/follow/' }];
const detail = (overrides = {}) => ({
  scopeKey,
  query: { scopeKey, data: { context: { id: 'context', name: 'support-agent', latestVersion: 2, labels: [{ name: 'production', version: 1 }] }, latest: v2 } },
  versionQuery: { scopeKey, data: { version: v1, descriptor: { description: 'Support instructions', links }, files: [{ path: 'AGENTS.md', origin: { kind: 'owned' } }, { path: '.futureagi/context.json', origin: { kind: 'generated' } }] } },
  fileQuery: { scopeKey, data: { path: 'AGENTS.md', versionId: v1.id, text: '# Instructions' } },
  versions: [v2, v1],
  onEditViewed: vi.fn(),
  sections: { tests: <p>Test controls</p>, versions: <p>Versions table</p>, consumers: <p>Apps</p>, recovery: <p>Recovery controls</p>, use: <p>Install</p> },
  ...overrides,
});
function Location() {
  const location = useLocation();
  return <output aria-label="Location">{location.search}</output>;
}
const show = (props = detail(), entry = '/') => render(<MemoryRouter initialEntries={[entry]}><ContextDetailView {...props} /><Location /></MemoryRouter>);

it('names the header action "Request release for v2" and keeps its exact candidate in the link', () => {
  show();
  const release = screen.getByRole('link', { name: 'Request release for v2' });
  expect(release).toHaveClass('MuiButton-outlined');
  const url = new URL(release.href);
  expect(url.searchParams.get('candidate')).toBe('v2');
  expect(url.searchParams.get('digest')).toBe(v2.digest);
  expect(screen.queryByText(/Request release review/)).not.toBeInTheDocument();
});

it('offers the first release by the same name before production exists', () => {
  const props = detail();
  show({ ...props, query: { ...props.query, data: { ...props.query.data, context: { ...props.query.data.context, latestVersion: 1, labels: [] }, latest: v1 } }, versions: [v1] });
  expect(screen.getByRole('link', { name: 'Request release for v1' })).toBeVisible();
});

it('keeps the tab URL values and order, with no Runtime or Activity tab unflagged', () => {
  show();
  expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual(['Instructions', 'Linked skills', 'Tests', 'Versions', 'Connected apps', 'Rollback', 'Install']);
  fireEvent.click(screen.getByRole('tab', { name: 'Linked skills' }));
  expect(screen.getByLabelText('Location')).toHaveTextContent('tab=links');
  fireEvent.click(screen.getByRole('tab', { name: 'Connected apps' }));
  expect(screen.getByLabelText('Location')).toHaveTextContent('tab=consumers');
  expect(screen.queryByRole('tab', { name: 'Runtime' })).not.toBeInTheDocument();
  expect(screen.queryByRole('tab', { name: 'Activity' })).not.toBeInTheDocument();
});

it('shows the linked skills read view with the shared names', () => {
  state.upstream = { follow: { data: { name: 'refund-policy', labels: [{ name: 'production', version: 2, versionId: 'new' }] } } };
  show(detail(), '/?tab=links');
  const table = screen.getByRole('table', { name: 'Linked skills' });
  expect(within(table).getByRole('columnheader', { name: 'Update rule' })).toBeVisible();
  expect(within(table).getByText('Follows production')).toBeVisible();
  expect(within(table).getByRole('button', { name: 'Check for update' })).toBeVisible();
});

it('says "update rules" for the generated file', () => {
  show(detail({ selectedPath: '.futureagi/context.json' }));
  expect(screen.getByText('Generated file · read-only. Records the update rules and locked skill versions.')).toBeVisible();
  expect(screen.queryByText(/update policies/)).not.toBeInTheDocument();
});

// SKILL-TRACES §7.4, §15 D5 and §16 UXF-07 (UXF5 commit 2), with `skillTraces` on.
const traceSections = (props = detail()) => ({ ...props, sections: { ...props.sections, runtime: <p>Context runtime</p>, usage: <p>Context fetches</p> } });

it('orders the context tabs like the skill page when skillTraces is on', () => {
  setSkillTracesOverride(true);
  show(traceSections());
  expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual(['Instructions', 'Linked skills', 'Tests', 'Runtime', 'Versions', 'Connected apps', 'Rollback', 'Activity', 'Install']);
  fireEvent.click(screen.getByRole('tab', { name: 'Runtime' }));
  expect(screen.getByLabelText('Location')).toHaveTextContent('tab=runtime');
  expect(screen.getByText('Context runtime')).toBeVisible();
  fireEvent.click(screen.getByRole('tab', { name: 'Activity' }));
  expect(screen.getByLabelText('Location')).toHaveTextContent('tab=usage');
  expect(screen.getByText('Context fetches')).toBeVisible();
});

it('adds Loaded (7 days) to Linked skills for the viewed version, with loads outside the links below', () => {
  setSkillTracesOverride(true);
  show(traceSections(), '/?tab=links');
  const table = screen.getByRole('table', { name: 'Linked skills' });
  expect(within(table).getByRole('columnheader', { name: 'Loaded (7 days)' })).toBeVisible();
  expect(within(table).getByText('follow loads')).toBeVisible();
  expect(loads.hook).toHaveBeenLastCalledWith('context', 'v1', { enabled: true });
  expect(screen.getByRole('region', { name: 'Loaded but not linked here' })).toHaveTextContent('tone');
});

it('shows no Runtime or Activity tab, column or list with skillTraces off, even when sections exist', () => {
  setSkillTracesOverride(false);
  show(traceSections(), '/?tab=runtime');
  expect(screen.queryByRole('tab', { name: 'Runtime' })).not.toBeInTheDocument();
  expect(screen.queryByRole('tab', { name: 'Activity' })).not.toBeInTheDocument();
  expect(screen.queryByText('Context runtime')).not.toBeInTheDocument();
  expect(screen.getByText("That section isn't available here. Showing Instructions.")).toBeVisible();
  cleanup();
  show(traceSections(), '/?tab=links');
  expect(within(screen.getByRole('table', { name: 'Linked skills' })).queryByRole('columnheader', { name: 'Loaded (7 days)' })).not.toBeInTheDocument();
  expect(loads.hook).not.toHaveBeenCalled();
  expect(screen.queryByRole('region', { name: 'Loaded but not linked here' })).not.toBeInTheDocument();
});
