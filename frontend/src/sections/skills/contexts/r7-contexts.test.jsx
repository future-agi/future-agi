import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter, useNavigate, useSearchParams } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import ContextDetailView from './ContextDetailView';
import ContextLibraryView from './ContextLibraryView';
import ContextLinksPanel from './ContextLinksPanel';

const state = vi.hoisted(() => ({ upstream: {} }));
vi.mock('src/api/skills/context-skill-inventory', () => ({ useLinkedSkillUpstream: () => state.upstream }));
afterEach(cleanup);
beforeEach(() => { state.upstream = {}; });
const version = { id: 'v1', number: 1, digest: `sha256:${'a'.repeat(64)}` };
const scopeKey = 'scope';
const detail = { scopeKey, query: { scopeKey, data: { context: { id: 'context', name: 'support-agent', latestVersion: 1, labels: [{ name: 'production', version: 1 }] } } }, versionQuery: { scopeKey, data: { version, descriptor: { description: 'Support instructions', links: [] }, files: [{ path: 'AGENTS.md', origin: { kind: 'owned' } }] } }, fileQuery: { scopeKey, data: { path: 'AGENTS.md', versionId: version.id, text: '# Instructions' } }, versions: [version], onEditViewed: vi.fn(), sections: { tests: <p>Test controls</p>, recovery: <p>Recovery controls</p> } };
function Navigation() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  return <><output aria-label="URL parameters">{params.toString()}</output><button type="button" onClick={() => navigate(-1)}>Back</button><button type="button" onClick={() => navigate(1)}>Forward</button></>;
}
it('owns section state in the URL, preserves version and test parameters, and restores back/forward', () => {
  render(<MemoryRouter initialEntries={['/?version=1&draft=1&reproduce=case&tab=recovery']}><Navigation /><ContextDetailView {...detail} /></MemoryRouter>);
  expect(screen.getByRole('tabpanel')).toHaveTextContent('Recovery controls');
  fireEvent.click(screen.getByRole('tab', { name: 'Tests' }));
  expect(screen.getByLabelText('URL parameters')).toHaveTextContent('version=1&draft=1&reproduce=case&tab=tests');
  expect(screen.getByRole('tabpanel')).toHaveTextContent('Test controls');
  fireEvent.click(screen.getByRole('button', { name: 'Back' }));
  expect(screen.getByRole('tab', { name: 'Recovery' })).toHaveAttribute('aria-selected', 'true');
  fireEvent.click(screen.getByRole('button', { name: 'Forward' }));
  expect(screen.getByRole('tab', { name: 'Tests' })).toHaveAttribute('aria-selected', 'true');
});
it('omits missing destinations and explains an unavailable section', () => {
  render(<MemoryRouter initialEntries={['/?tab=use']}><ContextDetailView {...detail} /></MemoryRouter>);
  expect(screen.queryByRole('tab', { name: 'Use' })).not.toBeInTheDocument();
  expect(screen.getByRole('tab', { name: 'Instructions' })).toHaveAttribute('aria-selected', 'true');
  expect(screen.getByText(/That section isn't available here/)).toBeVisible();
});
it('retains the header and sections while release bytes load', () => {
  render(<MemoryRouter><ContextDetailView {...detail} versionQuery={{ scopeKey, isLoading: true }} /></MemoryRouter>);
  expect(screen.getAllByText('support-agent').length).toBeGreaterThan(0);
  expect(screen.getByRole('tab', { name: 'Instructions' })).toBeVisible();
  expect(screen.queryByLabelText('AGENTS.md read-only source')).not.toBeInTheDocument();
});
const library = { scopeKey, query: { scopeKey, data: { count: 0, page: 1, pageSize: 25, results: [] } }, importActions: { onCreate: vi.fn() } };
it('distinguishes first run, filtered empty, out-of-range and failed reads', () => {
  const filter = vi.fn();
  const view = render(<ContextLibraryView {...library} onFilterChange={filter} />);
  expect(screen.getByRole('button', { name: 'Create agent context' })).toBeVisible();
  expect(screen.queryByRole('button', { name: 'New agent context' })).not.toBeInTheDocument();
  view.rerender(<ContextLibraryView {...library} filters={{ search: 'missing' }} onFilterChange={filter} />);
  expect(screen.getByText('No contexts match these filters')).toBeVisible();
  expect(screen.getByRole('button', { name: 'New agent context' })).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Clear filters' }));
  expect(filter).toHaveBeenCalledWith({ page: 1, search: '', tag: '', label: '' });
  view.rerender(<ContextLibraryView {...library} query={{ ...library.query, data: { ...library.query.data, page: 3 } }} onFilterChange={filter} />);
  fireEvent.click(screen.getByRole('button', { name: 'Go to first page' }));
  expect(filter).toHaveBeenLastCalledWith({ page: 1 });
  view.rerender(<ContextLibraryView {...library} query={{ scopeKey, error: new Error('offline'), refetch: vi.fn() }} />);
  expect(screen.getByText("Couldn't load agent contexts.")).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Create agent context' })).not.toBeInTheDocument();
});
it('shows each followed label against the locked version and never calls failed lookups current', () => {
  const retry = vi.fn();
  state.upstream = { follow: { data: { name: 'refund-policy', labels: [{ name: 'production', version: 2, versionId: 'new' }] } }, failed: { isError: true, refetch: retry } };
  const links = [{ skillId: 'follow', selector: { label: 'production' } }, { skillId: 'pin', selector: { version: 1 } }, { skillId: 'failed', selector: { label: 'production' } }];
  const session = { contextSpec: { links }, resolvedLock: { links: links.map((link) => ({ ...link, version: 1, versionId: 'old', digest: version.digest, mount: `skills/${link.skillId}/` })) } };
  render(<MemoryRouter><ContextLinksPanel scopeKey="scope" readOnly session={session} /></MemoryRouter>);
  const table = screen.getByRole('table', { name: 'Linked skills' });
  expect(within(table).getByText('v2 available')).toBeVisible();
  expect(within(table).getByText('Pinned intentionally')).toBeVisible();
  expect(within(table).getByText("Couldn't check")).toBeVisible();
  expect(within(table).queryByText('Up to date')).not.toBeInTheDocument();
  fireEvent.click(within(table).getByRole('button', { name: 'Retry' }));
  expect(retry).toHaveBeenCalledOnce();
  expect(within(table).getAllByRole('link', { name: 'Review update' })[0]).toHaveAttribute('href', '/dashboard/skills/follow?tab=dependents');
  expect(session.resolvedLock.links.every((link) => link.version === 1)).toBe(true);
});
