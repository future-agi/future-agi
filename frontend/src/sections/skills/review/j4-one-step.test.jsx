import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import PropTypes from 'prop-types';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { RequestReviewController } from './SkillsReviewControllers';
import ReleaseRequestComposer from './ReleaseRequestComposer';
import { useExecuteReleaseRequest, useReleaseRequest, useRenewalPreview } from 'src/api/skills/review-production';
import { useReviewCandidates, useReviewOptions, useReviewSubjects, usePrepareReleaseReview, useSubmitReleaseReview, useShareReleaseEvidence } from 'src/api/skills/review-authoring';

// J4 one-step paths: identities carried forward, single-target default, one
// explicit batch share decision, and renewal opened directly on refusal.
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
  useReviewCandidates.mockReturnValue({ data: { results: [{ id: 'v2', number: 2, digest: 'sha256:candidate' }], total_pages: 1 } });
  const shared = { targets: [{ name: 'refund_agent', target: { bindingId: 'binding', revision: 1 } }], evidence: [ref('run-a', 'workspace'), ref('run-b', 'workspace')], recipients: [] };
  const refetch = vi.fn().mockResolvedValue({ data: shared });
  useReviewOptions.mockReturnValue({ data: { ...shared, evidence: [ref('run-a'), ref('run-b')] }, refetch });
  const prepare = vi.fn().mockResolvedValue(null);
  usePrepareReleaseReview.mockReturnValue({ mutateAsync: prepare, isPending: false });
  useSubmitReleaseReview.mockReturnValue({ mutateAsync: vi.fn() });
  const share = vi.fn().mockResolvedValue({});
  useShareReleaseEvidence.mockReturnValue({ mutateAsync: share });
  render(<MemoryRouter><ReleaseRequestComposer scopeKey="org:ws:author" prefill={{ kind: 'agent_context', subjectId: 'context', candidateId: 'v2' }} /></MemoryRouter>);
  expect(useReviewOptions).toHaveBeenLastCalledWith('agent_context', 'context', 'v2');
  fireEvent.click(screen.getByRole('button', { name: 'Share and attach 2 results' }));
  expect(screen.getByText(/run-a · private/)).toBeVisible();
  expect(screen.getByText(/run-b · private/)).toBeVisible();
  expect(share).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Share and attach' }));
  await waitFor(() => expect(share).toHaveBeenCalledTimes(2));
  expect(share).toHaveBeenCalledWith(expect.objectContaining({ sourceId: 'run-a', acknowledgeSnapshotSharing: true }));
  fireEvent.click(screen.getByRole('button', { name: 'Prepare exact review' }));
  await waitFor(() => expect(prepare).toHaveBeenCalled());
  const input = prepare.mock.calls[0][0];
  expect(input).toMatchObject({ resourceId: 'context', kind: 'agent_context', candidateId: 'v2', target: { bindingId: 'binding', revision: 1 } });
  expect(input.evidence.map((item) => item.sourceId).sort()).toEqual(['run-a', 'run-b']);
});
