import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import PropTypes from 'prop-types';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { ContextReviewController, RequestReviewController } from './SkillsReviewControllers';
import ReleaseRequestComposer from './ReleaseRequestComposer';
import { useExecuteReleaseRequest, useReleaseRequest, useRenewalPreview } from 'src/api/skills/review-production';
import { useSkillV11Query } from 'src/api/skills/testing';
import { useContextDetail, useContextDraft } from 'src/api/skills/context-authoring';
import { useAcknowledgeContextHead, usePublishContext } from 'src/api/skills/context-production';
import { useReviewCandidates, useReviewOptions, useReviewSubjects, usePrepareReleaseReview, useSubmitReleaseReview, useShareReleaseEvidence } from 'src/api/skills/review-authoring';

// J4 one-step paths: identities carried forward, single-target default, one
// explicit batch share decision, and renewal opened directly on refusal.
const navigated = vi.hoisted(() => ({ to: null }));
vi.mock('react-router-dom', async (load) => ({ ...(await load()), useNavigate: () => (to) => { navigated.to = to; } }));
vi.mock('src/api/skills/testing', () => ({ useSkillV11Scope: () => ({ scope: { userId: 'reviewer' } }), useSkillV11Query: vi.fn(() => ({})) }));
vi.mock('src/api/skills/context-authoring', () => ({ useContextDetail: vi.fn(() => ({})), useContextDraft: vi.fn(() => ({})) }));
vi.mock('src/api/skills/context-production', () => ({ contextReviewReady: () => true, loadContextReview: vi.fn(), useAcknowledgeContextHead: vi.fn(() => ({})), usePublishContext: vi.fn(() => ({})) }));
vi.mock('src/api/skills/review-production', () => ({
  reviewsReady: () => true,
  isStaleReview: (error) => [403, 409, 410].includes(error?.statusCode) && ['release_conflict', 'evidence_expired'].includes(error?.code),
  useReviewInbox: vi.fn(() => ({})), useMarkReviewSeen: vi.fn(() => ({})), useReleaseRequest: vi.fn(), useDecideReleaseRequest: vi.fn(() => ({ mutateAsync: vi.fn() })),
  useExecuteReleaseRequest: vi.fn(), useRenewalPreview: vi.fn(() => ({ isLoading: true })), useRenewReleaseRequest: vi.fn(() => ({ mutateAsync: vi.fn() })),
}));
vi.mock('src/api/skills/review-authoring', () => ({
  useReviewSubjects: vi.fn(), useReviewCandidates: vi.fn(), useReviewOptions: vi.fn(), usePrepareReleaseReview: vi.fn(), useSubmitReleaseReview: vi.fn(), useShareReleaseEvidence: vi.fn(),
}));

afterEach(cleanup);
beforeEach(() => vi.clearAllMocks());

function Runner({ onExecute }) {
  return <button type="button" onClick={() => onExecute({}).catch(() => {})}>Run release</button>;
}
Runner.propTypes = { onExecute: PropTypes.func.isRequired };

const withRequest = () => useReleaseRequest.mockReturnValue({ data: { request: { id: 'request', authorId: 'author', revision: 1 } }, refetch: vi.fn() });

it('opens the renewal inspection when the server refuses a stale release, and nothing else', async () => {
  withRequest();
  const mutateAsync = vi.fn().mockRejectedValueOnce({ statusCode: 409, code: 'release_conflict' });
  useExecuteReleaseRequest.mockReturnValue({ mutateAsync });
  render(<MemoryRouter><RequestReviewController scopeKey="org:ws:reviewer" requestId="request" View={Runner} /></MemoryRouter>);
  expect(useRenewalPreview).toHaveBeenLastCalledWith('request', false);
  fireEvent.click(screen.getByRole('button', { name: 'Run release' }));
  await waitFor(() => expect(useRenewalPreview).toHaveBeenLastCalledWith('request', true));
  expect(screen.getByText('Renew release review')).toBeVisible();
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
  visibility, status: 'completed', payloadExpiresAt: '2999-01-01T00:00:00Z', annotationRevision: 1, executionFingerprint: 'exec', configuration: { text: { model: 'm' }, digest: 'cfg' } });

it('carries the published candidate forward, preselects the only target and shares the named results in one decision', async () => {
  useReviewSubjects.mockReturnValue({ data: { results: [{ id: 'context', name: 'Refund policy' }], total_pages: 1 } });
  useReviewCandidates.mockReturnValue({ data: { results: [{ id: 'v1', number: 1, digest: 'sha256:old' }], total_pages: 2 } });
  const shared = { targets: [{ name: 'refund_agent', target: { bindingId: 'binding', revision: 1 } }], evidence: [ref('run-a', 'workspace'), ref('run-b', 'workspace')], recipients: [] };
  const refetch = vi.fn().mockResolvedValue({ data: shared });
  useReviewOptions.mockReturnValue({ data: { ...shared, evidence: [ref('run-a'), ref('run-b')] }, refetch });
  const prepare = vi.fn().mockResolvedValue(null);
  usePrepareReleaseReview.mockReturnValue({ mutateAsync: prepare, isPending: false });
  useSubmitReleaseReview.mockReturnValue({ mutateAsync: vi.fn() });
  const share = vi.fn().mockResolvedValue({});
  useShareReleaseEvidence.mockReturnValue({ mutateAsync: share });
  render(<MemoryRouter><ReleaseRequestComposer scopeKey="org:ws:author" prefill={{ kind: 'agent_context', subjectId: 'context', name: 'Refund policy', candidate: { id: 'v2', number: 2, digest: 'sha256:candidate' } }} /></MemoryRouter>);
  // Carried forward even when the version list has not loaded this candidate.
  expect(screen.getByLabelText('Release resource')).toHaveValue('Refund policy');
  expect(screen.getByLabelText('Published candidate')).toHaveValue('v2');
  expect(useReviewOptions).toHaveBeenLastCalledWith('agent_context', 'context', 'v2');
  fireEvent.click(screen.getByRole('button', { name: 'Share and attach 2 results' }));
  expect(screen.getByText(/run-a · private/)).toBeVisible();
  expect(screen.getByText(/run-b · private/)).toBeVisible();
  expect(share).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Share and attach' }));
  await waitFor(() => expect(share).toHaveBeenCalledTimes(2));
  expect(share).toHaveBeenCalledWith(expect.objectContaining({ sourceId: 'run-a', acknowledgeSnapshotSharing: true }));
  // The batch decision continues to the exact packet without another click;
  // the packet is only prepared for inspection, never sent.
  await waitFor(() => expect(prepare).toHaveBeenCalledTimes(1));
  const input = prepare.mock.calls[0][0];
  expect(input).toMatchObject({ resourceId: 'context', kind: 'agent_context', candidateId: 'v2', target: { bindingId: 'binding', revision: 1 } });
  expect(input.evidence.map((item) => item.sourceId).sort()).toEqual(['run-a', 'run-b']);
});

function PublishStub({ onPublishAndRequest, oneStepOperation }) {
  return <>
    <button type="button" onClick={() => oneStepOperation.run(onPublishAndRequest, { revision: 3, expectedDigest: 'sha256:candidate', expectedHeadVersion: 1, reviewFingerprint: 'fp3', headAcknowledged: false, baseVersion: 1, note: '', acknowledgeWarnings: false })}>One step</button>
    {oneStepOperation.error && <p>{oneStepOperation.error}</p>}
    {oneStepOperation.uncertain && <button type="button" onClick={oneStepOperation.retry}>Retry original request</button>}
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

it('keeps an unknown publish outcome retryable without a second head acknowledgment or key', async () => {
  installDraft({ revision: 3, digest: 'sha256:candidate', headAcknowledged: false });
  const ack = vi.fn().mockResolvedValue({ revision: 4, reviewFingerprint: 'fp4' });
  const publish = vi.fn().mockRejectedValueOnce({ statusCode: 503 }).mockResolvedValue({ version: { id: 'v2', number: 2, digest: 'sha256:candidate' } });
  useAcknowledgeContextHead.mockReturnValue({ mutateAsync: ack });
  usePublishContext.mockReturnValue({ mutateAsync: publish });
  render(<MemoryRouter><ContextReviewController scopeKey="org:ws:author" contextId="context" View={PublishStub} /></MemoryRouter>);
  fireEvent.click(screen.getByRole('button', { name: 'One step' }));
  expect(await screen.findByText(/response was not confirmed/)).toBeVisible();
  // The server now reports the acknowledged draft; retry reuses it.
  useContextDraft.mock.results.at(-1).value.refetch.mockResolvedValue({ data: { revision: 4, digest: 'sha256:candidate', headAcknowledged: true, reviewFingerprint: 'fp4' } });
  fireEvent.click(screen.getByRole('button', { name: 'Retry original request' }));
  await waitFor(() => expect(publish).toHaveBeenCalledTimes(2));
  expect(ack).toHaveBeenCalledTimes(1);
  expect(publish.mock.calls[1][0].idempotencyKey).toBe(publish.mock.calls[0][0].idempotencyKey);
  expect(publish.mock.calls[1][0]).toMatchObject({ revision: 4, reviewFingerprint: 'fp4' });
});

it('keeps the form open instead of preparing when the optional pipeline record is incomplete', async () => {
  useReviewSubjects.mockReturnValue({ data: { results: [], total_pages: 1 } });
  useReviewCandidates.mockReturnValue({ data: { results: [], total_pages: 1 } });
  useReviewOptions.mockReturnValue({ data: { targets: [], evidence: [ref('run-a', 'workspace')], recipients: [] }, refetch: vi.fn() });
  const prepare = vi.fn().mockResolvedValue(null);
  usePrepareReleaseReview.mockReturnValue({ mutateAsync: prepare, isPending: false });
  useSubmitReleaseReview.mockReturnValue({ mutateAsync: vi.fn() });
  useShareReleaseEvidence.mockReturnValue({ mutateAsync: vi.fn() });
  render(<MemoryRouter><ReleaseRequestComposer scopeKey="org:ws:author" prefill={{ kind: 'agent_context', subjectId: 'context', name: 'Refund policy', candidate: { id: 'v2', number: 2, digest: 'sha256:candidate' } }} /></MemoryRouter>);
  fireEvent.change(screen.getByLabelText('Pipeline result URL'), { target: { value: 'https://ci.example.invalid/run/1' } });
  fireEvent.click(screen.getByRole('button', { name: 'Attach 1 results' }));
  await waitFor(() => expect(screen.getByRole('checkbox', { name: /^Attach text evidence / })).toBeChecked());
  expect(prepare).not.toHaveBeenCalled();
  expect(screen.getByRole('button', { name: 'Prepare exact review' })).toBeDisabled();
});
