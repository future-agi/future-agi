import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import MigrationBatchReview from './MigrationBatchReview';

const context = vi.hoisted(() => ({ prepare: vi.fn(), publish: vi.fn() }));
vi.mock('src/api/skills/j1-migration-context', async (original) => ({ ...(await original()),
  usePrepareMigrationContext: () => ({ mutateAsync: context.prepare, isPending: false }),
  usePublishMigrationContext: () => ({ mutateAsync: context.publish, isPending: false }) }));
vi.mock('../contexts/ContextPilotCredentials', () => ({ default: ({ snapshot }) => <p>{`Pilot credential for v${snapshot.version}`}</p> }));
import { classifyMigrationItem, retainPlan } from './migration-batch';

const digest = (c) => `sha256:${c.repeat(64)}`;
const item = (key, extra = {}) => ({ key, name: key, sourceOrigin: 'origin', sourceProject: 'project', sourceResource: key, sourceRevision: 'v1',
  sourceHubChanges: [], basisPayloadAvailable: false, expectedHeadVersion: null, expectedHeadDigest: null, fileCount: 1, totalBytes: 10, ...extra });
const plan = (items, id = 'plan') => ({ id, fingerprint: digest('a'), expiresAt: '2099-01-01T00:00:00Z', items });
const receipt = (key) => ({ resourceId: `resource-${key}`, draftRevision: 1, digest: digest('b'), decision: 'replace' });
afterEach(cleanup);

it('classifies only unambiguous items as defaults and keeps every exception explicit', () => {
  expect(classifyMigrationItem(item('new')).kind).toBe('default');
  expect(classifyMigrationItem(item('bad', { blocked: true, blockers: ['bin: file_not_text'] })).kind).toBe('excluded');
  expect(classifyMigrationItem(item('mine', { hasDraft: true, diverged: true })).kind).toBe('held');
  expect(classifyMigrationItem(item('none', { sourceHubChanges: undefined })).kind).toBe('held');
  expect(classifyMigrationItem(item('both', { diverged: true, destinationId: 'd' })).kind).toBe('choice');
});

it('requires the blocked-member acknowledgment and rights before one decision imports and publishes the set', async () => {
  const accept = vi.fn(async (input) => receipt(input.itemKey));
  const publish = vi.fn(async (input) => ({ resourceId: input.resourceId, version: 1, digest: input.digest }));
  const create = vi.fn();
  render(<MigrationBatchReview plans={[plan([item('one'), item('two'), item('bad', { blocked: true })])]} onAccept={accept} onPublish={publish} onCreateContext={create} />);
  const submit = screen.getByRole('button', { name: 'Import and publish 2 items' });
  fireEvent.click(screen.getByRole('checkbox', { name: /^I confirm my rights to copy all 2 items/ }));
  expect(submit).toBeDisabled();
  fireEvent.click(screen.getByRole('checkbox', { name: /^Leave out 1 skill/ }));
  expect(submit).toBeEnabled();
  fireEvent.click(submit);
  await screen.findByText('2 versions published without labels. Production was not changed.');
  expect(accept.mock.calls.map(([input]) => input.itemKey)).toEqual(['one', 'two']);
  expect(accept.mock.calls.every(([input]) => input.rightsConfirmed === true && input.mode === 'snapshot')).toBe(true);
  // Publication is bound to the exact imported revision and digest.
  expect(publish.mock.calls[0][0]).toEqual({ resourceId: 'resource-one', revision: 1, digest: digest('b'), acknowledgeWarnings: false });
  fireEvent.click(screen.getByRole('button', { name: 'Create agent context from these skills' }));
  expect(create).toHaveBeenCalledWith([{ skillId: 'resource-one', version: 1 }, { skillId: 'resource-two', version: 1 }]);
});

it('shows a reason instead of a zero-item import action when every item is held', () => {
  render(<MigrationBatchReview plans={[plan([item('mine', { hasDraft: true })])]} onAccept={vi.fn()} />);
  expect(screen.getByText('Resolve the held items, then preview again')).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Import and publish 0 items' })).not.toBeInTheDocument();
});

it('settles a lost response by replaying the exact body and operation key once', async () => {
  const accept = vi.fn().mockRejectedValueOnce(new Error('connection lost')).mockImplementation(async (input) => receipt(input.itemKey));
  render(<MigrationBatchReview plans={[plan([item('one'), item('two')])]} onAccept={accept} onPublish={async (input) => ({ version: 1, resourceId: input.resourceId })} />);
  fireEvent.click(screen.getByRole('checkbox', { name: /^I confirm my rights/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Import and publish 2 items' }));
  await screen.findByText('2 versions published without labels. Production was not changed.');
  expect(accept).toHaveBeenCalledTimes(3);
  expect(accept.mock.calls[1]).toEqual(accept.mock.calls[0]);
  expect(accept.mock.calls[2][1].idempotencyKey).not.toBe(accept.mock.calls[0][1].idempotencyKey);
  expect(screen.getAllByText(/lost response settled by exact replay/)).toHaveLength(1);
});

it('never replays a definite rejection and retries the remaining items with their original keys', async () => {
  const rejection = Object.assign(new Error('Accept the exact destination reviewed in this plan.'), { statusCode: 409 });
  const accept = vi.fn(async (input) => receipt(input.itemKey));
  accept.mockImplementationOnce(async (input) => receipt(input.itemKey)).mockRejectedValueOnce(rejection);
  render(<MigrationBatchReview plans={[plan([item('one'), item('two')])]} onAccept={accept} onPublish={async (input) => ({ version: 1, resourceId: input.resourceId })} />);
  fireEvent.click(screen.getByRole('checkbox', { name: /^I confirm my rights/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Import and publish 2 items' }));
  const retry = await screen.findByRole('button', { name: 'Retry 1 remaining with the same decisions' });
  expect(accept).toHaveBeenCalledTimes(2);
  fireEvent.click(retry);
  await screen.findByText('2 versions published without labels. Production was not changed.');
  expect(accept).toHaveBeenCalledTimes(3);
  expect(accept.mock.calls[2]).toEqual(accept.mock.calls[1]);
});

it('blocks the set until each diverged item has an explicit keep or replace decision and holds personal drafts out', async () => {
  const accept = vi.fn(async (input) => receipt(input.itemKey));
  const keep = vi.fn(async (input) => receipt(input.itemKey));
  const preserve = vi.fn();
  const diverged = item('both', { diverged: true, destinationId: 'd', expectedHeadVersion: 2, basisPayloadAvailable: true, sourceChanges: [], hubChanges: [] });
  render(<MigrationBatchReview plans={[plan([diverged, item('other', { destinationId: 'o', expectedHeadVersion: 1 }), item('mine', { hasDraft: true, destinationId: 'm' })])]} onAccept={accept} onKeep={keep} onPublish={async (input) => ({ version: 2, resourceId: input.resourceId })} onPreserveDraft={preserve} />);
  expect(screen.getByText('Your personal draft blocks replacement. Preserve it, then preview again.')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Preserve draft for mine' }));
  expect(preserve).toHaveBeenCalledWith(expect.objectContaining({ key: 'mine' }));
  fireEvent.click(screen.getByRole('checkbox', { name: /^I confirm my rights/ }));
  const submit = screen.getByRole('button', { name: 'Import and publish 2 items' });
  expect(submit).toBeDisabled();
  // The exception's differences are open for review and its decision is one click.
  expect(screen.getByRole('button', { name: 'Review file differences' })).toHaveAttribute('aria-expanded', 'true');
  expect(screen.getByRole('radio', { name: 'Keep Hub content' })).not.toBeChecked();
  expect(screen.getByRole('radio', { name: 'Replace with reviewed source' })).not.toBeChecked();
  expect(screen.getByRole('radiogroup', { name: 'Decision for both' })).toBeVisible();
  fireEvent.click(screen.getByRole('radio', { name: 'Keep Hub content' }));
  fireEvent.click(submit);
  await waitFor(() => expect(keep).toHaveBeenCalledOnce());
  expect(keep.mock.calls[0][0]).toMatchObject({ itemKey: 'both', expectedHeadVersion: 2, rightsConfirmed: true });
  expect(accept.mock.calls.map(([input]) => input.itemKey)).toEqual(['other']);
});

it('reviews distinct sources together and replaces an older preview of the same source identity', () => {
  const exchange = plan([item('one')], 'exchange');
  const folder = plan([item('two')], 'folder');
  const again = plan([item('one', { sourceRevision: 'v2' })], 'exchange-v2');
  expect(retainPlan(retainPlan([], exchange), folder).map((p) => p.id)).toEqual(['exchange', 'folder']);
  expect(retainPlan([exchange, folder], again).map((p) => p.id)).toEqual(['folder', 'exchange-v2']);
});

it('holds items whose publication warnings are unacknowledged and publishes them only after explicit acknowledgment', async () => {
  const warning = Object.assign(new Error('Review and acknowledge the warnings before publishing.'), { statusCode: 422, code: 'warnings_unacknowledged',
    detail: { summary: { errors: 0, warnings: 1 }, items: [{ severity: 'warning', code: 'description_style', message: 'Start the description with when to use it.' }] } });
  const accept = vi.fn(async (input) => receipt(input.itemKey));
  const publish = vi.fn(async (input) => {
    if (!input.acknowledgeWarnings) throw warning;
    return { resourceId: input.resourceId, version: 1 };
  });
  render(<MigrationBatchReview plans={[plan([item('one'), item('two')])]} onAccept={accept} onPublish={publish} />);
  fireEvent.click(screen.getByRole('checkbox', { name: /^I confirm my rights/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Import and publish 2 items' }));
  const publishReviewed = await screen.findByRole('button', { name: 'Publish 2 items with reviewed warnings' });
  expect(publishReviewed).toBeDisabled();
  expect(screen.getAllByText('Imported as a draft; its publication warnings need your acknowledgment')).toHaveLength(2);
  fireEvent.click(screen.getByRole('checkbox', { name: 'I reviewed the publication warnings for 2 items and want to publish them.' }));
  fireEvent.click(publishReviewed);
  await screen.findByText('2 versions published without labels. Production was not changed.');
  // Imports are never repeated; only publication is retried with acknowledgment.
  expect(accept).toHaveBeenCalledTimes(2);
  expect(publish.mock.calls.filter(([input]) => input.acknowledgeWarnings)).toHaveLength(2);
});

it('lets the owner inspect every defaulted item\'s exact source files inside the reviewed set', () => {
  const files = [{ path: 'SKILL.md', status: 'removed', fromText: 'Exact source café', toText: '' }];
  render(<MigrationBatchReview plans={[plan([item('one', { sourceHubChanges: files })])]} onAccept={vi.fn()} onPublish={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: 'Details', exact: true }));
  expect(screen.getByText('SKILL.md · added')).toBeVisible();
});

it('assembles the packet\'s proposed context from exactly the published versions, then publishes only after acceptance', async () => {
  const accept = vi.fn(async (input) => receipt(input.itemKey));
  const publish = vi.fn(async (input) => ({ resourceId: input.resourceId, version: 1 }));
  const lock = [{ mount: 'skills/one/', skillId: 'resource-one', version: 1, versionId: 'v-one', digest: digest('1') }, { mount: 'skills/two/', skillId: 'resource-two', version: 1, versionId: 'v-two', digest: digest('2') }];
  const prepared = { contextId: 'ctx', name: 'trial', links: [], preview: { candidateDigest: digest('c'), resolvedLock: { links: lock }, checks: { summary: { errors: 0, warnings: 0 }, items: [] } } };
  context.prepare.mockResolvedValueOnce(prepared);
  context.publish.mockResolvedValueOnce({ contextId: 'ctx', canIssueCredentials: true, receipt: { version: { id: 'ctx-v1', number: 1, digest: digest('c') } } });
  render(<MigrationBatchReview plans={[plan([item('one'), item('two')])]} onAccept={accept} onPublish={publish} onCreateContext={vi.fn()} contextProposal={{ name: 'trial', description: 'Private trial', agents: 'Root\n' }} />);
  fireEvent.click(screen.getByRole('checkbox', { name: /^I confirm my rights/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Import and publish 2 items' }));
  await screen.findByText('2 versions published without labels. Production was not changed.');
  // The proposal replaces the hand-off to another screen; fields stay editable.
  expect(screen.queryByRole('button', { name: 'Create agent context from these skills' })).not.toBeInTheDocument();
  expect(screen.getByLabelText('Immutable name')).toHaveValue('trial');
  expect(screen.getByLabelText('AGENTS.md')).toHaveValue('Root\n');
  fireEvent.click(screen.getByRole('button', { name: 'Preview agent context' }));
  await screen.findByRole('table', { name: 'Pinned links' });
  expect(context.prepare.mock.calls[0][0]).toMatchObject({ name: 'trial', description: 'Private trial', agents: 'Root\n',
    links: [{ skillId: 'resource-one', selector: { version: 1 } }, { skillId: 'resource-two', selector: { version: 1 } }] });
  const submit = screen.getByRole('button', { name: 'Publish agent context' });
  expect(submit).toBeDisabled();
  fireEvent.click(screen.getByRole('checkbox', { name: 'Accept these 2 exact pinned versions and this compiled candidate.' }));
  fireEvent.click(submit);
  await screen.findByText('Pilot credential for v1');
  expect(context.publish.mock.calls[0][0]).toMatchObject({ prepared, acknowledgeWarnings: false });
  // The same operation keys are kept for an exact retry.
  expect(context.publish.mock.calls[0][0].keys).toBe(context.prepare.mock.calls[0][0].keys);
});

it('keeps import consequence copy synchronized with the actual publication choice', () => {
  const onConsequenceChange = vi.fn();
  render(<MigrationBatchReview plans={[plan([item('one'), item('two')])]} onAccept={vi.fn()} onPublish={vi.fn()} onConsequenceChange={onConsequenceChange} />);
  expect(onConsequenceChange).toHaveBeenLastCalledWith("Imports 2 items as new published versions. Production labels don't change.");
  fireEvent.click(screen.getByRole('radio', { name: 'Drafts to review first' }));
  expect(onConsequenceChange).toHaveBeenLastCalledWith('Imports 2 items as unpublished drafts.');
  expect(screen.queryByText('Imports 2 items as unpublished drafts.')).not.toBeInTheDocument();
  expect(screen.getByRole('status')).toHaveTextContent('Confirm your rights to continue');
});

it('filters held items without removing required decisions or changing the submitted set', () => {
  render(<MigrationBatchReview plans={[plan([item('one'), item('both', { diverged: true, destinationId: 'd' }), item('mine', { hasDraft: true })])]} onAccept={vi.fn()} onPublish={vi.fn()} />);
  fireEvent.mouseDown(screen.getByRole('combobox', { name: 'Item status' }));
  fireEvent.click(screen.getByRole('option', { name: 'Held (1)', exact: true }));
  expect(screen.getByRole('table')).toHaveTextContent('mine');
  expect(screen.queryByRole('radiogroup', { name: 'Decision for both' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('checkbox', { name: /^I confirm my rights/ }));
  expect(screen.getByRole('button', { name: 'Import and publish 2 items' })).toBeDisabled();
});

it('requires fresh rights and exception acknowledgments when the reviewed source set changes', () => {
  const first = plan([item('one')]);
  const accept = vi.fn(); const publish = vi.fn();
  const { rerender } = render(<MigrationBatchReview plans={[first]} onAccept={accept} onPublish={publish} />);
  fireEvent.click(screen.getByRole('checkbox', { name: /^I confirm my rights/ }));
  expect(screen.getByRole('button', { name: 'Import and publish 1 item' })).toBeEnabled();
  rerender(<MigrationBatchReview plans={[first, plan([item('two')], 'second')]} onAccept={accept} onPublish={publish} />);
  expect(screen.getByRole('checkbox', { name: /^I confirm my rights/ })).not.toBeChecked();
  expect(screen.getByRole('button', { name: 'Import and publish 2 items' })).toBeDisabled();
});

it('shows a single next step rather than zero-item acknowledgments when everything is held', () => {
  render(<MigrationBatchReview plans={[plan([item('mine', { hasDraft: true })])]} onAccept={vi.fn()} onPublish={vi.fn()} />);
  expect(screen.getByText('1 item needs your decision before anything can be imported.')).toBeVisible();
  expect(screen.queryByRole('checkbox', { name: /^I confirm my rights/ })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Import and publish 0 items' })).not.toBeInTheDocument();
  expect(screen.getByText('Resolve the held items, then preview again')).toBeVisible();
  expect(screen.queryByRole('columnheader', { name: 'Result' })).not.toBeInTheDocument();
});

it('separates the default publication choice from explicit acknowledgments and can import drafts', async () => {
  const accept = vi.fn(async () => ({ resourceId: 'resource-one', revision: 1, digest: digest('b') }));
  const publish = vi.fn();
  render(<MigrationBatchReview plans={[plan([item('one')])]} onAccept={accept} onPublish={publish} />);
  expect(screen.getByRole('radiogroup', { name: 'Import as' })).toBeVisible();
  expect(screen.getByRole('radio', { name: 'Published v1, no labels' })).toBeChecked();
  expect(screen.getByRole('checkbox', { name: /I confirm my rights/ })).not.toBeChecked();
  fireEvent.click(screen.getByRole('radio', { name: 'Drafts to review first' }));
  expect(screen.getByRole('button', { name: 'Import 1 item' })).toBeDisabled();
  fireEvent.click(screen.getByRole('checkbox', { name: /I confirm my rights/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Import 1 item' }));
  await screen.findByText('Your skills are saved as drafts. Production labels did not change.');
  expect(accept).toHaveBeenCalledTimes(1);
  expect(publish).not.toHaveBeenCalled();
});
