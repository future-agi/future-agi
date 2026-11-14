import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import ReleaseRequestComposer, { ShareAllEvidence } from './ReleaseRequestComposer';
import ReleaseReviewBody from './ReleaseReviewBody';
import ReleaseRenewalDialog from './ReleaseRenewalDialog';
import { useReviewOptions } from 'src/api/skills/review-authoring';
import { useRenewalPreview } from 'src/api/skills/review-production';

vi.mock('src/api/skills/testing', async (load) => ({ ...(await load()), useSkillV11Scope: () => ({ scope: { userId: 'author' } }) }));
vi.mock('src/api/skills/review-authoring', () => ({
  useReviewSubjects: () => ({ data: { results: [], total_pages: 1 } }),
  useReviewCandidates: () => ({ data: { results: [], total_pages: 1 } }),
  useReviewOptions: vi.fn(), usePrepareReleaseReview: () => ({}), useSubmitReleaseReview: () => ({}), useShareReleaseEvidence: () => ({}),
}));
vi.mock('src/api/skills/review-production', async (load) => ({ ...(await load()), useRenewalPreview: vi.fn(), useRenewReleaseRequest: () => ({}), useDecideReleaseRequest: () => ({}) }));
import { mapRequest, mapReleaseReceipt } from 'src/api/skills/review-production';
import ReleaseRequestPanel from './ReleaseRequestPanel';
import ReleaseReceipt from './ReleaseReceipt';
import { relativeReviewTime, reviewPersonName } from '../utils/review-display';

const packet = { subjectId: 'skill', subjectKind: 'skill', candidateVersionId: 'version', candidateDigest: 'digest', evidence: [], target: null, transition: { label: 'production', expectedVersion: 1, version: 2 } };
afterEach(cleanup);
it('renders author and assigned reviewer names mapped from the server, not their IDs', () => {
  const request = mapRequest({ id: 'request', author_id: 'author-id', author_name: 'Alex Author', name: 'Support agent', revision: 1, status: 'pending', recipients: [{ id: 'reviewer-id', name: 'Robin Reviewer' }] });
  render(<ReleaseRequestPanel scopeKey="scope" state="ready" request={request} preview={{ packet, current: true, fingerprint: 'fp' }} review={{ complete: true }} permissions={{ canDecide: true }} onDecide={vi.fn()} />);
  expect(screen.getByText(/Submitted by Alex Author/)).toBeVisible();
  expect(screen.getByText("Waiting for Robin Reviewer's approval", { exact: true })).toBeVisible();
  expect(screen.queryByText(/Unavailable member/)).not.toBeInTheDocument();
});
it('does not mistake missing recipient metadata for an unassigned request', () => {
  render(<ReleaseRequestPanel scopeKey="scope" state="ready" request={{ id: 'request', revision: 1, status: 'pending' }} preview={{ packet, current: true, fingerprint: 'fp' }} review={{ complete: true }} permissions={{ canDecide: true }} onDecide={vi.fn()} />);
  expect(screen.getByText("Waiting for the assigned reviewer's approval", { exact: true })).toBeVisible();
  expect(screen.queryByText(/Reviewer not assigned/)).not.toBeInTheDocument();
});
it('maps recorded execution time, approver, executor and both versions into the receipt', () => {
  const receipt = mapReleaseReceipt({ request_id: 'request', executed_by: 'executor-id', executed_by_name: 'Erin Releaser', approved_by_name: 'Robin Reviewer', executed_at: '2026-11-10T12:00:00Z', previous_version: 1, name: 'Support agent', currentness_at_action: {}, post_move_state: { label: 'production', version: 2 } });
  render(<ReleaseReceipt receipt={receipt} />);
  expect(screen.getByText('Production moved from v1 to v2')).toBeVisible();
  // Label/value rows in one table, not mixed sentences; no duplicate "Released" badge.
  const row = (label) => screen.getByText(label, { selector: 'dt' }).parentElement;
  expect(row('Released by')).toHaveTextContent('Erin Releaser');
  expect(row('Approved by')).toHaveTextContent('Robin Reviewer');
  expect(row('Before the move')).toHaveTextContent('Production v1');
  expect(row('After the move')).toHaveTextContent('Production v2');
  expect(screen.queryByText('Released', { exact: true })).not.toBeInTheDocument();
  expect(receipt.recordedAt).toBe('2026-11-10T12:00:00Z');
});
it('keeps approval and release paused after a stale refusal until explicit refresh', async () => {
  const decide = vi.fn().mockRejectedValue({ statusCode: 409, code: 'review_stale' });
  const refresh = vi.fn().mockResolvedValue({ isError: false });
  render(<ReleaseRequestPanel scopeKey="scope" state="ready" request={{ id: 'request', revision: 1, status: 'pending' }} preview={{ packet, current: true, fingerprint: 'fp' }} review={{ complete: true }} permissions={{ canDecide: true }} onDecide={decide} onRefresh={refresh} />);
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(screen.getByRole('button', { name: 'Approve release' }));
  expect(await screen.findByText('Approval no longer applies. Review the change and renew the request.')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Approve release' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Why this review is paused' }));
  fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
  expect(refresh).toHaveBeenCalledTimes(1);
});
it('chooses relative-time units by magnitude and does not infer deleted membership', () => {
  const now = Date.parse('2026-11-12T12:00:00Z');
  expect(relativeReviewTime('2026-11-09T12:00:00Z', now)).toBe('3 days ago');
  expect(relativeReviewTime('2026-11-12T10:00:00Z', now)).toBe('2 hours ago');
  expect(relativeReviewTime('2026-11-12T11:55:00Z', now)).toBe('5 minutes ago');
  expect(reviewPersonName(null, false)).not.toBe('Former member');
  expect(reviewPersonName(null, true)).toBe('Former member');
});

it('closes the move confirmation before a stale response can open renewal', async () => {
  let reject;
  const execute = vi.fn(() => new Promise((resolve, fail) => { reject = fail; }));
  render(<ReleaseRequestPanel scopeKey="scope" state="ready" request={{ id: 'request', revision: 1, status: 'approved' }} preview={{ packet, current: true, fingerprint: 'fp' }} review={{ complete: true }} permissions={{ canExecute: true }} onExecute={execute} />);
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(screen.getByRole('button', { name: 'Release to production' }));
  fireEvent.click(screen.getByRole('button', { name: 'Move production', exact: true }));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  reject({ statusCode: 409, code: 'release_conflict' });
  expect(await screen.findByText('Approval no longer applies. Review the change and renew the request.')).toBeVisible();
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

it('keeps one evidence ID consistent across selection, consent, review, paused review and renewal', () => {
  const ref = { source: 'skills_text', sourceId: '5cae58ed', subjectId: 'skill', subjectKind: 'skill', candidateDigest: 'digest', configuration: { digest: 'config', text: {} }, annotationRevision: 2,
    status: 'completed', visibility: 'workspace', payloadExpiresAt: '2999-01-01', outcome: 'works_on_examples', counts: { cases: 1, graded: 2, planned: 2, regressed: 0 } };
  const evidencePacket = { ...packet, evidence: [ref] };
  useReviewOptions.mockReturnValue({ data: { targets: [], recipients: [], evidence: [ref] } });
  const composer = render(<MemoryRouter><ReleaseRequestComposer scopeKey="scope" prefill={{ kind: 'skill', subjectId: 'skill', candidate: { id: 'version', digest: 'digest', number: 2 }, evidence: ['skills_text:5cae58ed'] }} /></MemoryRouter>);
  expect(screen.getByText('Passed on examples')).toBeVisible();
  expect(screen.getByText('1 case tested · 2 runs graded · 0 regressions')).toBeVisible();
  expect(screen.getByText('From your test')).toBeVisible();
  composer.unmount();

  const consent = render(<ShareAllEvidence packet={packet} evidence={[{ ...ref, visibility: 'private' }]} onShare={vi.fn()} onShared={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: 'Share and attach 1 result' }));
  expect(within(screen.getByRole('dialog')).getByText('Passed on examples')).toBeVisible();
  expect(within(screen.getByRole('dialog')).getByText('1 case tested · 2 runs graded · 0 regressions')).toBeVisible();
  // The record is named by title with a Private badge; its ID sits under Details.
  expect(within(screen.getByRole('dialog')).getByText('Private', { exact: true })).toBeVisible();
  expect(screen.queryByTitle('5cae58ed')).not.toBeInTheDocument();
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Details' }));
  expect(screen.getByTitle('5cae58ed')).toBeVisible();
  consent.unmount();

  const review = render(<ReleaseReviewBody packet={evidencePacket} evidence={[ref]} />);
  expect(screen.getByText('Passed on examples')).toBeVisible();
  expect(screen.getByText('1 case tested · 2 runs graded · 0 regressions')).toBeVisible();
  const unloaded = { ...ref, resultState: 'not_loaded' };
  review.rerender(<ReleaseReviewBody packet={evidencePacket} evidence={[unloaded]} />);
  expect(screen.getByText('Not loaded')).toBeVisible();
  expect(screen.getByText('Renew the review to recheck this result.')).toBeVisible();
  expect(screen.queryByText('Passed on examples')).not.toBeInTheDocument();
  expect(screen.queryByText('Not recorded')).not.toBeInTheDocument();
  review.unmount();

  useRenewalPreview.mockReturnValue({ data: { revision: 2, fingerprint: 'renewed', packet: { ...evidencePacket, evidence: [unloaded] }, changes: [], removedEvidence: [] } });
  render(<ReleaseRenewalDialog open requestId="request" onClose={vi.fn()} />);
  expect(screen.getByText('Not loaded')).toBeVisible();
  expect(screen.getByText('Renew the review to recheck this result.')).toBeVisible();
  expect(screen.getByTitle('5cae58ed')).toBeVisible();
  expect(screen.queryByText('Not recorded')).not.toBeInTheDocument();
});

// ---- UX iteration 3, REV1-REV3, REV7: one summary table, one reason, one action ----
const summaryPacket = { ...packet, subjectKind: 'agent_context', transition: { ...packet.transition, expectedImpactFingerprint: 'impact-fp' } };
const capabilities = { beforeDigest: 'a'.repeat(64), afterDigest: 'b'.repeat(64), changes: [] };
const impact = { following: 2, pinned: 1, unobserved: 0, moreAvailable: false };
const emptyChecks = { items: [], summary: { errors: 0, warnings: 0 } };
const summaryRows = () => within(screen.getByLabelText('Release summary'));

it('states evidence, impact, permissions and files once in a key/value table, with the hashes inside Compared content', () => {
  render(<ReleaseReviewBody packet={summaryPacket} changes={[{ path: 'AGENTS.md', status: 'modified', fromText: 'a', toText: 'b' }]} dependencyChanges={[]} checks={emptyChecks} capabilities={capabilities} impact={impact} />);
  ['Evidence', 'Who is affected', 'Permissions', 'Files'].forEach((label) => expect(summaryRows().getByText(label, { selector: 'dt' })).toBeVisible());
  expect(summaryRows().getByText('No test results attached. Not tested is not a pass.')).toBeVisible();
  expect(summaryRows().getByText('1 file changed · 0 linked skills changed')).toBeVisible();
  // Nothing below restates a summary fact.
  expect(screen.getAllByText('No permission changes found.')).toHaveLength(1);
  expect(screen.getAllByText(/connected consumer|follow/i).length).toBeGreaterThan(0);
  expect(screen.queryByText('Who is affected', { selector: 'h2,h3,h4,h5,h6,p,span' })).not.toBeInTheDocument();
  expect(screen.queryByText('No matching test evidence. Not tested is not a pass.')).not.toBeInTheDocument();
  // Engineering words and raw hashes are not primary content.
  ['Release identities', 'Impact counts', 'Generated files', 'Other checks', 'Locked digest'].forEach((text) => expect(screen.queryByText(text)).not.toBeInTheDocument());
  expect(screen.queryByText(/aaaaaaaaaaaa/)).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Counts by app' })).toBeVisible();
  expect(screen.getByRole('button', { name: 'Version details' })).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Compared content' }));
  expect(screen.getByText('No changes in recognized static declarations. This is not a safety certification.')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Copy full Before' })).toBeVisible();
  expect(screen.getByRole('button', { name: 'Copy full Candidate' })).toBeVisible();
});
it('never hides a missing permission comparison or unavailable checks behind the summary', () => {
  render(<ReleaseReviewBody packet={summaryPacket} changes={[]} dependencyChanges={[]} checks={undefined} capabilities={undefined} impact={impact} />);
  expect(summaryRows().getByText('Comparison unavailable. Review the file changes before deciding.')).toBeVisible();
  expect(screen.getByText('Permission comparison unavailable')).toBeVisible();
  expect(screen.getByText('Checks unavailable. This is not a successful check.')).toBeVisible();
});
it('lists linked skills with a Version column that carries the locked version, not a digest column', () => {
  const dependency = { mount: 'skills/refund-policy', before: { version: 1 }, after: { version: 2, digest: 'c'.repeat(64), requested: { label: 'production' } } };
  render(<ReleaseReviewBody packet={summaryPacket} changes={[]} dependencyChanges={[dependency]} checks={emptyChecks} capabilities={capabilities} impact={impact} />);
  expect(screen.getByRole('columnheader', { name: 'Version' })).toBeVisible();
  expect(screen.queryByRole('columnheader', { name: 'Digest' })).not.toBeInTheDocument();
  expect(screen.queryByText('Locked digest')).not.toBeInTheDocument();
  expect(screen.getByText('v2')).toBeVisible();
});
it('shows the result count the author can still attach when none is attached', () => {
  render(<ReleaseReviewBody packet={summaryPacket} changes={[]} dependencyChanges={[]} checks={emptyChecks} capabilities={capabilities} impact={impact} availableEvidence={2} />);
  expect(summaryRows().getByText('No test results attached (2 available).')).toBeVisible();
  cleanup();
  render(<ReleaseReviewBody packet={summaryPacket} changes={[]} dependencyChanges={[]} checks={emptyChecks} capabilities={capabilities} impact={impact} availableEvidence={0} />);
  expect(summaryRows().getByText('No matching test evidence. Not tested is not a pass.')).toBeVisible();
});
it('orders the pending decision Add comment, Request changes, Approve release, with one reason under the primary', () => {
  render(<ReleaseRequestPanel scopeKey="scope" state="ready" request={{ id: 'request', revision: 1, status: 'pending' }} preview={{ packet, current: true, fingerprint: 'fp' }} review={{ complete: true }} permissions={{ canDecide: true }} onDecide={vi.fn()} />);
  const names = ['Add comment', 'Request changes', 'Approve release'];
  const buttons = names.map((name) => screen.getByRole('button', { name }));
  buttons.forEach((button, index) => { if (index) expect(buttons[index - 1].compareDocumentPosition(button) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy(); });
  expect(screen.getByRole('button', { name: 'Approve release' })).toHaveAccessibleDescription('Tick the acknowledgment to approve');
  expect(screen.getAllByText('Tick the acknowledgment to approve')).toHaveLength(1);
});
it('pauses a stale request with one title, one sentence, one action and Refresh inside the disclosure', () => {
  render(<ReleaseRequestPanel scopeKey="scope" state="ready" request={{ id: 'request', revision: 1, status: 'approved' }} preview={{ packet, current: false, fingerprint: 'fp' }} review={{ complete: true }} staleCode="release_conflict" permissions={{ canDecide: true, canExecute: true }} onRenew={vi.fn()} onRefresh={vi.fn()} />);
  expect(screen.getByText('Approval no longer applies. Review the change and renew the request.')).toBeVisible();
  expect(screen.queryByText(/This changed since you opened it/)).not.toBeInTheDocument();
  expect(screen.queryByText('Renew and approve this request before releasing.')).not.toBeInTheDocument();
  expect(screen.getAllByRole('button', { name: 'Renew review' })).toHaveLength(1);
  expect(screen.queryByRole('button', { name: 'Refresh' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Why this review is paused' }));
  expect(screen.getByRole('button', { name: 'Refresh' })).toBeVisible();
  const release = screen.getByRole('button', { name: 'Release to production' });
  expect(release).toBeDisabled();
  expect(release).toHaveAccessibleDescription('Renew the review first');
});
it('keeps the renewal approval line free of revision numbers', () => {
  useRenewalPreview.mockReturnValue({ data: { revision: 2, fingerprint: 'renewed', packet, changes: [], removedEvidence: [] } });
  render(<ReleaseRenewalDialog open requestId="request" onClose={vi.fn()} />);
  expect(screen.getByText('Approving records a new approval for this request.')).toBeVisible();
  expect(screen.queryByText(/Approving creates revision/)).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Approve renewed revision' })).toBeVisible();
});
it('renders the receipt as one table of facts and a clear action hierarchy', () => {
  const receipt = { requestId: 'request', decisionId: 'decision', executedBy: 'executor', executedByName: 'Erin Releaser', approvedByName: 'Robin Reviewer', recordedAt: '2026-11-10T12:00:00Z', subjectKind: 'agent_context', subjectId: 'context', previousVersion: 1, currentnessAtAction: {}, postMoveState: { label: 'production', version: 2 } };
  render(<ReleaseReceipt receipt={receipt} />);
  const table = within(screen.getByLabelText('Release receipt details'));
  ['Before the move', 'After the move', 'Approved by', 'Released by', 'Recorded'].forEach((label) => expect(table.getByText(label, { selector: 'dt' })).toBeVisible());
  expect(screen.queryByText('Released', { exact: true })).not.toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'View version' })).toHaveClass('MuiButton-contained');
  expect(screen.getByRole('link', { name: 'View release history' })).toHaveClass('MuiButton-outlined');
  expect(screen.getByRole('link', { name: 'Watch delivery' })).toHaveClass('MuiButton-text');
  expect(screen.getByRole('button', { name: 'Copy receipt link' })).toHaveClass('MuiButton-text');
});
