import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import ReleaseRequestPanel, { REQUESTER_ACKNOWLEDGMENT, reviewerAcknowledgment } from './ReleaseRequestPanel';
import { resetSessionAcknowledgments } from '../components/Acknowledgment';

// UXF-02: one tick per actor per decision. The requester and the reviewer
// each tick their own sentence; the final move asks no new tick when the same
// person ticked the same ack key (revision + fingerprint) in this session, and
// asks again after a renewal or when the request went stale. Every write is
// still bound to the same revision and fingerprint.

afterEach(() => { cleanup(); resetSessionAcknowledgments(); });

const packet = { subjectId: 'context', subjectKind: 'agent_context', candidateVersionId: 'version', candidateDigest: 'digest', evidence: [], target: null,
  transition: { label: 'production', expectedVersion: 1, version: 2 } };
const changes = [{ path: 'AGENTS.md', status: 'modified', fromText: 'a', toText: 'b' }, { path: 'skills/refund/SKILL.md', status: 'added', fromText: '', toText: 'c' }];
const REVIEWER = reviewerAcknowledgment(changes);
const request = (status, revision = 1) => ({ id: 'request', revision, status, name: 'support-agent', recipients: [{ id: 'reviewer', name: 'Robin Reviewer' }] });
const props = (overrides = {}) => ({
  scopeKey: 'scope', state: 'ready', preview: { packet, current: true, fingerprint: 'fp-1' }, review: { complete: true, changes },
  permissions: { canDecide: true, canExecute: true }, currentUserId: 'reviewer', onDecide: vi.fn().mockResolvedValue({}), onExecute: vi.fn().mockResolvedValue({}),
  ...overrides,
});

describe('ReleaseRequestPanel acknowledgments', () => {
  it('names each decision: the reviewer sentence counts the open changed files', () => {
    expect(REVIEWER).toBe("I've checked the 2 changed files, the evidence and what this release will affect.");
    expect(reviewerAcknowledgment(undefined)).toBe("I've checked the changes, the evidence and what this release will affect.");
    expect(REQUESTER_ACKNOWLEDGMENT).toBe("I've checked this version, where it's going and the evidence I'm attaching.");
  });

  it('the requester ticks once and the send carries the same fingerprint, named "Send request"', async () => {
    const onSubmit = vi.fn().mockResolvedValue({});
    render(<ReleaseRequestPanel {...props({ permissions: { canSubmit: true }, onSubmit, recipients: [{ id: 'reviewer', name: 'Robin Reviewer' }], initialRecipientId: 'reviewer' })} />);
    expect(screen.getAllByRole('checkbox')).toHaveLength(1);
    const send = screen.getByRole('button', { name: 'Send request' });
    expect(send).toBeDisabled();
    fireEvent.click(screen.getByRole('checkbox', { name: REQUESTER_ACKNOWLEDGMENT }));
    fireEvent.click(send);
    await waitFor(() => expect(onSubmit).toHaveBeenCalled());
    expect(onSubmit.mock.calls[0][0]).toMatchObject({ expectedFingerprint: 'fp-1', recipientIds: ['reviewer'], revision: 0 });
  });

  it('the final move asks no new tick for the same person and ack key', async () => {
    const onDecide = vi.fn().mockResolvedValue({});
    const view = render(<ReleaseRequestPanel {...props({ request: request('pending'), onDecide })} />);
    fireEvent.click(screen.getByRole('checkbox', { name: REVIEWER }));
    fireEvent.click(screen.getByRole('button', { name: 'Approve release' }));
    await waitFor(() => expect(onDecide).toHaveBeenCalled());
    expect(onDecide.mock.calls[0][0]).toMatchObject({ requestId: 'request', revision: 1, expectedFingerprint: 'fp-1', decision: 'approve' });
    // The server records the approval; the panel remounts as approved for the same revision and fingerprint.
    const onExecute = vi.fn().mockResolvedValue({});
    view.rerender(<ReleaseRequestPanel {...props({ request: request('approved'), onExecute })} />);
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
    expect(screen.getByText('You checked this release when you approved it.')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Release to production' }));
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).queryByRole('checkbox')).not.toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Move production' }));
    await waitFor(() => expect(onExecute).toHaveBeenCalled());
    expect(onExecute.mock.calls[0][0]).toMatchObject({ requestId: 'request', revision: 1, expectedFingerprint: 'fp-1' });
  });

  it('asks again in the confirmation for another person, or after a renewal changed the key', () => {
    // Ticked for revision 1 by the reviewer.
    const view = render(<ReleaseRequestPanel {...props({ request: request('pending') })} />);
    fireEvent.click(screen.getByRole('checkbox', { name: REVIEWER }));
    // Renewed and approved again: revision 2, a new fingerprint. The old tick does not carry.
    view.rerender(<ReleaseRequestPanel {...props({ request: request('approved', 2), preview: { packet, current: true, fingerprint: 'fp-2' } })} />);
    fireEvent.click(screen.getByRole('button', { name: 'Release to production' }));
    const dialog = screen.getByRole('dialog');
    const move = within(dialog).getByRole('button', { name: 'Move production' });
    expect(move).toBeDisabled();
    fireEvent.click(within(dialog).getByRole('checkbox', { name: REVIEWER }));
    expect(move).toBeEnabled();
    view.unmount();
    // Another person (an admin who did not tick) is asked in the confirmation.
    render(<ReleaseRequestPanel {...props({ request: request('approved'), currentUserId: 'admin' })} />);
    fireEvent.click(screen.getByRole('button', { name: 'Release to production' }));
    expect(within(screen.getByRole('dialog')).getByRole('button', { name: 'Move production' })).toBeDisabled();
  });

  it('forgets the tick when the request goes stale, so a later move asks again', () => {
    const view = render(<ReleaseRequestPanel {...props({ request: request('pending') })} />);
    fireEvent.click(screen.getByRole('checkbox', { name: REVIEWER }));
    view.rerender(<ReleaseRequestPanel {...props({ request: request('approved'), staleCode: 'release_conflict', onRenew: vi.fn() })} />);
    expect(screen.getByRole('button', { name: 'Renew request' })).toBeVisible();
    expect(screen.getByRole('button', { name: 'Release to production' })).toBeDisabled();
    view.unmount();
    // Same revision and fingerprint shown current again later in the session: asked again.
    render(<ReleaseRequestPanel {...props({ request: request('approved') })} />);
    expect(screen.queryByText('You checked this release when you approved it.')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Release to production' }));
    expect(within(screen.getByRole('dialog')).getByRole('button', { name: 'Move production' })).toBeDisabled();
  });

  it('keeps reviewer independence: an author cannot approve, and approval still needs the tick', () => {
    render(<ReleaseRequestPanel {...props({ request: request('pending'), permissions: { canDecide: false, canExecute: false } })} />);
    fireEvent.click(screen.getByRole('checkbox', { name: REVIEWER }));
    expect(screen.getByRole('button', { name: 'Approve release' })).toBeDisabled();
  });
});
