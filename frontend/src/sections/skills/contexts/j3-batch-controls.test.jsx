import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import ContextDependentsPanel from './ContextDependentsPanel';
import ContextLinksPanel from './ContextLinksPanel';
import ContextFollowerUpdate from './ContextFollowerUpdate';

const state = vi.hoisted(() => ({ dependents: null, history: null, prepare: vi.fn(), apply: vi.fn() }));
vi.mock('src/api/skills/testing', () => ({ useSkillV11Scope: () => ({ identity: 'org:ws:author' }) }));
vi.mock('src/api/skills', () => ({ useSkill: () => ({ data: { latestVersion: 8, labels: [{ name: 'production', version: 8 }] } }) }));
vi.mock('src/api/skills/context-skill-inventory', () => ({ useLinkedSkillUpstream: () => ({}) }));
vi.mock('src/api/skills/context-dependents', () => ({ useContextDependents: (_skill, { view }) => (view === 'history' ? state.history : state.dependents) }));
vi.mock('src/api/skills/context-follower-update', () => ({ useFollowerUpdates: () => ({ prepare: state.prepare, apply: state.apply }) }));
const row = (id, update_state, name = id) => ({ context_id: id, name, context_version: 1, labels: [], requested: update_state === 'pinned_as_intended' ? { version: 7 } : { label: 'production' }, locked_version: 7, update_state });
const plan = (id, fingerprint = `plan-${id}`) => ({ target: { contextId: id, name: id, version: 1 }, status: 'ready', links: [], plan: { revision: 1, fingerprint, candidateDigest: `sha256:${id}`, checks: { summary: { errors: 0 } }, changes: [{ skillId: 'child', mount: 'skills/policy/', change: 'updated', before: { version: 7 }, after: { version: 8 } }] } });
beforeEach(() => {
  vi.clearAllMocks();
  state.history = { data: { count: 1, as_of: 'now', total_pages: 1, results: [{ ...row('old', 'current', 'retired'), context_version: 0 }] }, refetch: vi.fn() };
  state.dependents = { data: { count: 3, as_of: 'now', total_pages: 1, results: [row('a', 'update_available'), row('b', 'update_available'), row('c', 'pinned_as_intended')] }, refetch: vi.fn() };
});
afterEach(cleanup);
const mount = () => render(<MemoryRouter><ContextDependentsPanel skillId="child" /></MemoryRouter>);

it('preselects followers with an update, shows the deliberate pin as a disabled exception, and previews them together', async () => {
  state.prepare.mockResolvedValue([plan('a'), plan('b')]);
  mount();
  expect(screen.getByRole('checkbox', { name: 'Select a v1 for update' })).toBeChecked();
  expect(screen.getByRole('checkbox', { name: 'Select b v1 for update' })).toBeChecked();
  const pin = screen.getByRole('checkbox', { name: 'Select c v1 for update' });
  expect(pin).toBeDisabled();
  expect(pin.closest('[title]')).toHaveAttribute('title', 'Pinned to v7 on purpose. Change the pin in the context to update.');
  expect(screen.getByText('Preparing starts a draft for each selected context. Production doesn\'t change.')).toBeVisible();
  expect(screen.getByRole('heading', { name: 'Used by' })).toBeVisible();
  expect(screen.getByRole('columnheader', { name: 'Locked at' })).toBeVisible();
  expect(screen.queryByText(/Locked skill version/)).not.toBeInTheDocument();
  expect(screen.queryByText(/not proof of delivery/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Prepare draft updates for 2 contexts' }));
  expect(await screen.findByText('a: policy v7 → v8 · Production stays unchanged')).toBeVisible();
  expect(screen.getByText('b: policy v7 → v8 · Production stays unchanged')).toBeVisible();
  // Before the first Prepare the copy is future tense; once cards exist it is gone.
  expect(screen.queryByText(/Preparing starts a draft/)).not.toBeInTheDocument();
  expect(screen.queryByText(/will be updated|Draft started|Draft origin/)).not.toBeInTheDocument();
  expect(state.prepare).toHaveBeenCalledWith([{ contextId: 'a', name: 'a', version: 1 }, { contextId: 'b', name: 'b', version: 1 }]);
});

it('leads each prepared card with the change and keeps production visibly unchanged', async () => {
  state.prepare.mockResolvedValue([{ ...plan('support-agent'), reusedDraft: false, target: { contextId: 'support-agent', name: 'support-agent', version: 1, productionVersion: 1 } }]);
  render(<MemoryRouter><ContextFollowerUpdate skillId="child" targets={[{ contextId: 'support-agent', name: 'support-agent', version: 1, productionVersion: 1 }]} /></MemoryRouter>);
  fireEvent.click(screen.getByRole('button', { name: 'Prepare draft updates for 1 context' }));
  expect(await screen.findByText('support-agent: policy v7 → v8 · Production stays v1')).toBeVisible();
  expect(screen.queryByText(/Draft started/)).not.toBeInTheDocument();
  expect(screen.queryByText(/One context draft will be updated/)).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Apply selected updates to 1 context draft' })).toBeDisabled();
  expect(state.apply).not.toHaveBeenCalled();
});
it('requires one explicit acknowledgement, applies only previewed plans, and renews only the rejected context', async () => {
  state.prepare.mockResolvedValueOnce([plan('a'), plan('b')]).mockResolvedValueOnce([plan('b', 'plan-b-2')]);
  state.apply.mockResolvedValueOnce([{ target: { contextId: 'a', name: 'a', version: 1 }, status: 'applied', revision: 2, digest: 'd' }, { target: { contextId: 'b', name: 'b', version: 1 }, status: 'error', code: 'stale_plan', httpStatus: 409 }]);
  mount();
  fireEvent.click(screen.getByRole('button', { name: 'Prepare draft updates for 2 contexts' }));
  const apply = await screen.findByRole('button', { name: 'Apply selected updates to 2 context drafts' });
  expect(apply).toBeDisabled();
  fireEvent.click(screen.getByRole('checkbox', { name: 'Accept these linked-skill versions and update rules for 2 contexts.' }));
  fireEvent.click(apply);
  await screen.findByRole('link', { name: 'Review a draft' });
  expect(state.apply.mock.calls[0][0].map((item) => item.plan.fingerprint)).toEqual(['plan-a', 'plan-b']);
  expect(screen.getByText(/upstream skill changed after this plan was previewed/)).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Preview again for 1 context that did not update' }));
  await screen.findByRole('button', { name: 'Apply selected updates to 1 context draft' });
  expect(state.prepare.mock.calls[1][0]).toEqual([{ contextId: 'b', name: 'b', version: 1 }]);
  expect(screen.getByRole('link', { name: 'Review a draft' })).toBeVisible();
  expect(screen.getByRole('checkbox', { name: /Accept these linked-skill versions/ })).not.toBeChecked();
});

it('shows the pinned row with a disabled checkbox, a lock mark and Production as a name', () => {
  state.dependents.data.results = [{ ...row('a', 'update_available'), labels: ['production'] }, row('c', 'pinned_as_intended')];
  mount();
  const pin = screen.getByRole('checkbox', { name: 'Select c v1 for update' });
  expect(pin).toBeDisabled();
  const rowOf = (name) => screen.getByRole('checkbox', { name }).closest('tr');
  expect(rowOf('Select c v1 for update').querySelector('.component-iconify')).not.toBeNull();
  expect(rowOf('Select a v1 for update').querySelector('.component-iconify')).toBeNull();
  expect(within(rowOf('Select a v1 for update')).getByText('Production')).toBeVisible();
  expect(within(rowOf('Select c v1 for update')).getByText('Pinned on purpose')).toBeVisible();
  expect(within(rowOf('Select c v1 for update')).getByText('v7')).toBeVisible();
});

it('keeps Prepare off after every selected context is updated, and on again once the selection changes', async () => {
  state.prepare.mockResolvedValue([plan('a'), plan('b')]);
  state.apply.mockResolvedValue([{ target: { contextId: 'a', name: 'a', version: 1 }, status: 'applied', revision: 2 }, { target: { contextId: 'b', name: 'b', version: 1 }, status: 'applied', revision: 2 }]);
  mount();
  fireEvent.click(screen.getByRole('button', { name: 'Prepare draft updates for 2 contexts' }));
  fireEvent.click(await screen.findByRole('checkbox', { name: /Accept these linked-skill versions/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Apply selected updates to 2 context drafts' }));
  await screen.findByText('Draft updated for 2 contexts. Production unchanged.');
  const prepare = screen.getByRole('button', { name: 'Prepare draft updates for 2 contexts' });
  expect(prepare).toBeDisabled();
  expect(prepare).toHaveAccessibleDescription('Already prepared for this selection.');
  fireEvent.click(screen.getByRole('checkbox', { name: 'Select b v1 for update' }));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Prepare draft updates for 1 context' })).toBeEnabled());
});

it('keeps Prepare available after a partial apply so the rejected context can be prepared again', async () => {
  state.prepare.mockResolvedValue([plan('a'), plan('b')]);
  state.apply.mockResolvedValue([{ target: { contextId: 'a', name: 'a', version: 1 }, status: 'applied', revision: 2 }, { target: { contextId: 'b', name: 'b', version: 1 }, status: 'error', code: 'stale_plan', httpStatus: 409 }]);
  mount();
  fireEvent.click(screen.getByRole('button', { name: 'Prepare draft updates for 2 contexts' }));
  fireEvent.click(await screen.findByRole('checkbox', { name: /Accept these linked-skill versions/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Apply selected updates to 2 context drafts' }));
  await screen.findByText(/upstream skill changed after this plan was previewed/);
  expect(screen.getByRole('button', { name: 'Prepare draft updates for 2 contexts' })).toBeEnabled();
});

it('shows a different-origin draft as an exception and never selects an unavailable pin', async () => {
  state.prepare.mockResolvedValue([plan('a'), { target: { contextId: 'b', name: 'b', version: 1 }, status: 'error', code: 'different_origin' }]);
  mount();
  fireEvent.click(screen.getByRole('button', { name: 'Prepare draft updates for 2 contexts' }));
  await screen.findByText(/already has a draft started from another release/);
  expect(screen.getByRole('button', { name: 'Apply selected updates to 1 context draft' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Preview again for 1 context that did not update' })).toBeVisible();
});

it('keeps superseded references available in a disclosure without duplicating the live table', () => {
  mount();
  expect(screen.queryByRole('combobox', { name: /Reference scope/ })).not.toBeInTheDocument();
  expect(within(screen.getByRole('table', { name: 'Published context dependents' })).getByText('a · v1')).toBeVisible();
  expect(screen.queryByRole('table', { name: 'Historical context references' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Show superseded releases' }));
  const history = screen.getByRole('table', { name: 'Historical context references' });
  expect(within(history).getByText('retired · v0')).toBeVisible();
  expect(within(history).queryByRole('checkbox')).not.toBeInTheDocument();
});

it('invalidates the prepared set and acknowledgement when selection changes, including changed then restored', async () => {
  state.prepare.mockResolvedValue([plan('a'), plan('b')]);
  mount();
  fireEvent.click(screen.getByRole('button', { name: 'Prepare draft updates for 2 contexts' }));
  fireEvent.click(await screen.findByRole('checkbox', { name: /Accept these linked-skill versions/ }));
  fireEvent.click(screen.getByRole('checkbox', { name: 'Select b v1 for update' }));
  expect(screen.getByText('Selection changed — prepare again.')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Apply selected updates', exact: true })).toBeDisabled();
  fireEvent.click(screen.getByRole('checkbox', { name: 'Select b v1 for update' }));
  expect(screen.getByRole('button', { name: 'Apply selected updates', exact: true })).toBeDisabled();
  expect(state.apply).not.toHaveBeenCalled();
});

it('discards an in-flight preparation for an earlier selection', async () => {
  let resolve;
  state.prepare.mockReturnValue(new Promise((done) => { resolve = done; }));
  mount();
  fireEvent.click(screen.getByRole('button', { name: 'Prepare draft updates for 2 contexts' }));
  fireEvent.click(screen.getByRole('checkbox', { name: 'Select b v1 for update' }));
  resolve([plan('a'), plan('b')]);
  await waitFor(() => expect(screen.getByRole('button', { name: 'Prepare draft updates for 1 context' })).toBeEnabled());
  expect(screen.queryByRole('checkbox', { name: /Accept these linked-skill versions/ })).not.toBeInTheDocument();
});

it('invalidates reviewed plans when the source release changes', async () => {
  state.prepare.mockResolvedValue([plan('a')]);
  const targets = [{ contextId: 'a', name: 'a', version: 1 }];
  const view = render(<MemoryRouter><ContextFollowerUpdate skillId="child" targets={targets} sourceRelease="v8" /></MemoryRouter>);
  fireEvent.click(screen.getByRole('button', { name: 'Prepare draft updates for 1 context' }));
  fireEvent.click(await screen.findByRole('checkbox', { name: /Accept these linked-skill versions/ }));
  view.rerender(<MemoryRouter><ContextFollowerUpdate skillId="child" targets={targets} sourceRelease="v9" /></MemoryRouter>);
  expect(screen.getByText('Selection changed — prepare again.')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Apply selected updates', exact: true })).toBeDisabled();
});

const available = [{ id: 'one', name: 'one', kind: 'skill', latestVersion: 7 }, { id: 'two', name: 'two', kind: 'skill', latestVersion: 7 }, { id: 'three', name: 'three', kind: 'skill', latestVersion: 7 }];
const session = () => ({ contextSpec: { links: [] }, resolvedLock: { links: [] }, revision: 3, dirty: false, editSequence: 0, flush: async () => ({ revision: 3 }), mutateDraft: async (operation) => operation({ revision: 3 }) });
it('adds every remaining published skill in one reviewed set, keeping a deliberate pin as its own exception', async () => {
  const preview = vi.fn(async () => ({ revision: 3, fingerprint: 'fp', candidateDigest: 'sha256:x', changes: [], checks: { summary: { errors: 0 } } }));
  render(<ContextLinksPanel scopeKey="scope" session={session()} availableSkills={available} onPreview={preview} onApply={vi.fn()} />);
  fireEvent.mouseDown(screen.getByRole('combobox', { name: 'Add a published skill' })); fireEvent.click(screen.getByRole('option', { name: 'one' }));
  fireEvent.mouseDown(screen.getByRole('combobox', { name: /^Update policy/ })); fireEvent.click(screen.getByRole('option', { name: 'Pin version' }));
  fireEvent.change(screen.getByRole('textbox', { name: 'Version' }), { target: { value: '7' } });
  fireEvent.click(screen.getByRole('button', { name: 'Add to proposed links' }));
  // The pin must not leak into the batch: the policy returns to its default.
  expect(screen.getByRole('textbox', { name: 'Label' })).toHaveValue('production');
  fireEvent.click(screen.getByRole('button', { name: 'Add all 2 remaining published skills' }));
  fireEvent.click(screen.getByRole('button', { name: 'Preview dependency changes' }));
  await waitFor(() => expect(preview).toHaveBeenCalledOnce());
  expect(preview.mock.calls[0][0].links).toEqual([{ skillId: 'one', selector: { version: 7 } }, { skillId: 'two', selector: { label: 'production' } }, { skillId: 'three', selector: { label: 'production' } }]);
});

it('clears a prepared selection silently when no context remains and never restores acceptance', async () => {
  state.prepare.mockResolvedValue([plan('a')]);
  const targets = [{ contextId: 'a', name: 'a', version: 1 }];
  const view = render(<MemoryRouter><ContextFollowerUpdate skillId="child" targets={targets} /></MemoryRouter>);
  fireEvent.click(screen.getByRole('button', { name: 'Prepare draft updates for 1 context' }));
  fireEvent.click(await screen.findByRole('checkbox', { name: /Accept these linked-skill versions/ }));
  view.rerender(<MemoryRouter><ContextFollowerUpdate skillId="child" targets={[]} /></MemoryRouter>);
  expect(screen.getByText('Select at least one context to prepare updates.')).toBeVisible();
  expect(screen.queryByText('Selection changed — prepare again.')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Prepare draft updates', exact: true })).toBeDisabled();
  view.rerender(<MemoryRouter><ContextFollowerUpdate skillId="child" targets={targets} /></MemoryRouter>);
  expect(screen.queryByRole('button', { name: /Apply selected updates/ })).not.toBeInTheDocument();
  expect(state.apply).not.toHaveBeenCalled();
});
it('does not repeat historical rows that are identical to active references', () => {
  state.history = state.dependents;
  mount();
  expect(screen.queryByRole('button', { name: 'Show superseded releases' })).not.toBeInTheDocument();
});

it('prepares one explicit source per context when production and latest are both referenced', async () => {
  state.dependents.data.results = [{ ...row('a', 'update_available'), labels: ['production'] }, { ...row('a', 'update_available'), context_version: 2 }, row('c', 'pinned_as_intended')];
  state.prepare.mockResolvedValue([plan('a')]);
  mount();
  expect(screen.getByRole('checkbox', { name: 'Select a v2 for update' })).toBeDisabled();
  expect(screen.getByRole('checkbox', { name: 'Select a v1 for update' })).toBeChecked();
  fireEvent.click(screen.getByRole('button', { name: 'Prepare draft updates for 1 context' }));
  await waitFor(() => expect(state.prepare).toHaveBeenCalledWith([{ contextId: 'a', name: 'a', version: 1, productionVersion: 1 }]));
});
it('shows confirmed draft status and a review action without exposing revision numbers', async () => {
  state.prepare.mockResolvedValue([plan('a'), plan('b')]);
  state.apply.mockResolvedValue([{ target: { contextId: 'a', name: 'a', version: 1 }, status: 'applied', revision: 42, lock: { links: [{ skillId: 'child', version: 8 }] } }]);
  mount();
  fireEvent.click(screen.getByRole('button', { name: 'Prepare draft updates for 2 contexts' }));
  fireEvent.click(await screen.findByRole('checkbox', { name: 'Accept these linked-skill versions and update rules for 2 contexts.' }));
  fireEvent.click(screen.getByRole('button', { name: 'Apply selected updates to 2 context drafts' }));
  expect(await screen.findByRole('link', { name: 'Review a draft' })).toHaveAttribute('href', '/dashboard/skills/contexts/a/review');
  expect(screen.getByText('Draft ready')).toBeVisible();
  expect(screen.queryByText(/revision 42/)).not.toBeInTheDocument();
});
