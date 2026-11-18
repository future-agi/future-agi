import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import ReleaseRenewalDialog from './ReleaseRenewalDialog';
import ReleaseRequestPanel from './ReleaseRequestPanel';
import { useDecideReleaseRequest, useRenewalPreview, useRenewReleaseRequest } from 'src/api/skills/review-production';

vi.mock('src/api/skills/review-production', () => ({ useRenewalPreview: vi.fn(), useRenewReleaseRequest: vi.fn(), useDecideReleaseRequest: vi.fn() }));
const packet = { schema: 1, subjectKind: 'agent_context', subjectId: 'context', candidateVersionId: 'candidate', candidateDigest: 'sha256:candidate',
  transition: { label: 'production', version: 2, expectedVersion: 1, expectedReleaseRevision: 3, expectedSecurityRevision: 0, expectedImpactFingerprint: 'impact' },
  target: null, evidence: [], externalPipeline: null };
const preview = { requestId: 'request', revision: 2, status: 'approved', subjectId: 'context', previousFingerprint: 'old', fingerprint: 'renewed', packet,
  changes: [{ item: 'transition.expected_release_revision', before: 1, after: 3 }],
  removedEvidence: [{ source: 'skills_text', sourceId: 'expired-run', reason: 'evidence_expired' }] };
const mutateAsync = vi.fn();
const decideAsync = vi.fn();
beforeEach(() => {
  vi.clearAllMocks();
  useRenewReleaseRequest.mockReturnValue({ mutateAsync });
  useDecideReleaseRequest.mockReturnValue({ mutateAsync: decideAsync });
  decideAsync.mockResolvedValue({ id: 'request', revision: 3, status: 'approved' });
  mutateAsync.mockResolvedValue({ id: 'request', revision: 3, status: 'pending' });
});
afterEach(cleanup);

it('shows every changed precondition and removed evidence before creating the next revision', async () => {
  useRenewalPreview.mockReturnValue({ data: preview, isLoading: false, isError: false });
  const onClose = vi.fn();
  render(<ReleaseRenewalDialog open requestId="request" onClose={onClose} />);
  // Mapped label, cause and before/after values lead; the raw field path is a detail.
  const row = document.querySelector('[data-field="transition.expected_release_revision"]');
  expect(row).toBeVisible();
  expect(row).toHaveTextContent('Release revision');
  expect(row).toHaveTextContent('A label moved or label protection was edited.');
  expect(row.children[1]).toHaveTextContent('At approval');
  expect(row.children[2]).toHaveTextContent('Changed since approval');
  fireEvent.click(screen.getByRole('button', { name: 'Changed values' }));
  fireEvent.click(screen.getByRole('button', { name: 'Technical details' }));
  expect(screen.getByText(/"before": 1/)).toHaveTextContent('"after": 3');
  fireEvent.click(screen.getByRole('button', { name: 'Changed values' }));
  expect(screen.queryByText('transition.expected_release_revision')).not.toBeInTheDocument();
  expect(screen.getByText('Removed (expired)')).toBeVisible();
  expect(screen.queryByText(/packet/)).not.toBeInTheDocument();
  const create = screen.getByRole('button', { name: 'Approve again' });
  expect(create).toBeDisabled();
  fireEvent.click(screen.getByRole('checkbox', { name: "I've checked every change since the last approval, and it needs approving again." }));
  fireEvent.click(create);
  await waitFor(() => expect(onClose).toHaveBeenCalled());
  expect(mutateAsync).toHaveBeenCalledWith(expect.objectContaining({ requestId: 'request', revision: 2, expectedFingerprint: 'renewed' }));
});

it('creates nothing when the reviewed packet is still current', () => {
  useRenewalPreview.mockReturnValue({ data: undefined, isLoading: false, isError: true, error: { code: 'review_current' } });
  render(<ReleaseRenewalDialog open requestId="request" onClose={vi.fn()} />);
  expect(screen.getByText('This revision is still current. Nothing needs renewal.')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Approve again' })).toBeDisabled();
  expect(mutateAsync).not.toHaveBeenCalled();
});

it('offers explicit renewal only to the assigned reviewer of a stale packet', () => {
  const props = { scopeKey: 'org:ws:reviewer', state: 'ready', preview: { packet, fingerprint: 'old', current: false }, staleCode: 'release_conflict',
    request: { id: 'request', revision: 2, status: 'approved' }, review: { complete: false }, onRenew: vi.fn(), onRefresh: vi.fn() };
  const { rerender } = render(<ReleaseRequestPanel {...props} permissions={{ canDecide: true, canExecute: true }} />);
  expect(screen.getByText('Approval no longer applies. See what changed and renew the request.')).toBeVisible();
  // One title, one sentence, one action: Refresh lives inside "Why this request is paused".
  expect(screen.queryByRole('button', { name: 'Refresh' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Why this request is paused' }));
  expect(screen.getByRole('button', { name: 'Refresh' })).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Renew request' }));
  expect(props.onRenew).toHaveBeenCalled();
  expect(screen.getByRole('button', { name: /^Move production to v\d+$/ })).toBeDisabled();
  rerender(<ReleaseRequestPanel {...props} permissions={{ canDecide: false, canExecute: false }} />);
  expect(screen.queryByRole('button', { name: 'Renew request' })).not.toBeInTheDocument();
});

it('approves exactly the renewed revision it created, bound to the inspected fingerprint', async () => {
  useRenewalPreview.mockReturnValue({ data: preview, isLoading: false, isError: false });
  const onClose = vi.fn();
  const onApproved = vi.fn();
  render(<ReleaseRenewalDialog open requestId="request" onClose={onClose} onApproved={onApproved} />);
  expect(screen.getByText('No test results kept.')).toBeVisible();
  const approve = screen.getByRole('button', { name: 'Approve again' });
  expect(approve).toBeDisabled();
  fireEvent.click(screen.getByRole('checkbox', { name: "I've checked every change since the last approval, and it needs approving again." }));
  fireEvent.click(approve);
  await waitFor(() => expect(onApproved).toHaveBeenCalledWith({ revision: 3, fingerprint: 'renewed' }));
  expect(onClose).toHaveBeenCalled();
  expect(mutateAsync).toHaveBeenCalledWith(expect.objectContaining({ requestId: 'request', revision: 2, expectedFingerprint: 'renewed' }));
  expect(decideAsync).toHaveBeenCalledWith(expect.objectContaining({ requestId: 'request', revision: 3, expectedFingerprint: 'renewed', decision: 'approve' }));
  // Distinct idempotency keys: one renewal and one decision, never reused.
  expect(decideAsync.mock.calls[0][0].idempotencyKey).not.toBe(mutateAsync.mock.calls[0][0].idempotencyKey);
});

it('never approves when the server renews to an unexpected revision', async () => {
  useRenewalPreview.mockReturnValue({ data: preview, isLoading: false, isError: false });
  mutateAsync.mockResolvedValue({ id: 'request', revision: 5, status: 'pending' });
  const onApproved = vi.fn();
  render(<ReleaseRenewalDialog open requestId="request" onClose={vi.fn()} onApproved={onApproved} />);
  fireEvent.click(screen.getByRole('checkbox', { name: "I've checked every change since the last approval, and it needs approving again." }));
  fireEvent.click(screen.getByRole('button', { name: 'Approve again' }));
  expect(await screen.findByText(/couldn't confirm whether this went through/)).toBeVisible();
  expect(decideAsync).not.toHaveBeenCalled();
  expect(onApproved).not.toHaveBeenCalled();
});

it('keeps a reviewer attestation only for the same revision and fingerprint', () => {
  const props = { scopeKey: 'org:ws:reviewer', state: 'ready', preview: { packet, fingerprint: 'fp', current: true },
    request: { id: 'request', revision: 2, status: 'approved' }, review: { complete: true }, permissions: { canDecide: true, canExecute: true }, onExecute: vi.fn(), onDecide: vi.fn() };
  const { rerender } = render(<ReleaseRequestPanel {...props} reviewedIdentity="2:fp" onReviewedChange={vi.fn()} />);
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: /^Move production to v\d+$/ }));
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Move production', exact: true })).toBeEnabled();
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(screen.getByRole('button', { name: /^Move production to v\d+$/ })).toBeEnabled();
  rerender(<ReleaseRequestPanel {...props} request={{ ...props.request, revision: 3 }} reviewedIdentity="2:fp" onReviewedChange={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: /^Move production to v\d+$/ }));
  expect(screen.getByRole('checkbox', { name: /I've checked the changes/ })).not.toBeChecked();
  expect(screen.getByRole('button', { name: 'Move production', exact: true })).toBeDisabled();
});

it('clears a refusal from the previous revision when the renewed revision is approved', async () => {
  const props = { scopeKey: 'org:ws:reviewer', state: 'ready', preview: { packet, fingerprint: 'fp', current: true }, review: { complete: true },
    permissions: { canDecide: true, canExecute: true }, onDecide: vi.fn(), onRenew: vi.fn(),
    onExecute: vi.fn().mockRejectedValue({ statusCode: 409, code: 'release_conflict' }) };
  const { rerender } = render(<ReleaseRequestPanel {...props} request={{ id: 'request', revision: 2, status: 'approved' }} reviewedIdentity="2:fp" onReviewedChange={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: /^Move production to v\d+$/ }));
  fireEvent.click(await screen.findByRole('button', { name: 'Move production' }));
  expect(await screen.findByText('Approval no longer applies. See what changed and renew the request.')).toBeVisible();
  // The confirmation is still closing; wait until the page is interactive again.
  expect(await screen.findByRole('button', { name: 'Renew request' })).toBeVisible();
  rerender(<ReleaseRequestPanel {...props} preview={{ packet, fingerprint: 'fp2', current: true }} request={{ id: 'request', revision: 3, status: 'approved' }} reviewedIdentity={null} onReviewedChange={vi.fn()} />);
  expect(screen.queryByText(/Approval no longer applies/)).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Renew request' })).not.toBeInTheDocument();
});

it('lets the same mounted dialog renew a later revision after an earlier renewal', async () => {
  useRenewalPreview.mockReturnValue({ data: preview, isLoading: false, isError: false });
  const onClose = vi.fn();
  const onApproved = vi.fn();
  const { rerender } = render(<ReleaseRenewalDialog open requestId="request" onClose={onClose} onApproved={onApproved} />);
  fireEvent.click(screen.getByRole('checkbox', { name: "I've checked every change since the last approval, and it needs approving again." }));
  fireEvent.click(screen.getByRole('button', { name: 'Approve again' }));
  await waitFor(() => expect(onClose).toHaveBeenCalled());
  rerender(<ReleaseRenewalDialog open={false} requestId="request" onClose={onClose} onApproved={onApproved} />);
  // A later race: revision 3 now needs renewal to revision 4.
  useRenewalPreview.mockReturnValue({ data: { ...preview, revision: 3, fingerprint: 'renewed-again' }, isLoading: false, isError: false });
  mutateAsync.mockResolvedValue({ id: 'request', revision: 4, status: 'pending' });
  decideAsync.mockResolvedValue({ id: 'request', revision: 4, status: 'approved' });
  rerender(<ReleaseRenewalDialog open requestId="request" onClose={onClose} onApproved={onApproved} />);
  const approve = screen.getByRole('button', { name: 'Approve again' });
  expect(approve).toBeDisabled();
  fireEvent.click(screen.getByRole('checkbox', { name: "I've checked every change since the last approval, and it needs approving again." }));
  expect(approve).toBeEnabled();
  fireEvent.click(approve);
  await waitFor(() => expect(onApproved).toHaveBeenCalledWith({ revision: 4, fingerprint: 'renewed-again' }));
});

it('blocks a second submit for the revision it already renewed', async () => {
  useRenewalPreview.mockReturnValue({ data: preview, isLoading: false, isError: false });
  render(<ReleaseRenewalDialog open requestId="request" onClose={vi.fn()} />);
  fireEvent.click(screen.getByRole('checkbox', { name: "I've checked every change since the last approval, and it needs approving again." }));
  fireEvent.click(screen.getByRole('button', { name: 'Approve again' }));
  await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Approve again' })).toBeDisabled());
  expect(screen.getByRole('button', { name: 'Approve again' })).toBeDisabled();
});

it('renews without approval only after acknowledgment and retains the fingerprint', async () => {
  useRenewalPreview.mockReturnValue({ data: { ...preview, changes: [{ item: 'transition.expected_security_revision', before: 0, after: 2 }] } });
  render(<ReleaseRenewalDialog open requestId="request" onClose={vi.fn()} />);
  expect(screen.getByText('The release review rules changed after this request was approved, so the approval no longer applies. Review what changed, then approve again.')).toBeVisible();
  const renewOnly = screen.getByRole('button', { name: 'Renew and decide later' });
  expect(renewOnly).toBeDisabled();
  fireEvent.click(screen.getByRole('checkbox', { name: "I've checked every change since the last approval, and it needs approving again." }));
  fireEvent.click(renewOnly);
  await waitFor(() => expect(mutateAsync).toHaveBeenCalledWith(expect.objectContaining({ revision: 2, expectedFingerprint: 'renewed' })));
  expect(decideAsync).not.toHaveBeenCalled();
});

it('explains a restored policy only when its values and moved revision are reported', () => {
  useRenewalPreview.mockReturnValue({ data: { ...preview, changes: [
    { item: 'transition.expected_security_revision', before: 0, after: 4 },
    { item: 'security_policy', before: { protected: true }, after: { protected: true } },
  ] } });
  const { rerender } = render(<ReleaseRenewalDialog open requestId="request" onClose={vi.fn()} />);
  expect(screen.getByText("The security policy changed and was restored after approval. The earlier approval can't be reused.")).toBeVisible();
  useRenewalPreview.mockReturnValue({ data: { ...preview, changes: [{ item: 'transition.expected_security_revision', before: 0, after: 4 }] } });
  rerender(<ReleaseRenewalDialog open requestId="request" onClose={vi.fn()} />);
  expect(screen.getByText('Any policy edit after approval requires a new approval.')).toBeVisible();
  expect(screen.queryByText(/earlier approval can't be reused/)).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Renew and decide later' }).closest('.MuiDialogActions-root')).not.toBeNull();
  expect(screen.getByRole('button', { name: 'Cancel', exact: true })).toBeVisible();
  expect(screen.getByRole('button', { name: 'Close', exact: true })).toBeVisible();
});
