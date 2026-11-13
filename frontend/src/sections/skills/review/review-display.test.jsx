import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
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
  expect(screen.getByText(/Approval pending · Robin Reviewer/)).toBeVisible();
  expect(screen.queryByText(/Unavailable member/)).not.toBeInTheDocument();
});
it('maps recorded execution time, approver, executor and both versions into the receipt', () => {
  const receipt = mapReleaseReceipt({ request_id: 'request', executed_by: 'executor-id', executed_by_name: 'Erin Releaser', approved_by_name: 'Robin Reviewer', executed_at: '2026-11-10T12:00:00Z', previous_version: 1, name: 'Support agent', currentness_at_action: {}, post_move_state: { label: 'production', version: 2 } });
  render(<ReleaseReceipt receipt={receipt} />);
  expect(screen.getByText('Production moved from v1 to v2')).toBeVisible();
  expect(screen.getByText(/Released by Erin Releaser/)).toHaveTextContent('Approved by Robin Reviewer');
  expect(receipt.recordedAt).toBe('2026-11-10T12:00:00Z');
});
it('keeps approval and release paused after a stale refusal until explicit refresh', async () => {
  const decide = vi.fn().mockRejectedValue({ statusCode: 409, code: 'review_stale' });
  const refresh = vi.fn().mockResolvedValue({ isError: false });
  render(<ReleaseRequestPanel scopeKey="scope" state="ready" request={{ id: 'request', revision: 1, status: 'pending' }} preview={{ packet, current: true, fingerprint: 'fp' }} review={{ complete: true }} permissions={{ canDecide: true }} onDecide={decide} onRefresh={refresh} />);
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(screen.getByRole('button', { name: 'Approve release' }));
  expect(await screen.findByText('This changed since you opened it. Refresh and review again.')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Approve release' })).toBeDisabled();
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
  expect(await screen.findByText('This changed since you opened it. Refresh and review again.')).toBeVisible();
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});
