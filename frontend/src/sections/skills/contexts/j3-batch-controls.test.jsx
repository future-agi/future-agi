import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import ContextDependentsPanel from './ContextDependentsPanel';
import ContextLinksPanel from './ContextLinksPanel';

const state = vi.hoisted(() => ({ dependents: null, prepare: vi.fn(), apply: vi.fn() }));
vi.mock('src/api/skills/testing', () => ({ useSkillV11Scope: () => ({ identity: 'org:ws:author' }) }));
vi.mock('src/api/skills/context-dependents', () => ({ useContextDependents: () => state.dependents }));
vi.mock('src/api/skills/context-follower-update', () => ({ useFollowerUpdates: () => ({ prepare: state.prepare, apply: state.apply }) }));
const row = (id, update_state, name = id) => ({ context_id: id, name, context_version: 1, labels: [], requested: update_state === 'pinned_as_intended' ? { version: 7 } : { label: 'production' }, locked_version: 7, update_state });
const plan = (id, fingerprint = `plan-${id}`) => ({ target: { contextId: id, name: id, version: 1 }, status: 'ready', links: [], plan: { revision: 1, fingerprint, candidateDigest: `sha256:${id}`, checks: { summary: { errors: 0 } }, changes: [{ skillId: 'child', mount: 'skills/policy/', change: 'updated', before: { version: 7 }, after: { version: 8 } }] } });
beforeEach(() => {
  vi.clearAllMocks();
  state.dependents = { data: { count: 3, as_of: 'now', total_pages: 1, results: [row('a', 'update_available'), row('b', 'update_available'), row('c', 'pinned_as_intended')] }, refetch: vi.fn() };
});
afterEach(cleanup);
const mount = () => render(<MemoryRouter><ContextDependentsPanel skillId="child" /></MemoryRouter>);

it('preselects followers with an update, shows the deliberate pin as a disabled exception, and previews them together', async () => {
  state.prepare.mockResolvedValue([plan('a'), plan('b')]);
  mount();
  expect(screen.getByRole('checkbox', { name: 'Select a v1 for update' })).toBeChecked();
  expect(screen.getByRole('checkbox', { name: 'Select b v1 for update' })).toBeChecked();
  expect(screen.getByRole('checkbox', { name: 'Select c v1 for update' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Preview updates for 2 selected contexts' }));
  expect(await screen.findAllByText('skills/policy/ · updated · v7 → v8')).toHaveLength(2);
  expect(state.prepare).toHaveBeenCalledWith([{ contextId: 'a', name: 'a', version: 1 }, { contextId: 'b', name: 'b', version: 1 }]);
});

it('requires one explicit acknowledgement, applies only previewed plans, and renews only the rejected context', async () => {
  state.prepare.mockResolvedValueOnce([plan('a'), plan('b')]).mockResolvedValueOnce([plan('b', 'plan-b-2')]);
  state.apply.mockResolvedValueOnce([{ target: { contextId: 'a', name: 'a', version: 1 }, status: 'applied', revision: 2, digest: 'd' }, { target: { contextId: 'b', name: 'b', version: 1 }, status: 'error', code: 'stale_plan', httpStatus: 409 }]);
  mount();
  fireEvent.click(screen.getByRole('button', { name: 'Preview updates for 2 selected contexts' }));
  const apply = await screen.findByRole('button', { name: 'Apply selected updates to 2 context drafts' });
  expect(apply).toBeDisabled();
  fireEvent.click(screen.getByRole('checkbox', { name: 'Accept these exact resolved versions and selector policies for all 2 contexts.' }));
  fireEvent.click(apply);
  await screen.findByRole('link', { name: 'Open draft for a' });
  expect(state.apply.mock.calls[0][0].map((item) => item.plan.fingerprint)).toEqual(['plan-a', 'plan-b']);
  expect(screen.getByText(/upstream skill changed after this plan was previewed/)).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Preview again for 1 context that did not update' }));
  await screen.findByRole('button', { name: 'Apply selected updates to 1 context draft' });
  expect(state.prepare.mock.calls[1][0]).toEqual([{ contextId: 'b', name: 'b', version: 1 }]);
  expect(screen.getByRole('link', { name: 'Open draft for a' })).toBeVisible();
  expect(screen.getByRole('checkbox', { name: /Accept these exact resolved versions/ })).not.toBeChecked();
});

it('shows a different-origin draft as an exception and never selects an unavailable pin', async () => {
  state.prepare.mockResolvedValue([plan('a'), { target: { contextId: 'b', name: 'b', version: 1 }, status: 'error', code: 'different_origin' }]);
  mount();
  fireEvent.click(screen.getByRole('button', { name: 'Preview updates for 2 selected contexts' }));
  await screen.findByText(/already has a draft started from another release/);
  expect(screen.getByRole('button', { name: 'Apply selected updates to 1 context draft' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Preview again for 1 context that did not update' })).toBeVisible();
});

const available = [{ id: 'one', name: 'one', kind: 'skill', latestVersion: 7 }, { id: 'two', name: 'two', kind: 'skill', latestVersion: 7 }, { id: 'three', name: 'three', kind: 'skill', latestVersion: 7 }];
const session = () => ({ contextSpec: { links: [] }, resolvedLock: { links: [] }, revision: 3, dirty: false, editSequence: 0, flush: async () => ({ revision: 3 }), mutateDraft: async (operation) => operation({ revision: 3 }) });
it('adds every remaining published skill in one reviewed set, keeping a deliberate pin as its own exception', async () => {
  const preview = vi.fn(async () => ({ revision: 3, fingerprint: 'fp', candidateDigest: 'sha256:x', changes: [], checks: { summary: { errors: 0 } } }));
  render(<ContextLinksPanel scopeKey="scope" session={session()} availableSkills={available} onPreview={preview} onApply={vi.fn()} />);
  fireEvent.mouseDown(screen.getByRole('combobox', { name: 'Published leaf skill' })); fireEvent.click(screen.getByRole('option', { name: 'one' }));
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

it('keeps the recorded review attestation visible at the publish step instead of asking for it again', async () => {
  const { default: ContextReleaseReview } = await import('./ContextReleaseReview');
  const draftReview = { subjectId: 'ctx', revision: 3, candidateDigest: 'sha256:c', baseVersion: 1, expectedHeadVersion: 1, headDigest: 'sha256:h', reviewFingerprint: 'fp', headAcknowledged: true, complete: true, current: true, changes: [], headChanges: [], acceptedLock: { links: [] }, capabilities: { beforeDigest: 'sha256:b', afterDigest: 'sha256:c', changes: [] }, checks: { items: [], summary: { errors: 0, warnings: 1 } } };
  const onPublish = vi.fn(async () => ({}));
  render(<MemoryRouter><ContextReleaseReview scopeKey="scope" state="ready" name="parent" draftReview={draftReview} canPublish onPublish={onPublish} /></MemoryRouter>);
  expect(screen.getByRole('checkbox', { name: /I reviewed the saved candidate/ })).toBeChecked();
  expect(screen.getByRole('checkbox', { name: /I acknowledge the static warnings/ })).toBeChecked();
  fireEvent.click(screen.getByRole('checkbox', { name: /I reviewed the saved candidate/ }));
  expect(screen.getByRole('button', { name: 'Publish version' })).toBeDisabled();
  fireEvent.click(screen.getByRole('checkbox', { name: /I reviewed the saved candidate/ }));
  fireEvent.click(screen.getByRole('checkbox', { name: /I acknowledge the static warnings/ }));
  expect(screen.getByRole('button', { name: 'Publish version' })).toBeDisabled();
  fireEvent.click(screen.getByRole('checkbox', { name: /I acknowledge the static warnings/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Publish version' }));
  await waitFor(() => expect(onPublish).toHaveBeenCalledOnce());
  expect(onPublish.mock.calls[0][0]).toMatchObject({ revision: 3, reviewFingerprint: 'fp', acknowledgeWarnings: true });
});
