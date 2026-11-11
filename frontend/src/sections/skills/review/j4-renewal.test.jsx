import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import ReleaseRenewalDialog from './ReleaseRenewalDialog';
import ReleaseRequestPanel from './ReleaseRequestPanel';
import { useRenewalPreview, useRenewReleaseRequest } from 'src/api/skills/review-production';

vi.mock('src/api/skills/review-production', () => ({ useRenewalPreview: vi.fn(), useRenewReleaseRequest: vi.fn() }));
const packet = { schema: 1, subjectKind: 'agent_context', subjectId: 'context', candidateVersionId: 'candidate', candidateDigest: 'sha256:candidate',
  transition: { label: 'production', version: 2, expectedVersion: 1, expectedReleaseRevision: 3, expectedSecurityRevision: 0, expectedImpactFingerprint: 'impact' },
  target: null, evidence: [], externalPipeline: null };
const preview = { requestId: 'request', revision: 2, status: 'approved', subjectId: 'context', previousFingerprint: 'old', fingerprint: 'renewed', packet,
  changes: [{ item: 'transition.expected_release_revision', before: 1, after: 3 }],
  removedEvidence: [{ source: 'skills_text', sourceId: 'expired-run', reason: 'evidence_expired' }] };
const mutateAsync = vi.fn();
beforeEach(() => {
  vi.clearAllMocks();
  useRenewReleaseRequest.mockReturnValue({ mutateAsync });
  mutateAsync.mockResolvedValue({ id: 'request', revision: 3, status: 'pending' });
});
afterEach(cleanup);

it('shows every changed precondition and removed evidence before creating the next revision', async () => {
  useRenewalPreview.mockReturnValue({ data: preview, isLoading: false, isError: false });
  const onClose = vi.fn();
  render(<ReleaseRenewalDialog open requestId="request" onClose={onClose} />);
  expect(screen.getByText('transition.expected_release_revision')).toBeVisible();
  expect(screen.getByText(/expired-run is removed from the renewed packet/)).toBeVisible();
  const create = screen.getByRole('button', { name: 'Create renewed revision' });
  expect(create).toBeDisabled();
  fireEvent.click(screen.getByRole('checkbox', { name: 'I inspected every change. Approval is required again.' }));
  fireEvent.click(create);
  await waitFor(() => expect(onClose).toHaveBeenCalled());
  expect(mutateAsync).toHaveBeenCalledWith(expect.objectContaining({ requestId: 'request', revision: 2, expectedFingerprint: 'renewed' }));
});

it('creates nothing when the reviewed packet is still current', () => {
  useRenewalPreview.mockReturnValue({ data: undefined, isLoading: false, isError: true, error: { code: 'review_current' } });
  render(<ReleaseRenewalDialog open requestId="request" onClose={vi.fn()} />);
  expect(screen.getByText('This revision is still current. Nothing needs renewal.')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Create renewed revision' })).toBeDisabled();
  expect(mutateAsync).not.toHaveBeenCalled();
});

it('offers explicit renewal only to the assigned reviewer of a stale packet', () => {
  const props = { scopeKey: 'org:ws:reviewer', state: 'ready', preview: { packet, fingerprint: 'old', current: false }, staleCode: 'release_conflict',
    request: { id: 'request', revision: 2, status: 'approved' }, review: { complete: false }, onRenew: vi.fn() };
  const { rerender } = render(<ReleaseRequestPanel {...props} permissions={{ canDecide: true, canExecute: true }} />);
  expect(screen.getByText(/stale \(release_conflict\)/)).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Renew review' }));
  expect(props.onRenew).toHaveBeenCalled();
  expect(screen.getByRole('button', { name: 'Release approved packet' })).toBeDisabled();
  rerender(<ReleaseRequestPanel {...props} permissions={{ canDecide: false, canExecute: false }} />);
  expect(screen.queryByRole('button', { name: 'Renew review' })).not.toBeInTheDocument();
});
