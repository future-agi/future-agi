import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import ContextDetailView from './ContextDetailView';

// SKILL-TRACES §16 UXF-14 (the release request has one name), UXF-16 ("update rules") and the
// stable tab URL values the shell owns. No Stream A surface renders in commit 1.

const state = vi.hoisted(() => ({ upstream: {} }));
vi.mock('src/api/skills/context-skill-inventory', () => ({ useLinkedSkillUpstream: () => state.upstream }));
vi.mock('src/components/iconify', () => ({ default: () => null }));
vi.mock('../utils/access', () => ({ useSkillsAccess: () => ({ ready: true, sameScope: true, isViewer: false, canWrite: true, canAdmin: false }) }));
afterEach(cleanup);
beforeEach(() => { state.upstream = {}; try { localStorage.clear(); } catch { /* optional */ } });

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
