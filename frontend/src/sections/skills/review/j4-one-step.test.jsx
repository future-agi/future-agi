import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import PropTypes from 'prop-types';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { ContextReviewController, RequestReviewController, ReviewInboxController } from './SkillsReviewControllers';
import ReleaseRequestComposer from './ReleaseRequestComposer';
import ReleaseRequestPanel from './ReleaseRequestPanel';
import ReleaseInbox from './ReleaseInbox';
import { useExecuteReleaseRequest, useReleaseRequest, useRenewalPreview, useReviewInbox } from 'src/api/skills/review-production';
import { useSkillV11Query } from 'src/api/skills/testing';
import { useContextDetail, useContextDraft } from 'src/api/skills/context-authoring';
import { useAcknowledgeContextHead, usePublishContext } from 'src/api/skills/context-production';
import { loadReviewOptions, useReviewCandidates, useReviewOptions, useReviewSubjects, usePrepareReleaseReview, useSubmitReleaseReview, useShareReleaseEvidence } from 'src/api/skills/review-authoring';

// J4 one-step paths: identities carried forward, single-target default, one
// explicit batch share decision, and renewal opened directly on refusal.
const navigated = vi.hoisted(() => ({ to: null }));
vi.mock('react-router-dom', async (load) => ({ ...(await load()), useNavigate: () => (to) => { navigated.to = to; } }));
vi.mock('src/api/skills/testing', () => ({ useSkillV11Scope: () => ({ scope: { userId: 'reviewer' } }), useSkillV11Query: vi.fn(() => ({})) }));
vi.mock('src/api/skills/context-authoring', () => ({ useContextDetail: vi.fn(() => ({})), useContextDraft: vi.fn(() => ({})) }));
vi.mock('src/api/skills/context-production', () => ({ contextReviewReady: () => true, loadContextReview: vi.fn(), useAcknowledgeContextHead: vi.fn(() => ({})), usePublishContext: vi.fn(() => ({})) }));
vi.mock('src/api/skills/review-metadata', async (load) => ({ ...(await load()), useReviewMembers: () => ({}), useReviewSubject: () => ({}) }));
vi.mock('src/api/skills/review-production', () => ({
  reviewsReady: () => true,
  isStaleReview: (error) => [403, 409, 410].includes(error?.statusCode) && ['release_conflict', 'evidence_expired'].includes(error?.code),
  useReviewInbox: vi.fn(() => ({})), useMarkReviewSeen: vi.fn(() => ({})), useReleaseRequest: vi.fn(), useDecideReleaseRequest: vi.fn(() => ({ mutateAsync: vi.fn() })),
  useExecuteReleaseRequest: vi.fn(), useRenewalPreview: vi.fn(() => ({ isLoading: true })), useRenewReleaseRequest: vi.fn(() => ({ mutateAsync: vi.fn() })),
}));
vi.mock('src/api/skills/review-authoring', () => ({
  useReviewSubjects: vi.fn(), useReviewCandidates: vi.fn(), useReviewOptions: vi.fn(), usePrepareReleaseReview: vi.fn(), useSubmitReleaseReview: vi.fn(), useShareReleaseEvidence: vi.fn(),
  loadReviewOptions: vi.fn(async () => ({ evidence: [] })),
}));

afterEach(cleanup);
beforeEach(() => vi.clearAllMocks());

function Runner({ onExecute, onRenew }) {
  return <><button type="button" onClick={() => onExecute({}).catch(() => {})}>Run release</button><button type="button" onClick={onRenew}>Inspect renewal</button></>;
}
Runner.propTypes = { onExecute: PropTypes.func.isRequired, onRenew: PropTypes.func };

const withRequest = () => useReleaseRequest.mockReturnValue({ data: { request: { id: 'request', authorId: 'author', revision: 1 } }, refetch: vi.fn() });

it('opens renewal after a definite stale release refusal', async () => {
  withRequest();
  const mutateAsync = vi.fn().mockRejectedValueOnce({ statusCode: 409, code: 'release_conflict' });
  useExecuteReleaseRequest.mockReturnValue({ mutateAsync });
  render(<MemoryRouter><RequestReviewController scopeKey="org:ws:reviewer" requestId="request" View={Runner} /></MemoryRouter>);
  expect(useRenewalPreview).toHaveBeenLastCalledWith('request', false);
  fireEvent.click(screen.getByRole('button', { name: 'Run release' }));
  await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
  await waitFor(() => expect(useRenewalPreview).toHaveBeenLastCalledWith('request', true));
  expect(screen.getByText('Renew request')).toBeVisible();
});

it('does not offer renewal for a role refusal', async () => {
  withRequest();
  const mutateAsync = vi.fn().mockRejectedValueOnce({ statusCode: 403, code: 'permission_denied' });
  useExecuteReleaseRequest.mockReturnValue({ mutateAsync });
  render(<MemoryRouter><RequestReviewController scopeKey="org:ws:reviewer" requestId="request" View={Runner} /></MemoryRouter>);
  fireEvent.click(screen.getByRole('button', { name: 'Run release' }));
  await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
  expect(useRenewalPreview).not.toHaveBeenCalledWith('request', true);
});

const ref = (id, visibility = 'private') => ({ source: 'skills_text', sourceId: id, subjectId: 'context', subjectKind: 'agent_context', candidateDigest: 'sha256:candidate',
  visibility, status: 'completed', outcome: 'works_on_examples', payloadExpiresAt: '2999-01-01T00:00:00Z', annotationRevision: 1, executionFingerprint: 'exec', configuration: { text: { model: 'm' }, digest: 'cfg' } });

it('carries the published candidate forward, preselects the only target and shares the named results in one decision', async () => {
  useReviewSubjects.mockReturnValue({ data: { results: [{ id: 'context', name: 'Refund policy', labels: [{ name: 'production', version: 1 }] }], total_pages: 1 } });
  useReviewCandidates.mockReturnValue({ data: { results: [{ id: 'v1', number: 1, digest: 'sha256:old' }], total_pages: 2 } });
  const shared = { targets: [{ name: 'refund_agent', target: { bindingId: 'binding', revision: 1 } }], evidence: [ref('run-a', 'workspace'), ref('run-b', 'workspace')], recipients: [{ id: 'reviewer', name: 'Sam Rivera' }] };
  const refetch = vi.fn().mockResolvedValue({ data: shared });
  useReviewOptions.mockReturnValue({ data: { ...shared, evidence: [ref('run-a'), ref('run-b')] }, refetch });
  const prepare = vi.fn().mockResolvedValue(null);
  usePrepareReleaseReview.mockReturnValue({ mutateAsync: prepare, isPending: false });
  useSubmitReleaseReview.mockReturnValue({ mutateAsync: vi.fn() });
  const share = vi.fn().mockResolvedValue({});
  useShareReleaseEvidence.mockReturnValue({ mutateAsync: share });
  render(<MemoryRouter><ReleaseRequestComposer scopeKey="org:ws:author" prefill={{ kind: 'agent_context', subjectId: 'context', name: 'Refund policy', candidate: { id: 'v2', number: 2, digest: 'sha256:candidate' }, evidence: ['skills_text:run-a', 'skills_text:run-b'] }} /></MemoryRouter>);
  // Carried forward even when the version list has not loaded this candidate.
  expect(screen.getByLabelText('Name')).toHaveValue('Refund policy');
  expect(screen.getByLabelText('Version to release')).toHaveValue('v2');
  expect(screen.getByLabelText('Type')).toHaveAttribute('aria-disabled', 'true');
  expect(useReviewOptions).toHaveBeenLastCalledWith('agent_context', 'context', 'v2');
  expect(screen.getAllByRole('checkbox', { name: /^Attach Answer check/ })).toHaveLength(2);
  screen.getAllByRole('checkbox', { name: /^Attach Answer check/ }).forEach((checkbox) => expect(checkbox).toBeChecked());
  fireEvent.click(screen.getByRole('button', { name: 'Share and attach 2 selected' }));
  // The confirmation names every record it shares by title with a Private badge; each full ID is one hover away and never printed.
  const confirmation = within(screen.getByRole('dialog'));
  expect(confirmation.getByText('Share 2 private results with the workspace?')).toBeVisible();
  expect(confirmation.getAllByText('Private', { exact: true })).toHaveLength(2);
  expect(confirmation.queryByText('run-a')).not.toBeInTheDocument();
  expect(confirmation.queryByRole('button', { name: 'Details' })).not.toBeInTheDocument();
  expect(confirmation.getAllByTitle('run-a').length).toBeGreaterThan(0);
  expect(confirmation.getAllByTitle('run-b').length).toBeGreaterThan(0);
  expect(screen.queryByText('No matching test evidence. Not tested is not a pass.')).not.toBeInTheDocument();
  expect(share).not.toHaveBeenCalled();
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Share and attach' }));
  await waitFor(() => expect(share).toHaveBeenCalledTimes(2));
  expect(share).toHaveBeenCalledWith(expect.objectContaining({ sourceId: 'run-a', acknowledgeSnapshotSharing: true }));
  // Sharing alone does not prepare or send the request.
  expect(prepare).not.toHaveBeenCalled();
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
  await waitFor(() => expect(prepare).toHaveBeenCalledTimes(1));
  const input = prepare.mock.calls[0][0];
  expect(input).toMatchObject({ resourceId: 'context', kind: 'agent_context', candidateId: 'v2', target: { bindingId: 'binding', revision: 1 } });
  expect(input.evidence.map((item) => item.sourceId).sort()).toEqual(['run-a', 'run-b']);
});

function PublishStub({ onPublishAndRequest, oneStepOperation }) {
  return <>
    <button type="button" onClick={() => oneStepOperation.run(onPublishAndRequest, { revision: 3, expectedDigest: 'sha256:candidate', expectedHeadVersion: 1, reviewFingerprint: 'fp3', headAcknowledged: false, baseVersion: 1, note: '', acknowledgeWarnings: false })}>One step</button>
    {oneStepOperation.error && <p>{oneStepOperation.error}</p>}
    {oneStepOperation.uncertain && <button type="button" onClick={oneStepOperation.retry}>Check status</button>}
  </>;
}
PublishStub.propTypes = { onPublishAndRequest: PropTypes.func.isRequired, oneStepOperation: PropTypes.object.isRequired };

const installDraft = (draft) => {
  useContextDetail.mockReturnValue({ data: { context: { name: 'Refund policy' } } });
  const refetch = vi.fn().mockResolvedValue({ data: draft });
  useContextDraft.mockReturnValue({ data: draft, refetch });
  useSkillV11Query.mockReturnValue({ data: { review: { draftRevision: 3, candidateDigest: 'sha256:candidate', reviewFingerprint: 'fp3' }, releaseState: { labels: [], permissions: { canPublish: true } } } });
};

it('acknowledges the reviewed head with the same identity, publishes and carries the version into the request', async () => {
  installDraft({ revision: 3, digest: 'sha256:candidate', headAcknowledged: false });
  const ack = vi.fn().mockResolvedValue({ revision: 4, reviewFingerprint: 'fp4' });
  const publish = vi.fn().mockResolvedValue({ version: { id: 'v2', number: 2, digest: 'sha256:candidate' } });
  useAcknowledgeContextHead.mockReturnValue({ mutateAsync: ack });
  usePublishContext.mockReturnValue({ mutateAsync: publish });
  render(<MemoryRouter><ContextReviewController scopeKey="org:ws:author" contextId="context" View={PublishStub} /></MemoryRouter>);
  fireEvent.click(screen.getByRole('button', { name: 'One step' }));
  await waitFor(() => expect(publish).toHaveBeenCalled());
  expect(ack.mock.calls[0][0]).toMatchObject({ revision: 3, expectedDigest: 'sha256:candidate', reviewFingerprint: 'fp3' });
  expect(publish.mock.calls[0][0]).toMatchObject({ revision: 4, reviewFingerprint: 'fp4', expectedDigest: 'sha256:candidate' });
  expect(navigated.to).toContain('compose=1');
  expect(navigated.to).toContain('candidate=v2');
  expect(navigated.to).toContain('number=2');
});

it("carries only the author's own settled private results for exactly the published bytes", async () => {
  navigated.to = null;
  installDraft({ revision: 3, digest: 'sha256:candidate', headAcknowledged: false });
  useAcknowledgeContextHead.mockReturnValue({ mutateAsync: vi.fn().mockResolvedValue({ revision: 4, reviewFingerprint: 'fp4' }) });
  usePublishContext.mockReturnValue({ mutateAsync: vi.fn().mockResolvedValue({ version: { id: 'v2', number: 2, digest: 'sha256:candidate' } }) });
  const later = new Date(Date.now() + 86_400_000).toISOString();
  const ref = (sourceId, extra = {}) => ({ source: 'skills_text', sourceId, subjectId: 'context', subjectKind: 'agent_context', candidateDigest: 'sha256:candidate', status: 'completed', visibility: 'private', payloadExpiresAt: later, configuration: { text: {} }, ...extra });
  loadReviewOptions.mockResolvedValueOnce({ evidence: [ref('run-a'), ref('run-b'), ref('shared', { visibility: 'workspace' }), ref('other-bytes', { candidateDigest: 'sha256:other' }), ref('running', { status: 'running' })] });
  render(<MemoryRouter><ContextReviewController scopeKey="org:ws:author" contextId="context" View={PublishStub} /></MemoryRouter>);
  fireEvent.click(screen.getByRole('button', { name: 'One step' }));
  await waitFor(() => expect(navigated.to).toContain('candidate=v2'));
  expect(loadReviewOptions).toHaveBeenCalledWith('agent_context', 'context', 'v2');
  expect(new URLSearchParams(navigated.to.split('?')[1]).get('evidence')).toBe('skills_text:run-a,skills_text:run-b');
});

it('keeps an unknown publish outcome retryable without a second head acknowledgment or key', async () => {
  installDraft({ revision: 3, digest: 'sha256:candidate', headAcknowledged: false });
  const ack = vi.fn().mockResolvedValue({ revision: 4, reviewFingerprint: 'fp4' });
  const publish = vi.fn().mockRejectedValueOnce({ statusCode: 503 }).mockResolvedValue({ version: { id: 'v2', number: 2, digest: 'sha256:candidate' } });
  useAcknowledgeContextHead.mockReturnValue({ mutateAsync: ack });
  usePublishContext.mockReturnValue({ mutateAsync: publish });
  render(<MemoryRouter><ContextReviewController scopeKey="org:ws:author" contextId="context" View={PublishStub} /></MemoryRouter>);
  fireEvent.click(screen.getByRole('button', { name: 'One step' }));
  expect(await screen.findByText(/couldn't confirm whether this went through/)).toBeVisible();
  // The server now reports the acknowledged draft; retry reuses it.
  useContextDraft.mock.results.at(-1).value.refetch.mockResolvedValue({ data: { revision: 4, digest: 'sha256:candidate', headAcknowledged: true, reviewFingerprint: 'fp4' } });
  fireEvent.click(screen.getByRole('button', { name: 'Check status' }));
  await waitFor(() => expect(publish).toHaveBeenCalledTimes(2));
  expect(ack).toHaveBeenCalledTimes(1);
  expect(publish.mock.calls[1][0].idempotencyKey).toBe(publish.mock.calls[0][0].idempotencyKey);
  expect(publish.mock.calls[1][0]).toMatchObject({ revision: 4, reviewFingerprint: 'fp4' });
});

it('keeps the form open instead of preparing when the optional pipeline record is incomplete', async () => {
  useReviewSubjects.mockReturnValue({ data: { results: [], total_pages: 1 } });
  useReviewCandidates.mockReturnValue({ data: { results: [], total_pages: 1 } });
  useReviewOptions.mockReturnValue({ data: { targets: [], evidence: [ref('run-a', 'workspace')], recipients: [{ id: 'reviewer', name: 'Sam Rivera' }] }, refetch: vi.fn() });
  const prepare = vi.fn().mockResolvedValue(null);
  usePrepareReleaseReview.mockReturnValue({ mutateAsync: prepare, isPending: false });
  useSubmitReleaseReview.mockReturnValue({ mutateAsync: vi.fn() });
  useShareReleaseEvidence.mockReturnValue({ mutateAsync: vi.fn() });
  render(<MemoryRouter><ReleaseRequestComposer scopeKey="org:ws:author" prefill={{ kind: 'agent_context', subjectId: 'context', name: 'Refund policy', candidate: { id: 'v2', number: 2, digest: 'sha256:candidate' } }} /></MemoryRouter>);
  fireEvent.click(screen.getByRole('button', { name: 'Attach your CI run (optional)' }));
  fireEvent.change(screen.getByLabelText('Pipeline result URL'), { target: { value: 'https://ci.example.invalid/run/1' } });
  expect(screen.queryByRole('button', { name: 'Attach 1 result' })).not.toBeInTheDocument();
  expect(screen.getByRole('checkbox', { name: /^Attach Answer check/ })).not.toBeChecked();
  fireEvent.click(screen.getByRole('checkbox', { name: /^Attach Answer check/ }));
  expect(prepare).not.toHaveBeenCalled();
  expect(screen.getByRole('button', { name: 'Continue' })).toBeDisabled();
});

it('fences a confirmed receipt to its request and authenticated scope while queries refresh', async () => {
  withRequest();
  const receipt = { requestId: 'request', postMoveState: { version: 2 } };
  useExecuteReleaseRequest.mockReturnValue({ mutateAsync: vi.fn().mockResolvedValue(receipt) });
  const View = vi.fn(() => null);
  const { rerender } = render(<MemoryRouter><RequestReviewController scopeKey="org:ws:reviewer" requestId="request" View={View} /></MemoryRouter>);
  await act(async () => { await View.mock.calls.at(-1)[0].onExecute({}); });
  expect(View.mock.calls.at(-1)[0].receipt).toEqual(receipt);
  rerender(<MemoryRouter><RequestReviewController scopeKey="org:ws:reviewer" requestId="another-request" View={View} /></MemoryRouter>);
  expect(View.mock.calls.at(-1)[0].receipt).toBeNull();
  rerender(<MemoryRouter><RequestReviewController scopeKey="org:ws:another-login" requestId="request" View={View} /></MemoryRouter>);
  expect(View.mock.calls.at(-1)[0].receipt).toBeNull();
});

it('blocks a same-version release, including the batch-share prepare path', async () => {
  useReviewSubjects.mockReturnValue({ data: { results: [], total_pages: 1 } });
  useReviewCandidates.mockReturnValue({ data: { results: [{ id: 'v2', number: 2, digest: 'sha256:candidate' }], total_pages: 1 } });
  const data = { subject: { labels: [{ name: 'production', version: 2 }] }, targets: [], evidence: [ref('run-a')], recipients: [{ id: 'reviewer', name: 'Sam Rivera' }] };
  useReviewOptions.mockReturnValue({ data, refetch: vi.fn().mockResolvedValue({ data: { ...data, evidence: [ref('run-a', 'workspace')] } }) });
  const prepare = vi.fn();
  usePrepareReleaseReview.mockReturnValue({ mutateAsync: prepare, isPending: false });
  useShareReleaseEvidence.mockReturnValue({ mutateAsync: vi.fn().mockResolvedValue({}) });
  render(<MemoryRouter><ReleaseRequestComposer scopeKey="org:ws:author" prefill={{ kind: 'agent_context', subjectId: 'context', name: 'Support agent', candidate: { id: 'v2', number: 2, digest: 'sha256:candidate' } }} /></MemoryRouter>);
  expect(screen.getByLabelText('Version to release')).toHaveValue('v2 (in production)');
  expect(screen.getAllByText('v2 is already in production. Choose another version to release.').length).toBeGreaterThan(0);
  expect(screen.getByRole('button', { name: 'Continue' })).toBeDisabled();
  expect(screen.queryByRole('button', { name: 'Next versions' })).not.toBeInTheDocument();
  // One private result has no bulk button; it is shared from its own row, and the consent title is singular.
  expect(screen.queryByRole('button', { name: 'Share and attach 1 result' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('checkbox', { name: /^Attach Answer check/ }));
  expect(within(screen.getByRole('dialog')).getByText('Share 1 private result with the workspace?')).toBeVisible();
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Share and attach' }));
  await waitFor(() => expect(useReviewOptions.mock.results.at(-1).value.refetch).toHaveBeenCalled());
  expect(prepare).not.toHaveBeenCalled();
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  fireEvent.click(screen.getByRole('button', { name: 'Choose another version' }));
  expect(screen.getByLabelText('Name')).toHaveAttribute('readonly');
  fireEvent.mouseDown(screen.getByRole('combobox', { name: 'Version to release' }));
  expect(screen.getByRole('option', { name: 'v2 (in production)' })).toBeVisible();
});

it('explains label movement and keeps the release write bound to its reviewed identity', async () => {
  const execute = vi.fn().mockResolvedValue({});
  const packet = { subjectKind: 'agent_context', candidateVersionId: 'v2', candidateDigest: 'digest', evidence: [], target: null, transition: { label: 'production', expectedVersion: 1, version: 2 } };
  render(<ReleaseRequestPanel scopeKey="reviewer" state="ready" preview={{ packet, fingerprint: 'fp', current: true }} review={{ complete: true }} request={{ id: 'request', name: 'support-agent', revision: 3, status: 'approved' }} permissions={{ canExecute: true }} reviewedIdentity="3:fp" onExecute={execute} />);
  fireEvent.click(screen.getByRole('button', { name: /^Move production to v\d+$/ }));
  expect(screen.getByText("Production for support-agent will point to v2. Apps that follow production pick it up the next time they fetch it; check Connected apps to confirm delivery. Running conversations aren't changed. If the request changed after it was approved, the move is refused and nothing moves.")).toBeVisible();
  expect(execute).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Move production' }));
  await waitFor(() => expect(execute).toHaveBeenCalledWith(expect.objectContaining({ requestId: 'request', revision: 3, expectedFingerprint: 'fp' })));
});

it('shows disabled decision reasons without requiring acknowledgment to request changes', async () => {
  const decide = vi.fn().mockResolvedValue({});
  const packet = { candidateVersionId: 'v2', evidence: [], target: null, transition: { label: 'production', expectedVersion: 1, version: 2 } };
  render(<ReleaseRequestPanel scopeKey="reviewer" state="ready" preview={{ packet, fingerprint: 'fp', current: true }} review={{ complete: true }} request={{ id: 'request', revision: 3, status: 'pending' }} permissions={{ canDecide: true }} onDecide={decide} />);
  expect(screen.getByText('Tick the acknowledgment to approve')).toBeVisible();
  expect(screen.queryByLabelText('Review comment')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Request changes' }));
  expect(decide).not.toHaveBeenCalled();
  expect(screen.getByText('A comment is required to request changes.')).toBeVisible();
  fireEvent.change(screen.getByLabelText('Review comment'), { target: { value: 'Please revise the instructions.' } });
  expect(screen.getByRole('button', { name: 'Approve release' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Request changes' }));
  await waitFor(() => expect(decide).toHaveBeenCalledWith(expect.objectContaining({ decision: 'request_changes', revision: 3, expectedFingerprint: 'fp' })));
});

it('gets the assigned waiting count from pending requests, not released unread records', () => {
  useReviewInbox.mockImplementation(({ status }) => ({ data: { results: [], count: status === 'pending' ? 1 : 7, unreadCount: 7 } }));
  const View = vi.fn(() => null);
  render(<MemoryRouter><ReviewInboxController scopeKey="reviewer" View={View} /></MemoryRouter>);
  expect(View.mock.calls.at(-1)[0].data.pendingCount).toBe(1);
  expect(View.mock.calls.at(-1)[0].status).toBe('');
});

it('renders denied review access instead of an error and hides creation even on a compose URL', () => {
  useReviewInbox.mockReturnValue({ isError: true, error: { statusCode: 403 } });
  render(<MemoryRouter initialEntries={['/dashboard/skills/reviews?compose=1']}><ReviewInboxController scopeKey="viewer" View={ReleaseInbox} /></MemoryRouter>);
  expect(screen.getByText("You don't have access to reviews.")).toBeVisible();
  expect(screen.getByText('Ask a workspace admin for reviewer access.')).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Request release review' })).not.toBeInTheDocument();
  expect(screen.queryByText("Couldn't load reviews.")).not.toBeInTheDocument();
});

it('uses recipient names only from metadata matching the inbox revision', () => {
  useReviewInbox.mockReturnValue({ data: { results: [{ id: 'request', revision: 2, status: 'pending' }], count: 1 } });
  useSkillV11Query.mockReturnValue({ data: [{ id: 'request', revision: 2, recipients: [{ id: 'reviewer', name: 'Maya Chen' }] }] });
  const View = vi.fn(() => null);
  const { rerender } = render(<MemoryRouter><ReviewInboxController scopeKey="reviewer" View={View} /></MemoryRouter>);
  expect(View.mock.calls.at(-1)[0].data.results[0].recipients).toEqual([{ id: 'reviewer', name: 'Maya Chen' }]);
  useSkillV11Query.mockReturnValue({ data: [{ id: 'request', revision: 1, recipients: [{ id: 'old-reviewer', name: 'Earlier reviewer' }] }] });
  rerender(<MemoryRouter><ReviewInboxController scopeKey="reviewer" View={View} /></MemoryRouter>);
  expect(View.mock.calls.at(-1)[0].data.results[0].recipients).toBeUndefined();
});

it('keeps carried evidence and reviewer when editing a prepared request, but clears its acknowledgment', async () => {
  const evidence = ref('carried', 'workspace');
  const packet = { subjectId: 'context', subjectKind: 'agent_context', candidateVersionId: 'v2', candidateDigest: 'sha256:candidate', evidence: [evidence], target: null, transition: { label: 'production', expectedVersion: 1, version: 2 } };
  useReviewSubjects.mockReturnValue({ data: { results: [], total_pages: 1 } });
  useReviewCandidates.mockReturnValue({ data: { results: [], total_pages: 1 } });
  useReviewOptions.mockReturnValue({ data: { targets: [], evidence: [evidence, { ...ref('newer', 'workspace'), completedAt: '2026-11-14' }], recipients: [{ id: 'reviewer', name: 'Sam Rivera' }] } });
  const prepared = { preview: { packet, fingerprint: 'fp', current: true }, review: { complete: true } };
  const prepare = vi.fn().mockResolvedValue(prepared);
  usePrepareReleaseReview.mockReturnValue({ mutateAsync: prepare });
  const submit = vi.fn();
  useSubmitReleaseReview.mockReturnValue({ mutateAsync: submit });
  useShareReleaseEvidence.mockReturnValue({ mutateAsync: vi.fn() });
  render(<MemoryRouter><ReleaseRequestComposer scopeKey="scope" prefill={{ kind: 'agent_context', subjectId: 'context', name: 'Support agent', candidate: { id: 'v2', number: 2, digest: 'sha256:candidate' }, evidence: ['skills_text:carried'] }} /></MemoryRouter>);
  expect(await screen.findByText('1 result attached from your test')).toBeVisible();
  expect(screen.getByText('From your test')).toBeVisible();
  // Something is attached, so the "no evidence" caution stays quiet.
  expect(screen.queryByText(/aren.t attached|isn.t attached/)).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Attach all' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
  expect(await screen.findByText('Reviewer: Sam Rivera')).toBeVisible();
  expect(screen.queryByRole('combobox', { name: 'Assigned reviewer' })).not.toBeInTheDocument();
  expect(prepare).toHaveBeenCalledWith(expect.objectContaining({ evidence: [{ source: 'skills_text', sourceId: 'carried', annotationRevision: 1 }] }));
  fireEvent.click(screen.getByRole('checkbox', { name: "I've checked this version, where it's going and the evidence I'm attaching." }));
  fireEvent.click(screen.getByRole('button', { name: 'Edit request' }));
  expect(screen.getByRole('combobox', { name: 'Assigned reviewer' })).toHaveTextContent('Sam Rivera');
  expect(screen.getByText('1 result attached from your test')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
  expect(await screen.findByRole('checkbox', { name: "I've checked this version, where it's going and the evidence I'm attaching." })).not.toBeChecked();
  expect(submit).not.toHaveBeenCalled();
  // Step 2 is titled for what it does; step 1 kept "Request release review".
  expect(screen.getByRole('heading', { name: 'Confirm and send' })).toBeVisible();
  expect(screen.queryByRole('heading', { name: 'Request release review' })).not.toBeInTheDocument();
  // One decision footer: the acknowledgment and the text button on the left; the reason, the outlined Cancel, then the contained primary on the right.
  const before = (a, b) => expect(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  const ack = screen.getByRole('checkbox', { name: "I've checked this version, where it's going and the evidence I'm attaching." });
  const edit = screen.getByRole('button', { name: 'Edit request' });
  const cancel = screen.getByRole('button', { name: 'Cancel' });
  const reason = screen.getByText('Tick the acknowledgment to send');
  const send = screen.getByRole('button', { name: 'Send request' });
  [[ack, edit], [edit, reason], [reason, cancel], [cancel, send]].forEach(([a, b]) => before(a, b));
  expect(edit).toHaveClass('MuiButton-text');
  expect(cancel).toHaveClass('MuiButton-outlined');
  expect(send).toHaveClass('MuiButton-contained');
  expect(send).toHaveAccessibleDescription('Tick the acknowledgment to send');
  expect(document.querySelector('[data-sticky-footer]').contains(send)).toBe(true);
});

// ---- UX iteration 3, REV2 and REV5 ----
it('marks and lists the carried private run first, pre-ticks it without attaching, and requires explicit sharing', async () => {
  useReviewSubjects.mockReturnValue({ data: { results: [], total_pages: 1 } });
  useReviewCandidates.mockReturnValue({ data: { results: [], total_pages: 1 } });
  useReviewOptions.mockReturnValue({ data: { targets: [], evidence: [ref('run-a'), ref('run-b')], recipients: [{ id: 'reviewer', name: 'Sam Rivera' }] }, refetch: vi.fn() });
  const prepare = vi.fn().mockResolvedValue(null);
  usePrepareReleaseReview.mockReturnValue({ mutateAsync: prepare, isPending: false });
  useSubmitReleaseReview.mockReturnValue({ mutateAsync: vi.fn() });
  useShareReleaseEvidence.mockReturnValue({ mutateAsync: vi.fn() });
  render(<MemoryRouter><ReleaseRequestComposer scopeKey="org:ws:author" prefill={{ kind: 'agent_context', subjectId: 'context', name: 'Refund policy', candidate: { id: 'v2', number: 2, digest: 'sha256:candidate' }, evidence: ['skills_text:run-b'] }} /></MemoryRouter>);
  const rows = within(screen.getByRole('table', { name: 'Test evidence' })).getAllByRole('row').slice(1);
  expect(within(rows[0]).getByText('From your test')).toBeVisible();
  expect(within(rows[1]).queryByText('From your test')).not.toBeInTheDocument();
  // A carried private result is selected for a possible share, not attached.
  expect(within(rows[0]).getByRole('checkbox')).toBeChecked();
  expect(within(rows[1]).getByRole('checkbox')).not.toBeChecked();
  // UXF-17: the footer names the ticked row as not attached instead of "No test evidence attached".
  expect(screen.getByText('1 ticked result not attached until shared. Continue asks you to share it.')).toBeVisible();
  expect(screen.queryByText('No test evidence attached')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Attach all' })).not.toBeInTheDocument();
  expect(screen.getAllByText('Private results are visible only to you; sharing lets the reviewer see them.')).toHaveLength(1);
  fireEvent.click(screen.getByRole('button', { name: 'Share and attach 1 selected' }));
  expect(within(screen.getByRole('dialog')).getByText('Share 1 private result with the workspace?')).toBeVisible();
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  expect(useShareReleaseEvidence.mock.results.at(-1).value.mutateAsync).not.toHaveBeenCalled();
  // Continue never sends a ticked private row unshared: it asks again, and only the explicit
  // "Continue without it" sends the request without that result.
  fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
  expect(within(screen.getByRole('dialog')).getByText('Share 1 private result with the workspace?')).toBeVisible();
  expect(prepare).not.toHaveBeenCalled();
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Continue without it' }));
  await waitFor(() => expect(prepare).toHaveBeenCalledTimes(1));
  expect(prepare.mock.calls[0][0].evidence).toEqual([]);
  expect(useShareReleaseEvidence.mock.results.at(-1).value.mutateAsync).not.toHaveBeenCalled();
});
it('shows two numbered steps with the current one marked, and plain headings', () => {
  useReviewSubjects.mockReturnValue({ data: { results: [], total_pages: 1 } });
  useReviewCandidates.mockReturnValue({ data: { results: [], total_pages: 1 } });
  useReviewOptions.mockReturnValue({ data: { targets: [{ name: 'refund_agent', target: { bindingId: 'binding', revision: 1 } }, { name: 'other_agent', target: { bindingId: 'other', revision: 1 } }], evidence: [], recipients: [{ id: 'reviewer', name: 'Sam Rivera' }] }, refetch: vi.fn() });
  usePrepareReleaseReview.mockReturnValue({ mutateAsync: vi.fn(), isPending: false });
  useSubmitReleaseReview.mockReturnValue({ mutateAsync: vi.fn() });
  useShareReleaseEvidence.mockReturnValue({ mutateAsync: vi.fn() });
  render(<MemoryRouter><ReleaseRequestComposer scopeKey="org:ws:author" prefill={{ kind: 'agent_context', subjectId: 'context', name: 'Refund policy', candidate: { id: 'v2', number: 2, digest: 'sha256:candidate' } }} /></MemoryRouter>);
  const steps = within(screen.getByLabelText('Request steps')).getAllByRole('listitem');
  expect(steps).toHaveLength(2);
  expect(steps[0]).toHaveAttribute('aria-current', 'step');
  expect(steps[1]).not.toHaveAttribute('aria-current');
  expect(screen.getByText('Release details')).toBeVisible();
  expect(screen.queryByText('Release candidate')).not.toBeInTheDocument();
  expect(screen.queryByText('Published candidate')).not.toBeInTheDocument();
  expect(screen.getByLabelText('Version to release')).toBeVisible();
  // With nothing to attach, the member gets one outlined, version-named way to run tests.
  const runTests = screen.getByRole('link', { name: 'Run tests on v2' });
  expect(runTests).toBeVisible();
  expect(runTests).toHaveClass('MuiButton-outlined');
  fireEvent.mouseDown(screen.getByRole('combobox', { name: 'Application target' }));
  expect(screen.getByRole('option', { name: 'refund_agent' })).toBeVisible();
  expect(screen.queryByText(/Configuration date not reported/)).not.toBeInTheDocument();
});

it('titles a released request by its outcome with no badge, and names only the resource in the breadcrumb', () => {
  const transition = { label: 'production', expectedVersion: 1, version: 2 };
  const packet = { subjectId: 'context', subjectKind: 'agent_context', candidateVersionId: 'v2', candidateDigest: 'sha256:candidate', evidence: [], target: null, transition };
  useReleaseRequest.mockReturnValue({ data: { request: { id: 'request', name: 'support-agent', authorId: 'author', revision: 3, status: 'released' }, preview: { packet, fingerprint: 'fp', current: true }, receipt: { requestId: 'request', postMoveState: { label: 'production', version: 2 } } }, refetch: vi.fn() });
  const View = vi.fn(() => null);
  const { rerender } = render(<MemoryRouter><RequestReviewController scopeKey="org:ws:reviewer" requestId="request" View={View} /></MemoryRouter>);
  expect(screen.getByRole('heading', { name: 'Production moved from v1 to v2' })).toBeVisible();
  expect(screen.queryByText('Released', { exact: true })).not.toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: /^Move production/ })).not.toBeInTheDocument();
  const crumbs = within(screen.getByLabelText('Breadcrumb'));
  expect(crumbs.getByText('support-agent')).toBeVisible();
  expect(crumbs.queryByText('support-agent v2')).not.toBeInTheDocument();
  // Until it is released the title names the move being asked for, with its status.
  useReleaseRequest.mockReturnValue({ data: { request: { id: 'request', name: 'support-agent', authorId: 'author', revision: 1, status: 'pending' }, preview: { packet, fingerprint: 'fp', current: true } }, refetch: vi.fn() });
  rerender(<MemoryRouter><RequestReviewController scopeKey="org:ws:reviewer" requestId="request" View={View} /></MemoryRouter>);
  expect(screen.getByRole('heading', { name: 'Move production from v1 to v2' })).toBeVisible();
  expect(screen.getByText('Waiting for approval')).toBeVisible();
});

it('offers Attach on the confirm step when results were available but none is attached, and goes back to the evidence', async () => {
  useReviewSubjects.mockReturnValue({ data: { results: [], total_pages: 1 } });
  useReviewCandidates.mockReturnValue({ data: { results: [], total_pages: 1 } });
  useReviewOptions.mockReturnValue({ data: { targets: [], evidence: [ref('run-a', 'workspace'), ref('run-b', 'workspace')], recipients: [{ id: 'reviewer', name: 'Sam Rivera' }] }, refetch: vi.fn() });
  const packet = { subjectId: 'context', subjectKind: 'agent_context', candidateVersionId: 'v2', candidateDigest: 'sha256:candidate', evidence: [], target: null, transition: { label: 'production', expectedVersion: 1, version: 2 } };
  usePrepareReleaseReview.mockReturnValue({ mutateAsync: vi.fn().mockResolvedValue({ preview: { packet, fingerprint: 'fp', current: true }, review: { complete: true } }), isPending: false });
  useSubmitReleaseReview.mockReturnValue({ mutateAsync: vi.fn() });
  useShareReleaseEvidence.mockReturnValue({ mutateAsync: vi.fn() });
  render(<MemoryRouter><ReleaseRequestComposer scopeKey="org:ws:author" prefill={{ kind: 'agent_context', subjectId: 'context', name: 'Refund policy', candidate: { id: 'v2', number: 2, digest: 'sha256:candidate' } }} /></MemoryRouter>);
  fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
  const attach = await screen.findByRole('button', { name: 'Attach' });
  expect(attach).toHaveClass('MuiButton-text');
  expect(attach.parentElement).toHaveTextContent('No test results attached ·');
  expect(screen.queryByText(/available\)/)).not.toBeInTheDocument();
  fireEvent.click(attach);
  expect(await screen.findByRole('button', { name: 'Continue' })).toBeVisible();
  expect(screen.getAllByRole('checkbox', { name: /^Attach Answer check/ })).toHaveLength(2);
});
