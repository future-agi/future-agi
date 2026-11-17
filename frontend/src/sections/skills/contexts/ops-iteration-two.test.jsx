import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import ContextConsumersView from './ContextConsumersView';
import ContextTargetRegistration from './ContextTargetRegistration';
import ContextCampaignReview from './ContextCampaignReview';
import ContextCampaignTargetDrawer from './ContextCampaignTargetDrawer';
import { planCommand } from './rolloutCommands';

vi.mock('src/api/skills/context-consumers', () => ({ useCreateContextBinding: () => ({ mutateAsync: vi.fn(), isPending: false }) }));
afterEach(cleanup);
const target = { bindingId: 'repo', name: 'Support repository', selector: { label: 'production' }, desiredVersion: 2, desiredVersionId: 'v2', desiredDigest: 'sha256:release', repository: { repositoryId: '123', defaultBranch: 'main' }, plan: null, prState: 'not_started', mergeState: 'unknown' };
const campaign = { name: 'Support rollout', status: 'draft', revision: 1, manifestDigest: 'sha256:manifest', targetCount: 1, enabled: true, canAuthorize: true };

it('hides empty-consumer filters and makes registration the primary without changing its name', () => {
  const data = { results: [], count: 0, page: 1, pageSize: 25 };
  const action = <ContextTargetRegistration contextId="ctx" canRegister />;
  const view = render(<ContextConsumersView onWindowChange={vi.fn()} onPageChange={vi.fn()} scopeKey="workspace" state="ready" data={data} registrationAction={action} />);
  expect(screen.queryByLabelText('Search apps')).toBeNull();
  expect(screen.queryByLabelText('Verification')).toBeNull();
  expect(screen.queryByLabelText('Observation window')).toBeNull();
  expect(screen.getByRole('button', { name: 'Connect an app' })).toHaveClass('MuiButton-contained');
  view.rerender(<ContextConsumersView onWindowChange={vi.fn()} onPageChange={vi.fn()} scopeKey="workspace" state="ready" data={{ ...data, count: 1, results: [{ binding: { id: 'app', name: 'Support app', kind: 'runtime', selector: { label: 'production' } }, states: ['unknown'] }] }} registrationAction={action} />);
  expect(screen.getByLabelText('Search apps')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Connect an app' })).toHaveClass('MuiButton-outlined');
});

it('has one helper line, a setup link and a footer note, without manifest wording', () => {
  render(<ContextTargetRegistration contextId="ctx" canRegister />);
  fireEvent.click(screen.getByRole('button', { name: 'Connect an app' }));
  const dialog = screen.getByRole('dialog');
  expect(within(dialog).getAllByText(/config file/i).length).toBeGreaterThan(0);
  expect(within(dialog).queryByText(/manifest/i)).toBeNull();
  expect(within(dialog).queryByText(/Choose the JSON file your app build produces/)).toBeNull();
  expect(within(dialog).getByRole('link', { name: 'App setup guide' })).toHaveAttribute('href', 'https://docs.futureagi.com/docs/skills/reviewed-releases');
  expect(within(dialog).getByRole('button', { name: 'Connect app' })).toBeDisabled();
});

it('requires a nonblank cancellation reason before opening confirmation', () => {
  const status = vi.fn();
  render(<MemoryRouter><ContextCampaignReview campaign={campaign} targets={[target]} onAuthorize={vi.fn()} onStatus={status} onRollback={vi.fn()} onSelectTarget={vi.fn()} /></MemoryRouter>);
  const cancel = screen.getByRole('button', { name: 'Stop unstarted work' });
  expect(cancel).toBeDisabled();
  expect(cancel).toHaveAccessibleDescription('Enter a reason to cancel');
  fireEvent.change(screen.getByLabelText('Reason for rollout change'), { target: { value: '  ' } });
  expect(cancel).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Reason for rollout change'), { target: { value: 'Waiting for owner review' } });
  expect(cancel).toBeEnabled();
  fireEvent.click(cancel);
  expect(screen.getByRole('button', { name: 'Confirm change' })).toBeVisible();
  expect(status).not.toHaveBeenCalled();
});

it('makes copying a local plan the primary when a target has no plan', async () => {
  const copy = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: copy } });
  render(<ContextCampaignTargetDrawer target={target} open onClose={vi.fn()} onDecision={vi.fn()} onNext={vi.fn()} />);
  expect(screen.queryByRole('button', { name: 'Next target needing review' })).toBeNull();
  expect(screen.getByRole('button', { name: 'Next target', exact: true })).toHaveClass('MuiButton-outlined');
  const button = screen.getByRole('button', { name: 'Copy plan command' });
  expect(button).toHaveClass('MuiButton-contained');
  fireEvent.click(button);
  await waitFor(() => expect(copy).toHaveBeenCalledWith(planCommand(2)));
  expect(screen.getByText('Copied plan command.')).toBeVisible();
  expect(screen.queryByRole('alert')).toBeNull();
});

it('reports a failed plan copy rather than claiming success', async () => {
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: vi.fn().mockRejectedValue(new Error('Denied')) } });
  render(<ContextCampaignTargetDrawer target={target} open onClose={vi.fn()} onDecision={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: 'Copy plan command' }));
  expect(await screen.findByText("Couldn't copy. Select the plan command above.")).toBeVisible();
  expect(screen.queryByText('Copied plan command.')).toBeNull();
});
