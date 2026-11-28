import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { RETIRED_TERMS } from '../utils/terms';
import ContextLinksPanel from './ContextLinksPanel';

// SKILL-TRACES §16 UXF-16 (one set of names in the editor and the read views; "Check for
// update" only where an update exists) and UXF-12 (rows open their primary target; every other
// action in the trailing ⋯; one ghost inline action), plus the extraColumn slot for commit 2.

const state = vi.hoisted(() => ({ upstream: {} }));
vi.mock('src/api/skills/context-skill-inventory', () => ({ useLinkedSkillUpstream: () => state.upstream }));
vi.mock('src/components/iconify', () => ({ default: () => null }));

const media = (phone) => (query) => ({
  matches: phone && /max-width/.test(query), media: query,
  addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, dispatchEvent() { return false; },
});
beforeEach(() => { state.upstream = {}; vi.stubGlobal('matchMedia', media(false)); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const digest = `sha256:${'c'.repeat(64)}`;
// follow: follows production, v2 available over the locked v1. current: follows production, up to date.
// pin: pinned to v1. failed: the upstream check failed.
const links = [
  { skillId: 'follow', selector: { label: 'production' } },
  { skillId: 'current', selector: { label: 'production' } },
  { skillId: 'pin', selector: { version: 1 } },
  { skillId: 'failed', selector: { label: 'production' } },
];
const lock = links.map((link) => ({ ...link, version: 1, versionId: `id-${link.skillId}`, digest, mount: `skills/${link.skillId}/` }));
const upstream = (retry = vi.fn()) => ({
  follow: { data: { name: 'refund-policy', labels: [{ name: 'production', version: 2, versionId: 'new' }] } },
  current: { data: { name: 'tone', labels: [{ name: 'production', version: 1, versionId: 'id-current' }] } },
  failed: { isError: true, refetch: retry },
});
const readSession = { contextSpec: { links }, resolvedLock: { links: lock } };
const draftSession = (overrides = {}) => ({ contextSpec: { links }, resolvedLock: { links: lock }, revision: 3, dirty: false, editSequence: 0, flush: async () => ({ revision: 3 }), mutateDraft: async (operation) => operation({ revision: 3 }), ...overrides });
const previewResult = { revision: 3, fingerprint: 'fp-1', candidateDigest: 'sha256:candidate', changes: [], checks: { summary: { errors: 0 } } };

function Location() {
  const location = useLocation();
  return <output aria-label="Location">{`${location.pathname}${location.search}`}</output>;
}
const showRead = (props = {}) => render(
  <MemoryRouter initialEntries={['/dashboard/skills/contexts/context?tab=links']}>
    <Routes><Route path="*" element={<><ContextLinksPanel scopeKey="scope" readOnly rowOpensSkill session={readSession} {...props} /><Location /></>} /></Routes>
  </MemoryRouter>,
);
const rowOf = (table, name) => within(table).getByText(name).closest('tr');
const pick = (combobox, option) => { fireEvent.mouseDown(screen.getByRole('combobox', { name: combobox })); fireEvent.click(screen.getByRole('option', { name: option })); };

describe('read view', () => {
  it('uses the read-view names and puts "Check for update" only on the row that has one, as a ghost button', () => {
    state.upstream = upstream();
    showRead();
    const table = screen.getByRole('table', { name: 'Linked skills' });
    expect(within(table).getAllByRole('columnheader').map((cell) => cell.textContent)).toEqual(['Skill', 'Update rule', 'Version in this context', 'Status', 'Actions']);
    expect(within(rowOf(table, 'refund-policy')).getByText('Follows production')).toBeVisible();
    expect(within(rowOf(table, 'pin')).getByText('Pinned to v1')).toBeVisible();
    const checks = within(table).getAllByRole('button', { name: 'Check for update' });
    expect(checks).toHaveLength(1);
    expect(rowOf(table, 'refund-policy')).toContainElement(checks[0]);
    expect(checks[0]).toHaveClass('MuiButton-text');
    expect(within(table).queryByRole('link', { name: 'Check update' })).not.toBeInTheDocument();
    expect(table.querySelectorAll('.MuiButton-outlined')).toHaveLength(0);
    for (const term of RETIRED_TERMS) expect(document.body.textContent).not.toContain(term);
  });

  it('opens the skill at its locked version from anywhere on the row; Check for update opens its Linked contexts', async () => {
    const user = userEvent.setup();
    state.upstream = upstream();
    showRead();
    const table = screen.getByRole('table', { name: 'Linked skills' });
    const row = rowOf(table, 'refund-policy');
    expect(row).toHaveAttribute('data-row-link', 'true');
    await user.click(within(row).getByText('Follows production'));
    expect(screen.getByLabelText('Location')).toHaveTextContent('/dashboard/skills/follow?version=1');
    cleanup();
    state.upstream = upstream();
    showRead();
    await user.click(screen.getByRole('button', { name: 'Check for update' }));
    expect(screen.getByLabelText('Location')).toHaveTextContent('/dashboard/skills/follow?tab=dependents');
  });

  it('keeps Open skill, the technical details and Retry check in the trailing ⋯', async () => {
    const user = userEvent.setup();
    const retry = vi.fn();
    state.upstream = upstream(retry);
    showRead();
    const table = screen.getByRole('table', { name: 'Linked skills' });
    await user.click(within(table).getByRole('button', { name: 'Actions for failed' }));
    expect(screen.getAllByRole('menuitem').map((item) => item.textContent)).toEqual(['Open skill', 'Show technical details', 'Retry check']);
    await user.click(screen.getByRole('menuitem', { name: 'Retry check' }));
    expect(retry).toHaveBeenCalledOnce();
    expect(screen.getByLabelText('Location')).toHaveTextContent('/dashboard/skills/contexts/context?tab=links');
    await user.click(within(table).getByRole('button', { name: 'Actions for refund-policy' }));
    await user.click(screen.getByRole('menuitem', { name: 'Show technical details' }));
    const details = screen.getByText('Technical details');
    expect(details.closest('td')).toHaveAttribute('colspan', '5');
    // The details open in place; nothing navigated.
    expect(screen.getByLabelText('Location')).toHaveTextContent('/dashboard/skills/contexts/context?tab=links');
  });

  it('renders the extra column before the row actions, and in the stacked card on a phone', () => {
    state.upstream = upstream();
    const cell = vi.fn(({ link }) => <span>{`loads of ${link.skillId}`}</span>);
    const view = showRead({ extraColumn: { header: 'Loaded (7 days)', cell } });
    const table = screen.getByRole('table', { name: 'Linked skills' });
    expect(within(table).getAllByRole('columnheader').map((item) => item.textContent)).toEqual(['Skill', 'Update rule', 'Version in this context', 'Status', 'Loaded (7 days)', 'Actions']);
    expect(within(rowOf(table, 'refund-policy')).getByText('loads of follow').closest('td').cellIndex).toBe(4);
    expect(cell).toHaveBeenCalledWith(expect.objectContaining({ name: 'refund-policy', readOnly: true, included: expect.objectContaining({ version: 1 }) }));
    view.unmount();
    vi.stubGlobal('matchMedia', media(true));
    showRead({ extraColumn: { header: 'Loaded (7 days)', cell } });
    const list = screen.getByRole('list', { name: 'Linked skills' });
    const card = within(list).getByText('refund-policy').closest('[role="listitem"]');
    expect(within(card).getByText('Loaded (7 days)')).toBeVisible();
    expect(within(card).getByText('loads of follow')).toBeVisible();
  });

  it('on a phone keeps the ⋯ and moves Check for update into it', async () => {
    const user = userEvent.setup();
    vi.stubGlobal('matchMedia', media(true));
    state.upstream = upstream();
    showRead();
    const list = screen.getByRole('list', { name: 'Linked skills' });
    expect(within(list).queryByRole('button', { name: 'Check for update' })).not.toBeInTheDocument();
    await user.click(within(list).getByRole('button', { name: 'Actions for refund-policy' }));
    await user.click(screen.getByRole('menuitem', { name: 'Check for update' }));
    expect(screen.getByLabelText('Location')).toHaveTextContent('/dashboard/skills/follow?tab=dependents');
  });
});

describe('locked draft editor (read-only, no rowOpensSkill)', () => {
  it('opens the row details on a row click and never leaves the editor', async () => {
    const user = userEvent.setup();
    state.upstream = upstream();
    render(
      <MemoryRouter initialEntries={['/dashboard/skills/contexts/context/edit']}>
        <Routes><Route path="*" element={<><ContextLinksPanel scopeKey="scope" readOnly session={readSession} /><Location /></>} /></Routes>
      </MemoryRouter>,
    );
    const table = screen.getByRole('table', { name: 'Linked skills' });
    await user.click(within(rowOf(table, 'tone')).getByText('Up to date'));
    expect(screen.getByText('Technical details')).toBeVisible();
    expect(screen.getByLabelText('Location')).toHaveTextContent('/dashboard/skills/contexts/context/edit');
    await user.click(within(table).getByRole('button', { name: 'Actions for tone' }));
    expect(screen.getAllByRole('menuitem').map((item) => item.textContent)).toEqual(['Hide technical details']);
    // No editing control appears in a locked editor.
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Add skill|Preview changes to linked skills|Remove/ })).not.toBeInTheDocument();
  });

  it('renders outside a router, without the actions that navigate', () => {
    state.upstream = upstream();
    render(<ContextLinksPanel scopeKey="scope" readOnly session={readSession} />);
    const table = screen.getByRole('table', { name: 'Linked skills' });
    expect(within(table).getByText('v2 available')).toBeVisible();
    expect(within(table).queryByRole('button', { name: 'Check for update' })).not.toBeInTheDocument();
  });
});

describe('draft editor', () => {
  it('names the add controls as the read views do, and works outside a router', () => {
    state.upstream = upstream();
    render(<ContextLinksPanel scopeKey="scope" session={draftSession()} availableSkills={[{ id: 'new', name: 'escalation', kind: 'skill', latestVersion: 3 }]} onPreview={vi.fn()} onApply={vi.fn()} />);
    expect(screen.getByRole('combobox', { name: 'Update rule' })).toHaveTextContent('Follows production');
    fireEvent.mouseDown(screen.getByRole('combobox', { name: 'Update rule' }));
    expect(screen.getAllByRole('option').map((option) => option.textContent)).toEqual(['Follows production', 'Follows another label', 'Pinned']);
    fireEvent.keyDown(screen.getByRole('listbox'), { key: 'Escape' });
    expect(screen.getByRole('button', { name: 'Add skill' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Changing update rules' })).toBeInTheDocument();
    for (const term of RETIRED_TERMS) expect(document.body.textContent).not.toContain(term);
  });

  it('asks "Check for update" only on the row with an update, with a per-skill name, and sends that tick to the preview', async () => {
    state.upstream = upstream();
    const onPreview = vi.fn(async () => previewResult);
    render(<ContextLinksPanel scopeKey="scope" session={draftSession()} onPreview={onPreview} onApply={vi.fn()} />);
    const table = screen.getByRole('table', { name: 'Linked skills' });
    const ticks = within(table).getAllByRole('checkbox');
    expect(ticks).toHaveLength(1);
    expect(ticks[0]).toHaveAccessibleName('Check for update: refund-policy');
    expect(rowOf(table, 'refund-policy')).toContainElement(ticks[0]);
    expect(within(rowOf(table, 'refund-policy')).getByText('Check for update')).toBeVisible();
    fireEvent.click(ticks[0]);
    fireEvent.click(screen.getByRole('button', { name: 'Preview changes to linked skills' }));
    await waitFor(() => expect(onPreview).toHaveBeenCalledOnce());
    expect(onPreview.mock.calls[0][0]).toEqual({ revision: 3, links, refreshSkillIds: ['follow'] });
  });

  it('never sends a tick whose row stopped offering an update, and asks for a new preview when the ticks change', async () => {
    state.upstream = upstream();
    const onPreview = vi.fn(async () => previewResult);
    const session = draftSession();
    const view = render(<ContextLinksPanel scopeKey="scope" session={session} onPreview={onPreview} onApply={vi.fn()} />);
    fireEvent.click(screen.getByRole('checkbox', { name: 'Check for update: refund-policy' }));
    fireEvent.click(screen.getByRole('button', { name: 'Preview changes to linked skills' }));
    await waitFor(() => expect(screen.getByRole('checkbox', { name: /Accept these skill versions/ })).toBeEnabled());
    // Upstream production is now the locked version: the row is up to date and loses its tick.
    state.upstream = { ...upstream(), follow: { data: { name: 'refund-policy', labels: [{ name: 'production', version: 1, versionId: 'id-follow' }] } } };
    view.rerender(<ContextLinksPanel scopeKey="scope" session={session} onPreview={onPreview} onApply={vi.fn()} />);
    expect(screen.queryByRole('checkbox', { name: /Check for update/ })).not.toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: /Accept these skill versions/ })).toBeDisabled();
    expect(screen.getByText('The draft changed. Preview again before applying.')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Preview changes to linked skills' }));
    await waitFor(() => expect(onPreview).toHaveBeenCalledTimes(2));
    expect(onPreview.mock.calls[1][0].refreshSkillIds).toEqual([]);
  });

  it('applies only after the explicit acceptance, with the previewed ticks and fingerprint', async () => {
    state.upstream = upstream();
    const onApply = vi.fn(async () => ({ revision: 4, contextSpec: { links } }));
    render(<ContextLinksPanel scopeKey="scope" session={draftSession()} onPreview={vi.fn(async () => previewResult)} onApply={onApply} />);
    fireEvent.click(screen.getByRole('checkbox', { name: 'Check for update: refund-policy' }));
    fireEvent.click(screen.getByRole('button', { name: 'Preview changes to linked skills' }));
    const accept = await screen.findByRole('checkbox', { name: /Accept these skill versions/ });
    await waitFor(() => expect(accept).toBeEnabled());
    expect(screen.getByRole('button', { name: 'Apply selected updates to draft' })).toBeDisabled();
    fireEvent.click(accept);
    fireEvent.click(screen.getByRole('button', { name: 'Apply selected updates to draft' }));
    await waitFor(() => expect(onApply).toHaveBeenCalledOnce());
    expect(onApply.mock.calls[0][0]).toEqual({ revision: 3, links, refreshSkillIds: ['follow'], expectedFingerprint: 'fp-1' });
  });

  it('sends the same selectors as before for each update rule', async () => {
    const available = ['one', 'two', 'three'].map((id) => ({ id, name: id, kind: 'skill', latestVersion: 7 }));
    const onPreview = vi.fn(async () => previewResult);
    render(<ContextLinksPanel scopeKey="scope" session={draftSession({ contextSpec: { links: [] }, resolvedLock: { links: [] } })} availableSkills={available} onPreview={onPreview} onApply={vi.fn()} />);
    pick('Add a published skill', 'one');
    pick('Update rule', 'Pinned');
    fireEvent.change(screen.getByRole('textbox', { name: 'Version' }), { target: { value: '7' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add skill' }));
    // The rule returns to its default after each add, so a pin never leaks into the next one.
    expect(screen.getByRole('combobox', { name: 'Update rule' })).toHaveTextContent('Follows production');
    expect(screen.queryByRole('textbox', { name: /Label|Version/ })).not.toBeInTheDocument();
    pick('Add a published skill', 'two');
    pick('Update rule', 'Follows another label');
    expect(screen.getByRole('button', { name: 'Add skill' })).toBeDisabled();
    fireEvent.change(screen.getByRole('textbox', { name: 'Label' }), { target: { value: 'staging' } });
    expect(screen.getByRole('combobox', { name: 'Update rule' })).toHaveTextContent('Follows staging');
    fireEvent.click(screen.getByRole('button', { name: 'Add skill' }));
    pick('Add a published skill', 'three');
    fireEvent.click(screen.getByRole('button', { name: 'Add skill' }));
    fireEvent.click(screen.getByRole('button', { name: 'Preview changes to linked skills' }));
    await waitFor(() => expect(onPreview).toHaveBeenCalledOnce());
    expect(onPreview.mock.calls[0][0].links).toEqual([
      { skillId: 'one', selector: { version: 7 } },
      { skillId: 'two', selector: { label: 'staging' } },
      { skillId: 'three', selector: { label: 'production' } },
    ]);
  });

  it('rejects an invalid label or version before anything is added', () => {
    render(<ContextLinksPanel scopeKey="scope" session={draftSession({ contextSpec: { links: [] }, resolvedLock: { links: [] } })} availableSkills={[{ id: 'one', name: 'one', kind: 'skill', latestVersion: 7 }]} onPreview={vi.fn()} onApply={vi.fn()} />);
    pick('Add a published skill', 'one');
    pick('Update rule', 'Follows another label');
    fireEvent.change(screen.getByRole('textbox', { name: 'Label' }), { target: { value: 'Not A Label' } });
    expect(screen.getByRole('button', { name: 'Add skill' })).toBeDisabled();
    pick('Update rule', 'Pinned');
    fireEvent.change(screen.getByRole('textbox', { name: 'Version' }), { target: { value: '0' } });
    expect(screen.getByRole('button', { name: 'Add skill' })).toBeDisabled();
  });

  it('opens the row details on a row click and keeps Remove from context in the ⋯', async () => {
    const user = userEvent.setup();
    state.upstream = upstream();
    render(<ContextLinksPanel scopeKey="scope" session={draftSession()} onPreview={vi.fn()} onApply={vi.fn()} />);
    const table = screen.getByRole('table', { name: 'Linked skills' });
    expect(within(table).queryByRole('button', { name: 'Remove from context' })).not.toBeInTheDocument();
    await user.click(within(rowOf(table, 'tone')).getByText('Up to date'));
    expect(screen.getByText('Technical details')).toBeVisible();
    await user.click(within(table).getByRole('button', { name: 'Actions for tone' }));
    expect(screen.getAllByRole('menuitem').map((item) => item.textContent)).toEqual(['Hide technical details', 'Remove from context']);
    await user.click(screen.getByRole('menuitem', { name: 'Remove from context' }));
    expect(within(table).queryByText('tone')).not.toBeInTheDocument();
  });
});
