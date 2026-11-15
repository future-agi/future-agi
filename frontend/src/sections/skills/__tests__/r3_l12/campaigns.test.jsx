import { useState } from 'react';
import { MemoryRouter } from 'react-router-dom';
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
function Harness({ value = campaign, targets = [target], onAuthorize = vi.fn().mockResolvedValue({}), onStatus = vi.fn().mockResolvedValue({}), access }) {
  const [selectedTarget, select] = useState(null);
  return <MemoryRouter><ContextCampaignReview access={access} campaign={value} targets={targets} onAuthorize={onAuthorize} onStatus={onStatus} onRollback={vi.fn()} selectedTarget={selectedTarget} onSelectTarget={select} observations={observations} /></MemoryRouter>;
}
Harness.propTypes = { value: PropTypes.object, targets: PropTypes.array, onAuthorize: PropTypes.func, onStatus: PropTypes.func, access: PropTypes.object };
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
    const button = screen.getByRole('button', { name: 'Authorize pull requests' });
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
    expect(screen.getByRole('button', { name: 'Authorize pull requests' })).toBeDisabled();
  });
  it('preserves a failed review and does not refresh stale preconditions', async () => {
    const authorize = vi.fn().mockRejectedValue(new Error('conflict'));
    const view = render(<Harness onAuthorize={authorize} />);
    await reviewTarget();
    fireEvent.click(screen.getByRole('button', { name: 'Authorize pull requests' }));
    fireEvent.click(screen.getByRole('button', { name: 'Authorize exact plans' }));
    await waitFor(() => expect(screen.getByText(/operation was not confirmed/)).toBeInTheDocument());
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    view.rerender(<Harness value={{ ...campaign, revision: 4, manifestDigest: 'sha256:new' }} onAuthorize={authorize} />);
    expect(screen.getByText(/No preconditions were refreshed automatically/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Authorize pull requests' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Review changed state' }));
    expect(screen.getByRole('checkbox')).not.toBeChecked();
    expect(screen.getByText('Not decided', { exact: true })).toBeInTheDocument();
  });
  it('cannot include a deliberate pin that would change its version', async () => {
    render(<Harness targets={[{ ...target, selector: { version: 2 } }]} />);
    fireEvent.click(screen.getByRole('button', { name: 'Review target synthetic-repository' }));
    fireEvent.mouseDown(screen.getByLabelText('Include this target'));
    expect(await screen.findByRole('option', { name: 'Include exact plan' })).toHaveAttribute('aria-disabled', 'true');
  });
  it('keeps disabled and role-lost campaigns read-only', () => {
    const view = render(<Harness value={{ ...campaign, enabled: false }} />);
    expect(screen.getByRole('button', { name: 'Authorize pull requests' })).toBeDisabled();
    view.rerender(<Harness value={{ ...campaign, canAuthorize: false }} />);
    expect(screen.getByRole('button', { name: 'Authorize pull requests' })).toBeDisabled();
  });
  it('collects a pause reason before the confirmation focus trap opens', async () => {
    const onStatus = vi.fn().mockResolvedValue({});
    render(<Harness value={{ ...campaign, status: 'running' }} onStatus={onStatus} />);
    fireEvent.change(screen.getByLabelText('Reason for rollout change'), { target: { value: 'Inspect uncertain PR response' } });
    fireEvent.click(screen.getByRole('button', { name: 'Pause new claims' }));
    fireEvent.click(screen.getByRole('button', { name: 'Confirm change' }));
    await waitFor(() => expect(onStatus).toHaveBeenCalledWith({ action: 'pause', revision: 3, reason: 'Inspect uncertain PR response' }));
  });
});

describe('independent observations and availability', () => {
  it('does not turn merged into installed or delivered', () => {
    render(<ContextCampaignTargetDrawer target={{ ...target, prState: 'merged', mergeState: 'merged' }} open onClose={vi.fn()} onDecision={vi.fn()} observations={observations} />);
    // Observations stay visible without a disclosure: merged is never shown as delivered.
    expect(screen.getByText('Delivery reports')).toBeVisible();
    expect(screen.getAllByText('Not observed')).toHaveLength(3);
    expect(screen.getByText(/A merged PR doesn't mean the app has the change/)).toBeInTheDocument();
  });
  it('retains the planned identity when a binding no longer names a repository', () => {
    render(<ContextCampaignTargetDrawer target={{ ...target, repository: null }} open onClose={vi.fn()} onDecision={vi.fn()} observations={observations} />);
    expect(screen.getByText(/target no longer names a repository/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Technical details' }));
    expect(screen.getByText(/Planned repository ID 100/)).toBeInTheDocument();
  });
  it('keeps a zero repository identifier in technical details without exposing it in the summary', () => {
    render(<ContextCampaignTargetDrawer target={{ ...target, repository: { ...target.repository, repositoryId: 0 } }} open onClose={vi.fn()} onDecision={vi.fn()} observations={observations} />);
    expect(screen.getByText('Branch: main · Root folder')).toBeVisible();
    expect(screen.queryByText('Repository ID 0')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Technical details' }));
    expect(screen.getByText('Repository ID 0')).toBeVisible();
  });
  it('renders remote diff text literally', () => {
    render(<ContextCampaignTargetDrawer target={{ ...target, plan: { ...target.plan, changes: [{ ...target.plan.changes[0], diffPreview: '<img src=x onerror=alert(1)>' }] } }} open onClose={vi.fn()} onDecision={vi.fn()} observations={observations} />);
    expect(screen.getByText('<img src=x onerror=alert(1)>')).toBeInTheDocument();
    expect(document.querySelector('img[src="x"]')).toBeNull();
  });
  it.each(['loading', 'empty', 'error', 'forbidden'])('has a reachable %s list state', (state) => {
    render(<ContextCampaignList query={{ refetch: vi.fn() }} enabled state={state} onOpen={vi.fn()} page={1} onPageChange={vi.fn()} />);
    const text = { loading: 'Checking rollout availability', empty: 'No repository rollouts yet', error: "Couldn't load rollouts.", forbidden: "You don't have access to repository rollouts. Ask a workspace admin." };
    expect(state === 'loading' ? screen.getByRole('progressbar', { name: text[state] }) : screen.getByText(text[state])).toBeInTheDocument();
  });
  it('labels a predecessor link as a follow-up and keeps same-named rollouts distinguishable', () => {
    // A parent is any predecessor: a replacement after base movement, a later update or a reverse plan.
    const rows = [{ ...campaign, id: 'replacement', revision: 40, status: 'settled', parentCampaignId: 'cancelled' },
      { ...campaign, id: 'cancelled', revision: 37, status: 'cancelled', parentCampaignId: null }];
    const onOpen = vi.fn();
    render(<ContextCampaignList query={{ data: { count: 2, results: rows }, refetch: vi.fn() }} enabled onOpen={onOpen} page={1} onPageChange={vi.fn()} />);
    expect(screen.getByText(/Follows an earlier rollout/)).toBeInTheDocument();
    expect(screen.queryByText(/Reverse campaign/)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Review Synthetic policy rollout, revision 40' }));
    expect(onOpen).toHaveBeenCalledWith('replacement');
    expect(screen.getByRole('button', { name: 'Review Synthetic policy rollout, revision 37' })).toBeInTheDocument();
  });
});

it('keeps retained rollout records readable when execution is off', () => {
  render(<ContextCampaignList query={{ data: { count: 1, results: [campaign] } }} enabled={false} onOpen={vi.fn()} page={1} onPageChange={vi.fn()} onCreate={vi.fn()} />);
  expect(screen.getByText('Repository rollouts are off')).toBeVisible();
  expect(screen.getByRole('table', { name: 'Repository rollouts' })).toBeVisible();
  expect(screen.queryByRole('button', { name: 'New rollout' })).toBeNull();
});

it('keeps hidden targets in authorization validation when filtering the inventory', async () => {
  const second = { ...target, bindingId: 'second', name: 'another-repository' };
  render(<Harness value={{ ...campaign, targetCount: 2 }} targets={[target, second]} />);
  await reviewTarget();
  fireEvent.change(screen.getByLabelText('Search repositories'), { target: { value: 'synthetic' } });
  expect(screen.queryByRole('button', { name: 'Review target another-repository' })).toBeNull();
  expect(screen.getByRole('button', { name: 'Authorize pull requests' })).toBeDisabled();
  expect(screen.getByText('Reviewed 1 of 2')).toBeVisible();
});


it('links the blocked count to visible, labelled blockers without relaxing authorization', () => {
  const blocked = { ...target, bindingId: 'blocked', name: 'blocked-repo', plan: { ...target.plan, blockers: ['Local modifications need review'] } };
  render(<Harness value={{ ...campaign, targetCount: 2 }} targets={[target, blocked]} />);
  fireEvent.click(screen.getByRole('button', { name: '1 blocked', exact: true }));
  expect(screen.getByText('Blocked: local changes')).toBeVisible();
  expect(screen.getByText('Local modifications need review')).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Review target synthetic-repository' })).toBeNull();
  expect(screen.getByRole('button', { name: 'Authorize pull requests' })).toBeDisabled();
});

it('renders unified excerpts with the shared diff while retaining hunk boundaries', () => {
  const diffPreview = '--- a/AGENTS.md\n+++ b/AGENTS.md\n@@ -1 +1 @@\n-old policy\n+new policy\n@@ -8 +8 @@\n-old ending\n+new ending';
  const { container } = render(<ContextCampaignTargetDrawer target={{ ...target, plan: { ...target.plan, changes: [{ ...target.plan.changes[0], diffPreview }] } }} open onClose={vi.fn()} onDecision={vi.fn()} />);
  expect(screen.getByText('@@ -1 +1 @@')).toBeVisible();
  expect(screen.getByText('@@ -8 +8 @@')).toBeVisible();
  expect(document.querySelectorAll('[data-status="added"]')).toHaveLength(2);
  expect(document.querySelectorAll('[data-status="removed"]')).toHaveLength(2);
  expect(container.querySelector('img')).toBeNull();
});

it('leads missing plans with a local command and keeps authorization blocked', () => {
  render(<Harness targets={[{ ...target, plan: null }]} />);
  expect(screen.getByText(/All 1 repository is blocked: no local plan yet/)).toBeVisible();
  expect(screen.getByRole('button', { name: 'Authorize pull requests' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Review target synthetic-repository' }));
  expect(within(screen.getByRole('dialog')).getByText(/python -m fi.context plan --name/)).toBeVisible();
  expect(within(screen.getByRole('dialog')).getByRole('link', { name: 'Plan and submit repository changes' })).toHaveAttribute('href', 'https://docs.futureagi.com/docs/skills/campaigns');
});

it('offers a button, not a bare command, in the empty state and reveals the inventory command on request', () => {
  render(<ContextCampaignList query={{ data: { count: 0, results: [] } }} enabled onOpen={vi.fn()} page={1} onPageChange={vi.fn()} />);
  const button = screen.getByRole('button', { name: 'Prepare a repository inventory' });
  expect(button).toHaveClass('MuiButton-outlined');
  expect(button).toHaveAttribute('aria-expanded', 'false');
  expect(screen.queryByText('python -m fi.context inventory --repositories repositories.json')).toBeNull();
  fireEvent.click(button);
  expect(button).toHaveAttribute('aria-expanded', 'true');
  const command = screen.getByText('python -m fi.context inventory --repositories repositories.json');
  expect(command).toBeVisible();
  expect(command.closest('pre')).not.toBeNull();
  expect(screen.getByRole('button', { name: 'Copy inventory command' })).toBeVisible();
  expect(screen.getByRole('link', { name: 'Read the inventory guide' })).toBeVisible();
});

it('shows a rollout state as a status badge in the list and the header', () => {
  const view = render(<ContextCampaignList query={{ data: { count: 1, results: [campaign] } }} enabled onOpen={vi.fn()} page={1} onPageChange={vi.fn()} />);
  const draft = within(screen.getByRole('table', { name: 'Repository rollouts' })).getByText('Draft');
  // A tinted badge (4px radius, filled), not a bare dot with text.
  expect(getComputedStyle(draft.parentElement).backgroundColor).not.toBe('rgba(0, 0, 0, 0)');
  view.unmount();
  render(<Harness />);
  const header = screen.getAllByText('Draft')[0];
  expect(getComputedStyle(header.parentElement).backgroundColor).not.toBe('rgba(0, 0, 0, 0)');
});

it('says a merged pull request is not delivery only in the target drawer, and leads the authorize panel as a bordered card', () => {
  render(<Harness targets={[{ ...target, plan: null }]} />);
  expect(screen.queryByText(/A merged pull request does not mean apps are updated/)).toBeNull();
  expect(screen.queryByRole('button', { name: 'Delivery details' })).toBeNull();
  expect(screen.queryByText(/merged PR doesn.t mean/)).toBeNull();
  // The panel is one card: its title, acknowledgment and button share a single boxed section.
  const checkbox = screen.getByRole('checkbox');
  let card = screen.getByText('Authorize pull requests', { selector: 'p' });
  while (card && !card.contains(checkbox)) card = card.parentElement;
  expect(card).toContainElement(screen.getByRole('button', { name: 'Authorize pull requests' }));
  expect(card).not.toContainElement(screen.getByRole('table', { name: 'Fixed rollout targets' }));
  fireEvent.click(screen.getByRole('button', { name: 'Review target synthetic-repository' }));
  expect(within(screen.getByRole('dialog')).getAllByText(/A merged PR doesn.t mean the app has the change/)).toHaveLength(1);
});

it('puts the note at the left of the target drawer footer, then the outlined Next target, then the contained Copy plan command', () => {
  render(<ContextCampaignTargetDrawer target={{ ...target, plan: null }} open onClose={vi.fn()} onDecision={vi.fn()} onNext={vi.fn()} />);
  const note = screen.getByText('Decisions are saved to this draft for this review session.');
  const next = screen.getByRole('button', { name: 'Next target', exact: true });
  const copy = screen.getByRole('button', { name: 'Copy plan command' });
  const inOrder = (a, b) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
  expect(inOrder(note, next)).toBe(true);
  expect(inOrder(next, copy)).toBe(true);
  expect(next).toHaveClass('MuiButton-outlined');
  expect(copy).toHaveClass('MuiButton-contained');
  expect(copy.parentElement).toBe(next.parentElement);
});

it.each([
  ['cancelled', 'Unstarted work is cancelled. Existing pull requests and receipts are kept.'],
  ['settled', 'Repository work has ended. Check each pull request and app delivery report before calling the rollout complete.'],
  ['paused', 'New repository work is paused. Review the reason before resuming unchanged plans.'],
  ['authorized', 'The reviewed plans are authorized. Open each repository to follow pull request progress.'],
  ['running', 'Repository work is running. Open each repository to check its pull request and delivery reports.'],
])('gives %s its own conclusion instead of restarting the missing-plan task', (status, conclusion) => {
  render(<Harness value={{ ...campaign, status }} targets={[{ ...target, plan: null }]} />);
  expect(screen.getByRole('status')).toHaveTextContent(conclusion);
  expect(screen.queryByText(/python -m fi.context plan --name/)).toBeNull();
});

const OFF_REASON = "Rollouts are off, so pull requests can't be authorized. Existing records stay readable.";
const ADMIN_COPY = 'Rollouts are set by your deployment configuration, not by this workspace. Ask whoever runs your Future AGI deployment to turn them on.';

describe('rollouts off', () => {
  const off = { ...campaign, enabled: false };
  it('explains why authorization is unavailable instead of asking for a review that cannot count', () => {
    render(<Harness value={off} />);
    const button = screen.getByRole('button', { name: 'Authorize pull requests' });
    expect(button).toBeDisabled();
    expect(button).toHaveAccessibleDescription(OFF_REASON);
    expect(screen.queryByText('Review every target to continue.')).toBeNull();
    expect(screen.getByRole('checkbox')).toBeDisabled();
  });
  it('still asks for the review when rollouts are on', () => {
    render(<Harness />);
    expect(screen.getByRole('button', { name: 'Authorize pull requests' })).toHaveAccessibleDescription('Review every target to continue.');
  });
  it('opens target drawers read-only: no plan command, no decision form, records and observations stay readable', () => {
    render(<Harness value={off} targets={[{ ...target, plan: null }]} />);
    fireEvent.click(screen.getByRole('button', { name: 'Review target synthetic-repository' }));
    const drawer = screen.getByRole('dialog');
    expect(within(drawer).getByText(OFF_REASON)).toBeVisible();
    expect(within(drawer).queryByRole('button', { name: 'Copy plan command' })).toBeNull();
    expect(within(drawer).queryByText(/python -m fi.context plan/)).toBeNull();
    expect(within(drawer).queryByLabelText('Include this target')).toBeNull();
    expect(within(drawer).getByText('Delivery reports')).toBeVisible();
    expect(within(drawer).getByRole('button', { name: 'Technical details' })).toBeVisible();
  });
  it('keeps a recorded decision visible, but not editable, after rollouts are turned off', () => {
    render(<Harness value={{ ...off, status: 'authorized' }} targets={[{ ...target, decision: 'include', reason: 'Reviewed.' }]} />);
    fireEvent.click(screen.getByRole('button', { name: 'Review target synthetic-repository' }));
    expect(within(screen.getByRole('dialog')).getByLabelText('Include this target')).toHaveAttribute('aria-disabled', 'true');
  });
  it.each([
    ['an admin', { ready: true, canAdmin: true }, ADMIN_COPY],
    ['a member', { ready: true, canAdmin: false }, 'Ask a workspace admin.'],
  ])('tells %s what controls the setting, on the review page and the list', (_, access, sentence) => {
    const view = render(<Harness value={off} access={access} />);
    expect(screen.getByText(new RegExp(sentence.replace(/[.]/g, '\\.')))).toBeVisible();
    if (access.canAdmin) expect(screen.getByRole('link', { name: 'Read the rollout setup guide' })).toHaveAttribute('href', 'https://docs.futureagi.com/docs/skills/campaigns');
    else expect(screen.queryByRole('link', { name: 'Read the rollout setup guide' })).toBeNull();
    view.unmount();
    render(<ContextCampaignList access={access} query={{ data: { count: 0, results: [] } }} enabled={false} onOpen={vi.fn()} page={1} onPageChange={vi.fn()} />);
    expect(screen.getByText(new RegExp(sentence.replace(/[.]/g, '\\.')))).toBeVisible();
    if (access.canAdmin) expect(screen.queryByText('Ask a workspace admin.')).toBeNull();
  });
  it('shows neither role sentence until the role is known', () => {
    render(<Harness value={off} access={{ ready: false, canAdmin: false }} />);
    expect(screen.getByText('Repository rollouts are off')).toBeVisible();
    expect(screen.queryByText(/Ask a workspace admin/)).toBeNull();
    expect(screen.queryByText(/deployment configuration/)).toBeNull();
  });
  it('keeps engineer words inside the technical details of the plan step', () => {
    render(<Harness targets={[{ ...target, plan: null }]} />);
    expect(screen.queryByText(/record_plan|repository_id|scoped context credential/)).toBeNull();
    expect(screen.getByText(/Each repository records its plan with a read-only key/)).toBeVisible();
  });
});
