import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter, useNavigate, useSearchParams } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import ContextDetailView from './ContextDetailView';
import ContextLibraryView from './ContextLibraryView';
import ContextLinksPanel from './ContextLinksPanel';

const state = vi.hoisted(() => ({ upstream: {} }));
vi.mock('src/api/skills/r3-spend', () => ({ useSkillsSpendPolicy: () => ({}) }));
vi.mock('src/api/skills/context-skill-inventory', () => ({ useLinkedSkillUpstream: () => state.upstream }));
// Presentation-only role signal; an author in the Skills scope (the server stays the authority).
vi.mock('../utils/access', () => ({ useSkillsAccess: () => ({ ready: true, sameScope: true, isViewer: false, canWrite: true, canAdmin: false }) }));
afterEach(cleanup);
beforeEach(() => { state.upstream = {}; localStorage.clear(); });
const version = { id: 'v1', number: 1, digest: `sha256:${'a'.repeat(64)}` };
const scopeKey = 'scope';
const detail = { scopeKey, query: { scopeKey, data: { context: { id: 'context', name: 'support-agent', latestVersion: 1, labels: [{ name: 'production', version: 1 }] } } }, versionQuery: { scopeKey, data: { version, descriptor: { description: 'Support instructions', links: [] }, files: [{ path: 'AGENTS.md', origin: { kind: 'owned' } }] } }, fileQuery: { scopeKey, data: { path: 'AGENTS.md', versionId: version.id, text: '# Instructions' } }, versions: [version], onEditViewed: vi.fn(), sections: { tests: <p>Test controls</p>, recovery: <p>Recovery controls</p> } };
function Navigation() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  return <><output aria-label="URL parameters">{params.toString()}</output><button type="button" onClick={() => navigate(-1)}>Back</button><button type="button" onClick={() => navigate(1)}>Forward</button></>;
}
it('makes the newer publication review primary without repeating tab-owned review actions', () => {
  const latest = { id: 'v2', number: 2, digest: `sha256:${'b'.repeat(64)}` };
  const props = { ...detail, query: { ...detail.query, data: { ...detail.query.data, context: { ...detail.query.data.context, latestVersion: 2 }, latest } }, versions: [latest, version], sections: { ...detail.sections, versions: <button type="button">Request release review</button> } };
  render(<MemoryRouter><ContextDetailView {...props} /></MemoryRouter>);
  const release = screen.getByRole('link', { name: 'Request release review for v2' });
  expect(release).toHaveClass('MuiButton-contained');
  expect(new URL(release.href).searchParams.get('candidate')).toBe('v2');
  expect(new URL(release.href).searchParams.get('digest')).toBe(latest.digest);
  expect(screen.getByRole('button', { name: 'Edit viewed v1' })).toHaveClass('MuiButton-outlined');
  fireEvent.click(screen.getByRole('tab', { name: 'Versions' }));
  expect(screen.queryByRole('link', { name: 'Request release review for v2' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Request release review', exact: true })).toBeVisible();
  fireEvent.click(screen.getByRole('tab', { name: 'Tests' }));
  expect(screen.queryByRole('link', { name: 'Request release review for v2' })).not.toBeInTheDocument();
  expect(screen.getByText('v2 published · Production is still v1.')).toBeVisible();
});
it('shows release unavailable rather than loading in the linked-skills error preview', () => {
  render(<MemoryRouter initialEntries={['/?tab=links']}><ContextDetailView {...detail} state="error" /></MemoryRouter>);
  expect(screen.getByRole('combobox', { name: 'Viewed release' })).toHaveTextContent('Release unavailable');
  expect(screen.queryByText('Loading release…')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Edit viewed version' })).toBeDisabled();
});
it('uses the shared missing-object pattern with a separate subtitle and way back', () => {
  render(<MemoryRouter><ContextDetailView {...detail} query={{ scopeKey, error: { status: 404 } }} /></MemoryRouter>);
  expect(screen.getByText('Agent context not found')).toBeVisible();
  expect(screen.getByText('It may have been archived or belong to another workspace.')).toBeVisible();
  expect(screen.getByRole('link', { name: 'Back to Agent contexts' })).toHaveClass('MuiButton-outlined');
});
it('owns section state in the URL, preserves version and test parameters, and restores back/forward', () => {
  render(<MemoryRouter initialEntries={['/?version=1&draft=1&reproduce=case&tab=recovery']}><Navigation /><ContextDetailView {...detail} /></MemoryRouter>);
  expect(screen.getByRole('tabpanel')).toHaveTextContent('Recovery controls');
  fireEvent.click(screen.getByRole('tab', { name: 'Tests' }));
  expect(screen.getByLabelText('URL parameters')).toHaveTextContent('version=1&draft=1&reproduce=case&tab=tests');
  expect(screen.getByRole('tabpanel')).toHaveTextContent('Test controls');
  fireEvent.click(screen.getByRole('button', { name: 'Back' }));
  expect(screen.getByRole('tab', { name: 'Rollback' })).toHaveAttribute('aria-selected', 'true');
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
  expect(screen.getByText('This page is no longer available')).toBeVisible();
  expect(screen.getByText('Your filters are kept.')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Go to first page' })).toHaveClass('MuiButton-outlined');
  // The out-of-range state is not a filtered empty: it never offers Clear filters.
  expect(screen.queryByRole('button', { name: 'Clear filters' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Go to first page' }));
  expect(filter).toHaveBeenLastCalledWith({ page: 1 });
  view.rerender(<ContextLibraryView {...library} query={{ scopeKey, error: new Error('offline'), refetch: vi.fn() }} />);
  expect(screen.getByText("Couldn't load agent contexts.")).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Create agent context' })).not.toBeInTheDocument();
});
it('matches list action order, search and tag controls and resets paging when page size changes', () => {
  const change = vi.fn();
  const context = { id: 'context', name: 'support-agent', tags: ['support'], latestVersion: 1, labels: [{ name: 'production', version: 1 }], updatedAt: '2026-11-14T00:00:00Z', updatedBy: { name: 'Sam Rivera' } };
  render(<ContextLibraryView {...library} query={{ scopeKey, data: { count: 1, page: 1, pageSize: 25, results: [context] } }} filters={{ page: 1, pageSize: 25 }} onFilterChange={change} />);
  expect(screen.getByRole('button', { name: 'Import AGENTS.md' }).compareDocumentPosition(screen.getByRole('button', { name: 'New agent context' })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(screen.getByRole('textbox', { name: 'Search agent contexts' })).toHaveAttribute('placeholder', 'Search agent contexts');
  expect(screen.getByRole('combobox', { name: 'Tag' })).toHaveAttribute('placeholder', 'Any tag');
  expect(screen.getByText('Sam Rivera')).toBeVisible();
  expect(screen.getAllByText('v1')).toHaveLength(2);
  fireEvent.change(screen.getByRole('combobox', { name: 'Rows per page' }), { target: { value: '50' } });
  expect(change).toHaveBeenLastCalledWith({ page: 1, pageSize: 50 });
});
it('shows each followed label against the locked version and never calls failed lookups current', () => {
  const retry = vi.fn();
  state.upstream = { follow: { data: { name: 'refund-policy', labels: [{ name: 'production', version: 2, versionId: 'new' }] } }, failed: { isError: true, refetch: retry } };
  const links = [{ skillId: 'follow', selector: { label: 'production' } }, { skillId: 'pin', selector: { version: 1 } }, { skillId: 'failed', selector: { label: 'production' } }];
  const session = { contextSpec: { links }, resolvedLock: { links: links.map((link) => ({ ...link, version: 1, versionId: 'old', digest: version.digest, mount: `skills/${link.skillId}/` })) } };
  render(<MemoryRouter><ContextLinksPanel scopeKey="scope" readOnly session={session} /></MemoryRouter>);
  const table = screen.getByRole('table', { name: 'Linked skills' });
  expect(within(table).getByText('v2 available')).toBeVisible();
  expect(within(table).getByText('Pinned on purpose')).toBeVisible();
  expect(within(table).getByText("Couldn't check")).toBeVisible();
  expect(within(table).queryByText('Up to date')).not.toBeInTheDocument();
  fireEvent.click(within(table).getByRole('button', { name: 'Retry' }));
  expect(retry).toHaveBeenCalledOnce();
  expect(within(table).getAllByRole('link', { name: 'Review update' })).toHaveLength(1);
  expect(within(table).getByRole('link', { name: 'Review update' })).toHaveAttribute('href', '/dashboard/skills/follow?tab=dependents');
  expect(session.resolvedLock.links.every((link) => link.version === 1)).toBe(true);
});

it('defaults markdown to preview and remembers the per-viewer source choice', () => {
  const view = render(<MemoryRouter><ContextDetailView {...detail} /></MemoryRouter>);
  expect(screen.getByRole('tab', { name: 'Preview', exact: true })).toHaveAttribute('aria-selected', 'true');
  fireEvent.click(screen.getByRole('tab', { name: 'Source', exact: true }));
  expect(screen.getByLabelText('AGENTS.md read-only source')).toBeVisible();
  view.unmount();
  render(<MemoryRouter><ContextDetailView {...detail} /></MemoryRouter>);
  expect(screen.getByRole('tab', { name: 'Source', exact: true })).toHaveAttribute('aria-selected', 'true');
});
it('does not expose stale release identity or editing after a failed release request', () => {
  render(<MemoryRouter initialEntries={['/?version=1']}><ContextDetailView {...detail} versionQuery={{ ...detail.versionQuery, error: new Error('offline') }} /></MemoryRouter>);
  expect(screen.getByText("Couldn't load v1")).toBeVisible();
  expect(screen.getByRole('button', { name: 'Edit viewed version' })).toBeDisabled();
  expect(screen.queryByText(/Digest/)).not.toBeInTheDocument();
});
it('offers draft work rather than editing an absent release', () => {
  render(<MemoryRouter><ContextDetailView {...detail} query={{ scopeKey, data: { context: { name: 'new-agent', latestVersion: null }, ownDraft: { revision: 1 } } }} versionQuery={{ scopeKey }} fileQuery={{ scopeKey }} onResumeDraft={vi.fn()} /></MemoryRouter>);
  expect(screen.getByText('No releases yet')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Resume your draft' })).toBeEnabled();
  expect(screen.queryByRole('button', { name: /Edit viewed/ })).not.toBeInTheDocument();
});
it('removes inaccessible library actions and filters, and offers a way back from a denied detail', () => {
  const view = render(<ContextLibraryView {...library} state="denied" />);
  expect(screen.queryByRole('button', { name: 'New agent context' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument();
  expect(screen.queryByRole('textbox', { name: /Search/ })).not.toBeInTheDocument();
  view.unmount();
  render(<MemoryRouter><ContextDetailView {...detail} state="denied" /></MemoryRouter>);
  expect(screen.getByRole('link', { name: 'Back to agent contexts' })).toHaveAttribute('href', '/dashboard/skills/contexts');
});

it('keeps release selection named and never prints the full digest in the identity row', () => {
  render(<MemoryRouter><ContextDetailView {...detail} /></MemoryRouter>);
  expect(screen.getByRole('combobox', { name: 'Viewed release' })).toBeVisible();
  // The shared identity row shows a short digest chip (UX-R7 §2.3); the full value stays in its tooltip and details.
  expect(screen.queryByText(version.digest)).not.toBeInTheDocument();
});
it('omits release-only tabs before publishing and offers review from the empty state', () => {
  const review = vi.fn();
  render(<MemoryRouter><ContextDetailView {...detail} query={{ scopeKey, data: { context: { name: 'new-agent', latestVersion: null }, ownDraft: { revision: 1 } } }} versionQuery={{ scopeKey }} fileQuery={{ scopeKey }} onReviewDraft={review} /></MemoryRouter>);
  expect(screen.queryByRole('tab', { name: 'Tests' })).not.toBeInTheDocument();
  const panel = screen.getByRole('tabpanel');
  fireEvent.click(within(panel).getByRole('button', { name: 'Review saved draft' }));
  expect(review).toHaveBeenCalledOnce();
});

const contextRow = (extra = {}) => ({ id: 'ctx', name: 'support-agent', description: 'Customer support.', latestVersion: 2, labels: [{ name: 'production', version: 1 }], hasDraft: false, updatedAt: '2026-11-10T00:00:00Z', updatedBy: { name: 'Sam Rivera' }, ...extra });
it('has no Draft column and shows a "Your draft" badge in the name cell only when a draft exists', () => {
  render(<ContextLibraryView {...library} query={{ scopeKey, data: { count: 2, page: 1, pageSize: 25, results: [contextRow(), contextRow({ id: 'two', name: 'returns-desk', hasDraft: true })] } }} filters={{ page: 1, pageSize: 25 }} onFilterChange={vi.fn()} />);
  expect(screen.getAllByRole('columnheader').map((cell) => cell.textContent)).toEqual(['Name', 'Production', 'Latest', 'Updated', '']);
  expect(screen.getAllByText('Your draft')).toHaveLength(1);
  const rows = screen.getAllByRole('row').slice(1);
  expect(within(rows[0]).queryByText('Your draft')).not.toBeInTheDocument();
  expect(within(rows[1]).getByText('Your draft').closest('td')).toBe(within(rows[1]).getByRole('button', { name: 'returns-desk' }).closest('td'));
  expect(within(rows[0]).queryByText('—')).not.toBeInTheDocument();
});
it('shows each context as a stacked card below sm with one readable facts line', () => {
  vi.stubGlobal('matchMedia', (query) => ({ matches: true, media: query, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }));
  try {
    render(<ContextLibraryView {...library} query={{ scopeKey, data: { count: 1, page: 1, pageSize: 25, results: [contextRow()] } }} filters={{ page: 1, pageSize: 25 }} onFilterChange={vi.fn()} />);
    const row = screen.getAllByRole('row')[1];
    expect(within(row).getByText('Production v1')).toBeVisible();
    expect(within(row).getByText('Latest v2')).toBeVisible();
    expect(within(row).queryByText('—')).not.toBeInTheDocument();
  } finally { vi.unstubAllGlobals(); }
});
it('opens one full-width detail row per linked skill with a named expand button, instead of a disclosure in every row', () => {
  const links = [{ skillId: 'one', selector: { label: 'production' } }, { skillId: 'two', selector: { version: 1 } }];
  state.upstream = { one: { data: { name: 'refund-policy', labels: [{ name: 'production', version: 2, versionId: 'new' }] } } };
  const session = { contextSpec: { links }, resolvedLock: { links: links.map((link) => ({ ...link, version: 1, versionId: `id-${link.skillId}-1111`, digest: `sha256:${'c'.repeat(64)}`, mount: `skills/${link.skillId}/` })) } };
  render(<MemoryRouter><ContextLinksPanel scopeKey="scope" readOnly session={session} /></MemoryRouter>);
  const table = screen.getByRole('table', { name: 'Linked skills' });
  expect(screen.queryByRole('button', { name: 'Technical details' })).not.toBeInTheDocument();
  expect(within(table).getAllByRole('columnheader').map((cell) => cell.textContent)).toEqual(['Skill', 'Update rule', 'Version in this context', 'Status', 'Action', 'Details']);
  // The update action sits in its own column; the status cell holds only the badge.
  const update = within(table).getByRole('link', { name: 'Review update' });
  expect(update.closest('td').cellIndex).toBe(4);
  expect(within(update.closest('tr')).getByText('v2 available').closest('td').cellIndex).toBe(3);
  // Digests never show in the row; the version opens them on demand.
  expect(screen.queryByText(/sha256|cccccc/)).not.toBeInTheDocument();
  const expand = within(table).getByRole('button', { name: 'Show details for refund-policy' });
  expect(expand).toHaveAttribute('aria-expanded', 'false');
  fireEvent.click(expand);
  expect(expand).toHaveAttribute('aria-expanded', 'true');
  expect(screen.getByText('Content fingerprint')).toBeVisible();
  expect(screen.getByText('Content fingerprint').closest('td')).toHaveAttribute('colspan', '6');
  fireEvent.click(expand);
  expect(screen.queryByText('Content fingerprint')).not.toBeInTheDocument();
});
it('says where editing starts when the viewed version is not the latest', () => {
  const latest = { id: 'v2', number: 2, digest: `sha256:${'b'.repeat(64)}` };
  const props = { ...detail, query: { ...detail.query, data: { ...detail.query.data, context: { ...detail.query.data.context, latestVersion: 2 }, latest } }, versions: [latest, version] };
  render(<MemoryRouter><ContextDetailView {...props} /></MemoryRouter>);
  expect(screen.getByRole('button', { name: 'Edit viewed v1' })).toBeVisible();
  expect(screen.getByText("Editing starts from v1, the version you're viewing. Latest is v2.")).toBeVisible();
});
it('does not add the origin line when the viewed version is the latest', () => {
  render(<MemoryRouter><ContextDetailView {...detail} /></MemoryRouter>);
  expect(screen.queryByText(/Editing starts from/)).not.toBeInTheDocument();
});
