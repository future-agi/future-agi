import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import ContextDependentsPanel from './ContextDependentsPanel';

// The skill page's "Linked contexts" tab (SKILL-TRACES §2.2, UX-18; URL value `dependents`) and its
// rows in the shared row-action pattern (§16 UXF-12). The follower selection and update flow are unchanged.

const state = vi.hoisted(() => ({ dependents: null, history: null }));
vi.mock('src/api/skills/testing', () => ({ useSkillV11Scope: () => ({ identity: 'org:ws:author' }) }));
vi.mock('src/api/skills', () => ({ useSkill: () => ({ data: { name: 'refund-policy', latestVersion: 8, labels: [{ name: 'production', version: 8 }] } }) }));
vi.mock('src/api/skills/context-dependents', () => ({ useContextDependents: (_skill, { view }) => (view === 'history' ? state.history : state.dependents) }));
vi.mock('src/api/skills/context-follower-update', () => ({ useFollowerUpdates: () => ({ prepare: vi.fn(), apply: vi.fn() }) }));
vi.mock('src/components/iconify', () => ({ default: () => null }));

const media = (phone) => (query) => ({
  matches: phone && /max-width/.test(query), media: query,
  addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, dispatchEvent() { return false; },
});
const row = (id, update_state, name = id) => ({ context_id: id, name, context_version: 1, labels: id === 'a' ? ['production'] : [], requested: update_state === 'pinned_as_intended' ? { version: 7 } : { label: 'production' }, locked_version: 7, update_state });
beforeEach(() => {
  vi.stubGlobal('matchMedia', media(false));
  state.history = { data: { count: 0, as_of: 'now', total_pages: 1, results: [] }, refetch: vi.fn() };
  state.dependents = { data: { count: 2, as_of: 'now', total_pages: 1, results: [row('a', 'update_available', 'support-agent'), row('c', 'pinned_as_intended', 'billing-agent')] }, refetch: vi.fn() };
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function Location() {
  const location = useLocation();
  return <output aria-label="Location">{`${location.pathname}${location.search}`}</output>;
}
const mount = () => render(
  <MemoryRouter initialEntries={['/dashboard/skills/child?tab=dependents']}>
    <Routes><Route path="*" element={<><ContextDependentsPanel skillId="child" /><Location /></>} /></Routes>
  </MemoryRouter>,
);
const table = () => screen.getByRole('table', { name: 'Published context dependents' });
const rowOf = (name) => within(table()).getByText(name).closest('tr');

it('is titled Linked contexts and says "link", never "use"', () => {
  mount();
  expect(screen.getByRole('heading', { name: 'Linked contexts' })).toBeVisible();
  expect(screen.getByText(/2 agent contexts link this skill/)).toBeVisible();
  expect(screen.queryByText(/Used by|use this skill|uses this skill/)).not.toBeInTheDocument();
  expect(within(rowOf('support-agent')).getByText('Follows production · v7')).toBeVisible();
  expect(within(rowOf('billing-agent')).getByText('Pinned to v7')).toBeVisible();
  // The review column is gone: "review" names only the second person's decision (UXF-14).
  expect(within(table()).queryByRole('columnheader', { name: 'Review' })).not.toBeInTheDocument();
});

it('says "link" in the empty state', () => {
  state.dependents = { data: { count: 0, as_of: 'now', total_pages: 1, results: [] }, refetch: vi.fn() };
  mount();
  expect(screen.getByText('No agent contexts link this skill yet')).toBeVisible();
});

it('opens the context release from anywhere on the row, but never from the selection boxes', async () => {
  const user = userEvent.setup();
  mount();
  const pinnedBox = within(rowOf('billing-agent')).getByRole('checkbox');
  expect(pinnedBox).toBeDisabled();
  await user.click(pinnedBox.closest('td'));
  await user.click(within(rowOf('support-agent')).getByRole('checkbox', { name: 'Select support-agent v1 for update' }));
  expect(screen.getByLabelText('Location')).toHaveTextContent('/dashboard/skills/child?tab=dependents');
  expect(within(rowOf('support-agent')).getByRole('checkbox', { name: 'Select support-agent v1 for update' })).not.toBeChecked();
  await user.click(within(rowOf('support-agent')).getByText('support-agent'));
  expect(screen.getByLabelText('Location')).toHaveTextContent('/dashboard/skills/contexts/a?version=1');
});

it('keeps Open context, and Change pin on a deliberate pin, in the trailing ⋯ with no outlined button', async () => {
  const user = userEvent.setup();
  mount();
  expect(table().querySelectorAll('.MuiButton-outlined')).toHaveLength(0);
  await user.click(within(rowOf('support-agent')).getByRole('button', { name: 'Actions for support-agent v1' }));
  expect(screen.getAllByRole('menuitem').map((item) => item.textContent)).toEqual(['Open context']);
  await user.keyboard('{Escape}');
  await user.click(within(rowOf('billing-agent')).getByRole('button', { name: 'Actions for billing-agent v1' }));
  await user.click(screen.getByRole('menuitem', { name: 'Change pin' }));
  expect(screen.getByLabelText('Location')).toHaveTextContent('/dashboard/skills/contexts/c?tab=links');
});

it('on a phone each row is a tappable card with a chevron and no button', async () => {
  const user = userEvent.setup();
  vi.stubGlobal('matchMedia', media(true));
  mount();
  const card = rowOf('support-agent');
  expect(within(card).queryByRole('button', { name: /Actions for/ })).not.toBeInTheDocument();
  expect(card.querySelector('[data-row-chevron="true"]')).not.toBeNull();
  await user.click(within(card).getByText('support-agent'));
  expect(screen.getByLabelText('Location')).toHaveTextContent('/dashboard/skills/contexts/a?version=1');
});
