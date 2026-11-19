import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import ReleaseRenewalDialog, { NOT_RECORDED, RULES_CHANGED_LEAD, keptEvidenceLine, renewalLead, ruleChanges } from './ReleaseRenewalDialog';
import { useDecideReleaseRequest, useRenewalPreview, useRenewReleaseRequest } from 'src/api/skills/review-production';
import { RETIRED_TERMS } from '../utils/terms';

// UXF-10: the renewal dialog leads with what happened, shows the changed rule
// values (or says they weren't recorded), keeps the evidence to one line and
// never says "candidate". The approval is still bound to the inspected
// renewal fingerprint.

vi.mock('src/api/skills/review-production', () => ({ useRenewalPreview: vi.fn(), useRenewReleaseRequest: vi.fn(), useDecideReleaseRequest: vi.fn() }));

const TICK = "I've checked every change since the last approval, and it needs approving again.";
const packet = { schema: 1, subjectKind: 'agent_context', subjectId: 'context', candidateVersionId: 'candidate', candidateDigest: 'sha256:candidate',
  transition: { label: 'production', version: 2, expectedVersion: 1, expectedReleaseRevision: 3, expectedSecurityRevision: 1, expectedImpactFingerprint: 'impact' },
  target: null, evidence: [{ source: 'skills_text', sourceId: 'kept-1' }, { source: 'skills_text', sourceId: 'kept-2' }], externalPipeline: null };
const base = { requestId: 'request', revision: 2, status: 'approved', subjectId: 'context', previousFingerprint: 'old', fingerprint: 'renewed', packet, removedEvidence: [] };
const security = { item: 'transition.expected_security_revision', before: 0, after: 1 };
const protection = { field: 'protected_labels', before: ['production'], after: ['production', 'staging'], changedBy: 'Priya Shah', changedAt: '2026-11-16T10:00:00Z', recorded: true };
const unrecorded = { field: 'security_rules', before: null, after: null, changedBy: null, changedAt: null, recorded: false };

const renew = vi.fn();
const decide = vi.fn();
beforeEach(() => {
  vi.clearAllMocks();
  useRenewReleaseRequest.mockReturnValue({ mutateAsync: renew });
  useDecideReleaseRequest.mockReturnValue({ mutateAsync: decide });
  renew.mockResolvedValue({ id: 'request', revision: 3, status: 'pending' });
  decide.mockResolvedValue({ id: 'request', revision: 3, status: 'approved' });
});
afterEach(cleanup);

const show = (value) => {
  useRenewalPreview.mockReturnValue({ data: value, isLoading: false, isError: false });
  return render(<ReleaseRenewalDialog open requestId="request" onClose={vi.fn()} />);
};

describe('ReleaseRenewalDialog', () => {
  it('leads with the rule change and shows each changed value with who and when', () => {
    show({ ...base, changes: [security], policyChanges: [protection] });
    expect(screen.getByText(RULES_CHANGED_LEAD)).toBeVisible();
    // "Changed values" is open while there is a rule change to read.
    const row = document.querySelector('[data-rule-field="protected_labels"]');
    expect(row).toBeVisible();
    expect(row).toHaveTextContent('Protected labels');
    expect(row).toHaveTextContent('Production → Production, Staging');
    expect(row).toHaveTextContent('Changed by Priya Shah');
    expect(screen.queryByText(NOT_RECORDED)).not.toBeInTheDocument();
  });

  it('says the values at approval were not recorded instead of inventing them', () => {
    show({ ...base, changes: [security], policyChanges: [unrecorded] });
    expect(screen.getByText(RULES_CHANGED_LEAD)).toBeVisible();
    const row = document.querySelector('[data-rule-field="security_rules"]');
    expect(within(row).getByText(NOT_RECORDED)).toBeVisible();
    expect(row).toHaveTextContent('Security rules');
  });

  it('still shows a not-recorded row when an older server sends no rule list', () => {
    show({ ...base, changes: [security], policyChanges: null });
    expect(within(document.querySelector('[data-rule-field="security_rules"]')).getByText(NOT_RECORDED)).toBeVisible();
    expect(ruleChanges({ changes: [{ item: 'transition.expected_release_revision', before: 1, after: 2 }], policyChanges: null })).toEqual([]);
  });

  it('does not claim the rules changed when only a label moved', () => {
    show({ ...base, changes: [{ item: 'transition.expected_release_revision', before: 1, after: 3 }], policyChanges: [] });
    expect(screen.queryByText(RULES_CHANGED_LEAD)).not.toBeInTheDocument();
    expect(screen.getByText(renewalLead('approved', false))).toBeVisible();
  });

  it('keeps the evidence to one line, lists removed results and never says "candidate"', () => {
    show({ ...base, changes: [security], policyChanges: [protection], removedEvidence: [{ source: 'skills_text', sourceId: 'expired-run', reason: 'evidence_expired' }] });
    expect(screen.getByTestId('renewal-evidence')).toHaveTextContent("2 test results kept; they'll be rechecked when you renew.");
    expect(screen.getByText('Removed (expired)')).toBeVisible();
    expect(screen.queryByRole('table', { name: 'Test evidence' })).not.toBeInTheDocument();
    const text = document.body.textContent;
    expect(text).not.toMatch(/candidate/i);
    for (const retired of RETIRED_TERMS) expect(text).not.toContain(retired);
    expect(keptEvidenceLine(1)).toBe("1 test result kept; it'll be rechecked when you renew.");
    expect(keptEvidenceLine(0)).toBe('No test results kept.');
  });

  it('puts Cancel, "Renew and decide later" and "Approve again" in that order on the right', () => {
    show({ ...base, changes: [security], policyChanges: [protection] });
    const buttons = screen.getAllByRole('button').filter((button) => ['Cancel', 'Renew and decide later', 'Approve again'].includes(button.textContent));
    expect(buttons.map((button) => button.textContent)).toEqual(['Cancel', 'Renew and decide later', 'Approve again']);
    expect(screen.getByRole('heading', { name: 'Renew request' })).toBeVisible();
  });

  it('approves only after the one tick, bound to the inspected fingerprint, and asks again when it changes', async () => {
    const view = show({ ...base, changes: [security], policyChanges: [protection] });
    const approve = screen.getByRole('button', { name: 'Approve again' });
    expect(approve).toBeDisabled();
    fireEvent.click(screen.getByRole('checkbox', { name: TICK }));
    expect(approve).toBeEnabled();
    // A different renewal (the server moved again) clears the tick.
    useRenewalPreview.mockReturnValue({ data: { ...base, fingerprint: 'renewed-again', changes: [security], policyChanges: [protection] }, isLoading: false, isError: false });
    view.rerender(<ReleaseRenewalDialog open requestId="request" onClose={vi.fn()} />);
    expect(screen.getByRole('checkbox', { name: TICK })).not.toBeChecked();
    expect(screen.getByRole('button', { name: 'Approve again' })).toBeDisabled();
    fireEvent.click(screen.getByRole('checkbox', { name: TICK }));
    fireEvent.click(screen.getByRole('button', { name: 'Approve again' }));
    await waitFor(() => expect(decide).toHaveBeenCalled());
    expect(renew).toHaveBeenCalledWith(expect.objectContaining({ requestId: 'request', revision: 2, expectedFingerprint: 'renewed-again' }));
    expect(decide).toHaveBeenCalledWith(expect.objectContaining({ revision: 3, expectedFingerprint: 'renewed-again', decision: 'approve' }));
  });
});
