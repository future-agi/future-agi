import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import ReleaseRequestComposer, { ShareConsentDialog } from './ReleaseRequestComposer';
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
  // The page title states the move ("Production moved from v1 to v2"); the receipt is the caption, one time element and the table.
  expect(screen.getByText('Release receipt', { exact: true })).toBeVisible();
  expect(screen.queryByText('Production moved from v1 to v2')).not.toBeInTheDocument();
  expect(screen.getByText(/^· /)).toBeVisible();
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
  expect(await screen.findByText('Approval no longer applies. See what changed and renew the request.')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Approve release' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Why this request is paused' }));
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
  fireEvent.click(screen.getByRole('button', { name: /^Move production to v\d+$/ }));
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(screen.getByRole('button', { name: 'Move production', exact: true }));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  reject({ statusCode: 409, code: 'release_conflict' });
  expect(await screen.findByText('Approval no longer applies. See what changed and renew the request.')).toBeVisible();
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

it('keeps one evidence ID consistent across selection, consent, review, paused review and renewal', () => {
  const ref = { source: 'skills_text', sourceId: '5cae58ed', subjectId: 'skill', subjectKind: 'skill', candidateDigest: 'digest', configuration: { digest: 'config', text: {} }, annotationRevision: 2,
    status: 'completed', visibility: 'workspace', payloadExpiresAt: '2999-01-01', outcome: 'works_on_examples', counts: { cases: 1, graded: 2, planned: 2, regressed: 0 } };
  const evidencePacket = { ...packet, evidence: [ref] };
  useReviewOptions.mockReturnValue({ data: { targets: [], recipients: [], evidence: [ref] } });
  const composer = render(<MemoryRouter><ReleaseRequestComposer scopeKey="scope" prefill={{ kind: 'skill', subjectId: 'skill', candidate: { id: 'version', digest: 'digest', number: 2 }, evidence: ['skills_text:5cae58ed'] }} /></MemoryRouter>);
  expect(screen.getByText('Passed on test cases')).toBeVisible();
  expect(screen.getByText('1 case tested · 2 runs graded · 0 regressions')).toBeVisible();
  expect(screen.getByText('From your test')).toBeVisible();
  composer.unmount();

  // One private result is shared from its own row; the bulk button needs two or more.
  const consent = render(<ShareConsentDialog open share={[{ ...ref, visibility: 'private' }]} onShare={vi.fn()} onShared={vi.fn()} onClose={vi.fn()} />);
  expect(within(screen.getByRole('dialog')).getByText('Share 1 private result with the workspace?')).toBeVisible();
  expect(within(screen.getByRole('dialog')).getByText('Passed on test cases')).toBeVisible();
  expect(within(screen.getByRole('dialog')).getByText('1 case tested · 2 runs graded · 0 regressions')).toBeVisible();
  // The record is named by title with a Private badge; its ID is one hover away and never printed.
  expect(within(screen.getByRole('dialog')).getByText('Private', { exact: true })).toBeVisible();
  expect(screen.queryByText('5cae58ed')).not.toBeInTheDocument();
  expect(screen.getByTitle('5cae58ed')).toBeVisible();
  consent.unmount();

  const review = render(<ReleaseReviewBody packet={evidencePacket} evidence={[ref]} />);
  expect(screen.getByText('Passed on test cases')).toBeVisible();
  expect(screen.getByText('1 case tested · 2 runs graded · 0 regressions')).toBeVisible();
  const unloaded = { ...ref, resultState: 'not_loaded' };
  review.rerender(<ReleaseReviewBody packet={evidencePacket} evidence={[unloaded]} />);
  expect(screen.getByText('Not checked yet')).toBeVisible();
  expect(screen.getByText('Rechecked when you renew.')).toBeVisible();
  expect(screen.queryByText('Passed on test cases')).not.toBeInTheDocument();
  expect(screen.queryByText('Not recorded')).not.toBeInTheDocument();
  review.unmount();

  useRenewalPreview.mockReturnValue({ data: { revision: 2, fingerprint: 'renewed', packet: { ...evidencePacket, evidence: [unloaded] }, changes: [], removedEvidence: [] } });
  render(<ReleaseRenewalDialog open requestId="request" onClose={vi.fn()} />);
  // Renewal summarizes retained evidence without claiming the result was checked.
  expect(screen.getByText("1 test result kept; it'll be rechecked when you renew.")).toBeVisible();
  expect(screen.queryByText('Passed on test cases')).not.toBeInTheDocument();
  expect(screen.queryByText('Not recorded')).not.toBeInTheDocument();
});

// ---- UX iteration 3, REV1-REV3, REV7: one summary table, one reason, one action ----
const summaryPacket = { ...packet, subjectKind: 'agent_context', transition: { ...packet.transition, expectedImpactFingerprint: 'impact-fp' } };
const capabilities = { beforeDigest: 'a'.repeat(64), afterDigest: 'b'.repeat(64), changes: [] };
const impact = { following: 2, pinned: 1, unobserved: 0, moreAvailable: false };
const emptyChecks = { items: [], summary: { errors: 0, warnings: 0 } };
const summaryRows = () => within(screen.getByLabelText('Release summary'));

it('heads the release diff with the subject\'s own versions, never a bare "Production"', () => {
  const file = [{ path: 'AGENTS.md', status: 'modified', fromText: 'a', toText: 'b' }];
  const view = render(<ReleaseReviewBody packet={summaryPacket} changes={file} changesFrom={1} dependencyChanges={[]} checks={emptyChecks} capabilities={capabilities} impact={impact} />);
  expect(screen.getByRole('columnheader', { name: 'In production (context v1)' })).toBeVisible();
  expect(screen.getByRole('columnheader', { name: 'This release (context v2)' })).toBeVisible();
  expect(screen.queryByRole('columnheader', { name: 'Production' })).not.toBeInTheDocument();
  view.unmount();
  // A skill's own diff names the skill version; an unknown base names only the label.
  render(<ReleaseReviewBody packet={packet} changes={file} checks={emptyChecks} capabilities={capabilities} />);
  expect(screen.getByRole('columnheader', { name: 'In production' })).toBeVisible();
  expect(screen.getByRole('columnheader', { name: 'This release (v2)' })).toBeVisible();
});

it('names the affected apps in the summary and keeps the per-app counts one click away', () => {
  const apps = [{ id: 'b2', name: 'acme-support application', selector: { version: 2 }, reported: false }, { id: 'b1', name: 'acme-support production agent', selector: { label: 'production' }, reported: true }];
  render(<ReleaseReviewBody packet={summaryPacket} changes={[]} dependencyChanges={[]} checks={emptyChecks} capabilities={capabilities} impact={{ following: 1, pinned: 1, unobserved: 1, moreAvailable: false, apps }} />);
  expect(summaryRows().getByText("acme-support production agent follows production and moves v1 → v2. acme-support application is pinned to v2, so it already uses this version; it hasn't reported a version yet.")).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: /Apps affected/ }));
  expect(screen.getByText(/1 follower · 1 intentional pin · 1 application without observations/)).toBeVisible();
  expect(screen.getByText('impact-fp')).toBeInTheDocument();
});

it('states evidence, impact, permissions and files once in a key/value table, with the hashes inside What was compared and Technical details', () => {
  render(<ReleaseReviewBody packet={summaryPacket} changes={[{ path: 'AGENTS.md', status: 'modified', fromText: 'a', toText: 'b' }]} dependencyChanges={[]} checks={emptyChecks} capabilities={capabilities} impact={impact} />);
  ['Evidence', 'Who is affected', 'Permissions', 'Files'].forEach((label) => expect(summaryRows().getByText(label, { selector: 'dt' })).toBeVisible());
  expect(summaryRows().getByText('No test results attached. Not tested is not a pass.')).toBeVisible();
  expect(summaryRows().getByText('1 file changed · 0 linked skills changed')).toBeVisible();
  // Nothing below restates a summary fact.
  expect(screen.getAllByText('No permission changes found.')).toHaveLength(1);
  expect(screen.getAllByText("Of 3 connected apps: 2 move v1 → v2; 1 is pinned and doesn't move.").length).toBeGreaterThan(0);
  expect(screen.queryByText('Who is affected', { selector: 'h2,h3,h4,h5,h6,p,span' })).not.toBeInTheDocument();
  expect(screen.queryByText('No matching test evidence. Not tested is not a pass.')).not.toBeInTheDocument();
  // Engineering words and raw hashes are not primary content.
  ['Release identities', 'Impact counts', 'Generated files', 'Other checks', 'Locked digest'].forEach((text) => expect(screen.queryByText(text)).not.toBeInTheDocument());
  expect(screen.queryByText(/aaaaaaaaaaaa/)).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Apps affected' })).toBeVisible();
  expect(screen.getByRole('button', { name: 'Technical details' })).toBeVisible();
  ['Counts by app', 'Version details', 'Compared content'].forEach((text) => expect(screen.queryByRole('button', { name: text })).not.toBeInTheDocument());
  // The reviewer's tick covers what the release affects, so the compared fingerprints are open; each is named
  // ("Before", "Candidate") with its exact digest on hover or focus, never a hash as content.
  expect(screen.getByRole('button', { name: 'What was compared' })).toHaveAttribute('aria-expanded', 'true');
  expect(screen.getByText('No changes in recognized static declarations. This is not a safety certification.')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Copy full Before' })).toBeVisible();
  expect(screen.getByRole('button', { name: 'Copy full Candidate' })).toBeVisible();
  expect(screen.getByTitle(capabilities.beforeDigest)).toHaveTextContent('Before');
});
it('keeps the target configuration out of the summary and its digest under Technical details', () => {
  const target = { bindingId: 'binding', environmentId: null, revision: 2, configurationDigest: 'd'.repeat(64) };
  render(<ReleaseReviewBody packet={{ ...summaryPacket, target }} changes={[]} dependencyChanges={[]} checks={emptyChecks} capabilities={capabilities} impact={impact} />);
  // No summary row that only says "App configuration" (Fable P2): the target lives in Technical details.
  expect(summaryRows().queryByText('Application target')).not.toBeInTheDocument();
  expect(screen.queryByText('App configuration')).not.toBeInTheDocument();
  expect(screen.queryByText(/dddddddddddd/)).not.toBeInTheDocument();
  // The revision is a technical detail, not part of the summary line.
  expect(screen.queryByText(/App configuration r2/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Technical details' }));
  expect(screen.getByText('Target configuration r2')).toBeVisible();
});
it('never hides a missing permission comparison or unavailable checks behind the summary', () => {
  const retry = vi.fn();
  render(<ReleaseReviewBody packet={summaryPacket} changes={[]} dependencyChanges={[]} checks={undefined} capabilities={undefined} impact={impact} onRetryDependencies={retry} />);
  expect(summaryRows().getByText('Comparison unavailable. Review the file changes before deciding.')).toBeVisible();
  expect(screen.getByText('Permission comparison unavailable')).toBeVisible();
  // A failed load says so, with Retry; a review with no check rows shows no empty heading.
  expect(screen.getByText("Couldn't load checks.")).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  expect(retry).toHaveBeenCalledTimes(1);
  expect(screen.queryByText('Static checks')).not.toBeInTheDocument();
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
it('orders the pending decision as one footer: Add comment on the left, then the reason, Request changes and Approve release on the right', () => {
  render(<ReleaseRequestPanel scopeKey="scope" state="ready" request={{ id: 'request', revision: 1, status: 'pending' }} preview={{ packet, current: true, fingerprint: 'fp' }} review={{ complete: true }} permissions={{ canDecide: true }} onDecide={vi.fn()} />);
  const names = ['Add comment', 'Request changes', 'Approve release'];
  const buttons = names.map((name) => screen.getByRole('button', { name }));
  buttons.forEach((button, index) => { if (index) expect(buttons[index - 1].compareDocumentPosition(button) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy(); });
  expect(screen.getByRole('button', { name: 'Approve release' })).toHaveAccessibleDescription('Tick the acknowledgment to approve');
  expect(screen.getAllByText('Tick the acknowledgment to approve')).toHaveLength(1);
  const follows = (a, b) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
  const reason = screen.getByText('Tick the acknowledgment to approve');
  expect(follows(screen.getByRole('checkbox'), buttons[0])).toBe(true);
  expect(follows(buttons[0], reason)).toBe(true);
  expect(follows(reason, buttons[1])).toBe(true);
  expect(buttons[0]).toHaveClass('MuiButton-text');
  expect(buttons[1]).toHaveClass('MuiButton-outlined');
  expect(buttons[2]).toHaveClass('MuiButton-contained');
  expect(screen.getByRole('button', { name: 'Decision ↓' })).toBeVisible();
});
it('tells the assigned reviewer the request waits for their approval, and others who it waits for', () => {
  const pending = { id: 'request', revision: 1, status: 'pending', recipients: [{ id: 'reviewer-id', name: 'Robin Reviewer' }] };
  const view = (currentUserId) => <ReleaseRequestPanel scopeKey="scope" state="ready" request={pending} currentUserId={currentUserId} preview={{ packet, current: true, fingerprint: 'fp' }} review={{ complete: true }} permissions={{ canDecide: true }} onDecide={vi.fn()} />;
  const { rerender } = render(view('reviewer-id'));
  expect(screen.getByText('Waiting for your approval', { exact: true })).toBeVisible();
  expect(screen.queryByText(/Robin Reviewer/)).not.toBeInTheDocument();
  rerender(view('someone-else'));
  expect(screen.getByText("Waiting for Robin Reviewer's approval", { exact: true })).toBeVisible();
});
it('pauses a stale request with one title, one sentence, one action and Refresh inside the disclosure', () => {
  render(<ReleaseRequestPanel scopeKey="scope" state="ready" request={{ id: 'request', revision: 1, status: 'approved' }} preview={{ packet, current: false, fingerprint: 'fp' }} review={{ complete: true }} staleCode="release_conflict" permissions={{ canDecide: true, canExecute: true }} onRenew={vi.fn()} onRefresh={vi.fn()} />);
  expect(screen.getByText('Approval no longer applies. See what changed and renew the request.')).toBeVisible();
  expect(screen.queryByText(/This changed since you opened it/)).not.toBeInTheDocument();
  expect(screen.queryByText('Renew and approve this request before releasing.')).not.toBeInTheDocument();
  expect(screen.getAllByRole('button', { name: 'Renew request' })).toHaveLength(1);
  expect(screen.queryByRole('button', { name: 'Refresh' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Why this request is paused' }));
  expect(screen.getByRole('button', { name: 'Refresh' })).toBeVisible();
  const release = screen.getByRole('button', { name: /^Move production to v\d+$/ });
  expect(release).toBeDisabled();
  expect(release).toHaveAccessibleDescription('Renew the request first');
  // The notice lives in the page; the sticky decision bar stays one row (acknowledgment, reason, disabled release).
  const bar = document.querySelector('[data-sticky-footer]');
  expect(bar).not.toBeNull();
  expect(bar.contains(screen.getByText('Approval no longer applies. See what changed and renew the request.'))).toBe(false);
  expect(bar.contains(screen.getByRole('button', { name: 'Renew request' }))).toBe(false);
  expect(bar.contains(screen.getByRole('button', { name: 'Why this request is paused' }))).toBe(false);
  expect(bar.contains(release)).toBe(true);
  expect(bar.contains(screen.getByText('Renew the request first'))).toBe(true);
  // An approved request asks acknowledgment only inside an allowed move confirmation.
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
});
it('keeps the renewal approval line free of revision numbers', () => {
  useRenewalPreview.mockReturnValue({ data: { revision: 2, fingerprint: 'renewed', packet, changes: [], removedEvidence: [] } });
  render(<ReleaseRenewalDialog open requestId="request" onClose={vi.fn()} />);
  expect(screen.getByText('Approving again records a new approval. Renew and decide later keeps the request open.')).toBeVisible();
  expect(screen.queryByText(/Approving creates revision/)).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Approve again' })).toBeVisible();
});
it('renders the receipt as one table of facts and a clear action hierarchy', () => {
  const receipt = { requestId: 'request', decisionId: 'decision', executedBy: 'executor', executedByName: 'Erin Releaser', approvedByName: 'Robin Reviewer', recordedAt: '2026-11-10T12:00:00Z', subjectKind: 'agent_context', subjectId: 'context', previousVersion: 1, currentnessAtAction: {}, postMoveState: { label: 'production', version: 2 } };
  render(<ReleaseReceipt receipt={receipt} />);
  const table = within(screen.getByLabelText('Release receipt details'));
  ['Before the move', 'After the move', 'Approved by', 'Released by'].forEach((label) => expect(table.getByText(label, { selector: 'dt' })).toBeVisible());
  expect(table.queryByText('Recorded', { selector: 'dt' })).not.toBeInTheDocument();
  expect(screen.queryByText('Released', { exact: true })).not.toBeInTheDocument();
  // The next job for a context is confirming apps picked the release up, so that is the one primary.
  expect(screen.getByRole('link', { name: 'Check apps picked up v2' })).toHaveClass('MuiButton-contained');
  expect(screen.getByRole('link', { name: 'Check apps picked up v2' })).toHaveAttribute('href', expect.stringContaining('?tab=consumers'));
  expect(screen.getByRole('link', { name: 'View version' })).toHaveClass('MuiButton-outlined');
  expect(screen.getByRole('link', { name: 'View release history' })).toHaveClass('MuiButton-outlined');
  expect(screen.queryByRole('link', { name: 'Watch delivery' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Copy receipt link' })).toHaveClass('MuiButton-text');
});
