import { useState } from 'react';
import PropTypes from 'prop-types';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import ContextCampaignList from '../../contexts/ContextCampaignList';
import ContextCampaignReview from '../../contexts/ContextCampaignReview';
import ContextCampaignTargetDrawer from '../../contexts/ContextCampaignTargetDrawer';

const campaign = { id: 'campaign', name: 'Synthetic policy rollout', revision: 3, status: 'draft', targetCount: 1, manifestDigest: 'sha256:manifest', enabled: true, canAuthorize: true };
const target = {
  bindingId: 'binding', name: 'synthetic-repository', bindingRevision: 1,
  selector: { label: 'production' }, desiredVersion: 4, desiredVersionId: 'version', desiredDigest: 'sha256:release',
  repository: { repositoryId: '100', defaultBranch: 'main', projectScope: '' },
  planDigest: 'sha256:plan', decision: 'pending', reason: '', prState: 'not_started', mergeState: 'unknown', attempt: null,
  plan: { repositoryId: '100', baseCommit: 'a'.repeat(40), previousLockDigest: null, materializationDigest: 'sha256:materialization', localReviewRequired: true, blockers: [],
    changes: [{ path: 'AGENTS.md', action: 'replace', beforeSha256: 'a'.repeat(64), afterSha256: 'b'.repeat(64), ownership: 'adopted', diffPreview: 'Keep local instructions. Add managed policy.' }] },
};
const observations = { data: { installed: null, discovered: null, delivered: null } };
function Harness({ value = campaign, targets = [target], onAuthorize = vi.fn().mockResolvedValue({}), onStatus = vi.fn().mockResolvedValue({}) }) {
  const [selectedTarget, select] = useState(null);
  return <ContextCampaignReview campaign={value} targets={targets} onAuthorize={onAuthorize} onStatus={onStatus} onRollback={vi.fn()} selectedTarget={selectedTarget} onSelectTarget={select} observations={observations} />;
}
Harness.propTypes = { value: PropTypes.object, targets: PropTypes.array, onAuthorize: PropTypes.func, onStatus: PropTypes.func };
afterEach(cleanup);

async function reviewTarget() {
  fireEvent.click(screen.getByRole('button', { name: 'Review target synthetic-repository' }));
  fireEvent.mouseDown(screen.getByLabelText('Include this target'));
  fireEvent.click(await screen.findByRole('option', { name: 'Include exact plan' }));
  fireEvent.change(screen.getByLabelText('Review or exclusion reason'), { target: { value: 'Preserved and reviewed local policy.' } });
  fireEvent.click(screen.getByRole('button', { name: 'Close target' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  fireEvent.click(screen.getByRole('checkbox'));
}

describe('exact repository review', () => {
  it('requires complete inventory, per-target decision, local review and explicit consent', async () => {
    const authorize = vi.fn().mockResolvedValue({});
    render(<Harness onAuthorize={authorize} />);
    const button = screen.getByRole('button', { name: 'Authorize reviewed PR plans' });
    expect(button).toBeDisabled();
    await reviewTarget();
    expect(button).not.toBeDisabled();
    fireEvent.click(button);
    fireEvent.click(screen.getByRole('button', { name: 'Authorize exact plans' }));
    await waitFor(() => expect(authorize).toHaveBeenCalledTimes(1));
    expect(authorize).toHaveBeenCalledWith({ revision: 3, expectedManifestDigest: 'sha256:manifest', acknowledgeRepositoryWrites: true,
      decisions: [{ bindingId: 'binding', planDigest: 'sha256:plan', decision: 'include', reason: 'Preserved and reviewed local policy.' }] });
  });
  it('never authorizes a partial first page of 40 targets', async () => {
    render(<Harness value={{ ...campaign, targetCount: 40 }} />);
    await reviewTarget();
    expect(screen.getByText(/complete target inventory has not loaded/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Authorize reviewed PR plans' })).toBeDisabled();
  });
  it('preserves a failed review and does not refresh stale preconditions', async () => {
    const authorize = vi.fn().mockRejectedValue(new Error('conflict'));
    const view = render(<Harness onAuthorize={authorize} />);
    await reviewTarget();
    fireEvent.click(screen.getByRole('button', { name: 'Authorize reviewed PR plans' }));
    fireEvent.click(screen.getByRole('button', { name: 'Authorize exact plans' }));
    await waitFor(() => expect(screen.getByText(/operation was not confirmed/)).toBeInTheDocument());
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    view.rerender(<Harness value={{ ...campaign, revision: 4, manifestDigest: 'sha256:new' }} onAuthorize={authorize} />);
    expect(screen.getByText(/No preconditions were refreshed automatically/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Authorize reviewed PR plans' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Review changed state' }));
    expect(screen.getByRole('checkbox')).not.toBeChecked();
    expect(screen.getByText('pending', { exact: true })).toBeInTheDocument();
  });
  it('cannot include a deliberate pin that would change its version', async () => {
    render(<Harness targets={[{ ...target, selector: { version: 2 } }]} />);
    fireEvent.click(screen.getByRole('button', { name: 'Review target synthetic-repository' }));
    fireEvent.mouseDown(screen.getByLabelText('Include this target'));
    expect(await screen.findByRole('option', { name: 'Include exact plan' })).toHaveAttribute('aria-disabled', 'true');
  });
  it('keeps disabled and role-lost campaigns read-only', () => {
    const view = render(<Harness value={{ ...campaign, enabled: false }} />);
    expect(screen.getByRole('button', { name: 'Authorize reviewed PR plans' })).toBeDisabled();
    view.rerender(<Harness value={{ ...campaign, canAuthorize: false }} />);
    expect(screen.getByRole('button', { name: 'Authorize reviewed PR plans' })).toBeDisabled();
  });
  it('collects a pause reason before the confirmation focus trap opens', async () => {
    const onStatus = vi.fn().mockResolvedValue({});
    render(<Harness value={{ ...campaign, status: 'running' }} onStatus={onStatus} />);
    fireEvent.change(screen.getByLabelText('Reason for campaign change'), { target: { value: 'Inspect uncertain PR response' } });
    fireEvent.click(screen.getByRole('button', { name: 'Pause new claims' }));
    fireEvent.click(screen.getByRole('button', { name: 'Confirm change' }));
    await waitFor(() => expect(onStatus).toHaveBeenCalledWith({ action: 'pause', revision: 3, reason: 'Inspect uncertain PR response' }));
  });
});

describe('independent observations and availability', () => {
  it('does not turn merged into installed or delivered', () => {
    render(<ContextCampaignTargetDrawer target={{ ...target, prState: 'merged', mergeState: 'merged' }} open onClose={vi.fn()} onDecision={vi.fn()} observations={observations} />);
    expect(screen.getAllByText('Not observed')).toHaveLength(3);
    expect(screen.getByText(/Merged does not mean installed or delivered/)).toBeInTheDocument();
  });
  it('retains the planned identity when a binding no longer names a repository', () => {
    render(<ContextCampaignTargetDrawer target={{ ...target, repository: null }} open onClose={vi.fn()} onDecision={vi.fn()} observations={observations} />);
    expect(screen.getByText(/binding no longer names a repository/)).toBeInTheDocument();
    expect(screen.getByText(/Planned repository ID 100/)).toBeInTheDocument();
  });
  it('renders remote diff text literally', () => {
    render(<ContextCampaignTargetDrawer target={{ ...target, plan: { ...target.plan, changes: [{ ...target.plan.changes[0], diffPreview: '<img src=x onerror=alert(1)>' }] } }} open onClose={vi.fn()} onDecision={vi.fn()} observations={observations} />);
    expect(screen.getByText('<img src=x onerror=alert(1)>')).toBeInTheDocument();
    expect(document.querySelector('img[src="x"]')).toBeNull();
  });
  it.each(['loading', 'empty', 'error', 'forbidden'])('has a reachable %s list state', (state) => {
    render(<ContextCampaignList query={{ refetch: vi.fn() }} state={state} onOpen={vi.fn()} page={1} onPageChange={vi.fn()} />);
    expect(screen.getByText(/Campaigns are unavailable/)).toBeInTheDocument();
  });
});
