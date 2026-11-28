import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { HelmetProvider } from 'react-helmet-async';
import { paths } from 'src/routes/paths';
import SkillsWorkspaceLayout from 'src/sections/skills/components/SkillsWorkspaceLayout';
import { enqueueSnackbar } from 'notistack';
import SkillsSpendPolicyPage, { safeReturnTo } from './SkillsSpendPolicyPage';

const api = vi.hoisted(() => ({ scope: {}, org: {}, workspace: {}, query: {}, save: vi.fn(), ready: true }));
vi.mock('src/api/skills/testing', () => ({ useSkillV11Scope: () => api.scope }));
vi.mock('src/api/skills/context-campaigns', () => ({ campaignsContractReady: () => false, useContextCampaignAvailability: () => ({}) }));
vi.mock('src/api/skills/r3-spend', () => ({ spendPolicyReady: () => api.ready, useSkillsSpendPolicy: () => api.query, useUpdateSkillsSpendPolicy: () => ({ mutateAsync: api.save }) }));
vi.mock('src/contexts/OrganizationContext', () => ({ useOrganization: () => api.org }));
vi.mock('src/contexts/WorkspaceContext', () => ({ useWorkspace: () => api.workspace }));
vi.mock('src/sections/falcon-ai/components/FalconAIFab', () => ({ useOpenFalconAI: () => () => undefined, FalconIcon: () => null }));
vi.mock('notistack', () => ({ enqueueSnackbar: vi.fn() }));
const policy = { revision: 0, enabled: false, periodLimitUsd: null, startsAt: '2026-11-01', endsAt: '2026-12-01', knownEstimateUsd: '0', committedUsd: '0', remainingUsd: null, unknownReservedUsd: '0', priorPeriodUnknownUsd: '0' };
const mount = (path = paths.dashboard.skills.root) => render(<HelmetProvider><MemoryRouter initialEntries={[path]}><Routes>
  <Route path="/dashboard/skills" element={<SkillsWorkspaceLayout />}>
    <Route index element={<div>Library</div>} />
    <Route path="allowance" element={<SkillsSpendPolicyPage />} />
    <Route path="contexts/:id" element={<div>Context tests</div>} />
  </Route>
</Routes></MemoryRouter></HelmetProvider>);
beforeEach(() => {
  api.ready = true;
  api.scope = { enabled: true, identity: 'org:workspace:user', scope: { organizationId: 'org', workspaceId: 'workspace' } };
  api.org = { isReady: true, currentOrganizationId: 'org', orgLevel: 1 };
  api.workspace = { isReady: true, currentWorkspaceId: 'workspace', wsLevel: 8 };
  api.query = { data: policy, refetch: vi.fn() };
  api.save.mockReset().mockResolvedValue({ ...policy, revision: 1 });
});
afterEach(cleanup);
it('discovers the real allowance panel through production navigation and confirms an exact revision', async () => {
  mount();
  fireEvent.click(screen.getByRole('button', { name: 'More Skills pages' }));
  fireEvent.click(screen.getByRole('menuitem', { name: 'Workspace allowance' }));
  expect(screen.getByLabelText('Monthly allowance (USD)')).toHaveValue('');
  expect(api.save).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText('Monthly allowance (USD)'), { target: { value: '12.3400' } });
  fireEvent.click(screen.getByLabelText('Allow paid test runs up to this amount'));
  fireEvent.click(screen.getByRole('button', { name: 'Save', exact: true }));
  expect(api.save).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Save allowance' }));
  await waitFor(() => expect(api.save).toHaveBeenCalledOnce());
  expect(api.save.mock.calls[0][0]).toEqual({ revision: 0, enabled: true, periodLimitUsd: '12.3400', idempotencyKey: expect.any(String) });
});
it('shows members the summary and who can change it, with no form', () => {
  api.workspace.wsLevel = 3;
  mount();
  fireEvent.click(screen.getByRole('button', { name: 'More Skills pages' }));
  fireEvent.click(screen.getByRole('menuitem', { name: 'Workspace allowance' }));
  expect(screen.getByText('Paid test runs are off for this workspace.')).toBeVisible();
  expect(screen.getByRole('heading', { name: 'Only workspace admins can set the allowance' })).toBeVisible();
  expect(screen.queryByText('Only workspace admins can change this.')).not.toBeInTheDocument();
  expect(screen.queryByLabelText('Monthly allowance (USD)')).not.toBeInTheDocument();
  expect(screen.queryByLabelText('Allowance per period (USD)')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Save', exact: true })).not.toBeInTheDocument();
  expect(api.save).not.toHaveBeenCalled();
});
// UX iteration 5, SH6: a member who lands here from a blocked test can ask an admin, not just read a refusal.
it('lets a member copy a ready-to-send request for an admin, with a link back to this page', async () => {
  api.workspace.wsLevel = 3;
  const write = vi.fn().mockResolvedValue();
  Object.defineProperty(navigator, 'clipboard', { value: { writeText: write }, configurable: true });
  mount(`${paths.dashboard.skills.allowance}?returnTo=${encodeURIComponent('/dashboard/skills/contexts/c1?tab=tests')}`);
  const copy = screen.getByRole('button', { name: 'Copy request for admin' });
  expect(copy).toHaveClass('MuiButton-outlined');
  fireEvent.click(copy);
  await waitFor(() => expect(write).toHaveBeenCalledWith(`Please set a Skills workspace allowance so paid application tests can run: ${window.location.origin}${paths.dashboard.skills.allowance}`));
  await waitFor(() => expect(enqueueSnackbar).toHaveBeenCalledWith('Copied the request for an admin.'));
  expect(api.save).not.toHaveBeenCalled();
  // Refresh sits beside the request, in the same notice.
  fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
  expect(api.query.refetch).toHaveBeenCalledOnce();
});
it('says so when the request cannot be copied', async () => {
  api.workspace.wsLevel = 3;
  Object.defineProperty(navigator, 'clipboard', { value: { writeText: vi.fn().mockRejectedValue(new Error('denied')) }, configurable: true });
  mount(paths.dashboard.skills.allowance);
  fireEvent.click(screen.getByRole('button', { name: 'Copy request for admin' }));
  await waitFor(() => expect(enqueueSnackbar).toHaveBeenCalledWith("Couldn't copy the request. Ask a workspace admin to set the allowance.", { variant: 'error' }));
});
it('names the field for the period the server reports', () => {
  mount(paths.dashboard.skills.allowance);
  expect(screen.getByLabelText('Monthly allowance (USD)')).toBeVisible();
  cleanup();
  api.query = { data: { ...policy, startsAt: '2026-11-01T00:00:00Z', endsAt: '2026-11-15T00:00:00Z' }, refetch: vi.fn() };
  mount(paths.dashboard.skills.allowance);
  expect(screen.getByLabelText('Allowance per period (USD)')).toBeVisible();
  expect(screen.queryByLabelText('Monthly allowance (USD)')).not.toBeInTheDocument();
  expect(screen.getByText('Limit for each period, in USD.')).toBeVisible();
});
it('reads a saved allowance in dollars and states the conclusion first', () => {
  api.query = { data: { ...policy, revision: 3, enabled: true, periodLimitUsd: '20.00000000', knownEstimateUsd: '0', remainingUsd: '20.00000000' }, refetch: vi.fn() };
  mount();
  fireEvent.click(screen.getByRole('button', { name: 'More Skills pages' }));
  fireEvent.click(screen.getByRole('menuitem', { name: 'Workspace allowance' }));
  // One line: limit, use, what's left and the period, then the caveat once (UX iteration 4, SH7).
  expect(screen.getByRole('status')).toHaveTextContent(/^\$20\.00 limit · \$0\.00 used · \$20\.00 left · Nov 1–30 \(UTC\)\. Estimates, not invoices\.$/);
  expect(screen.queryByText(/allowed up to/)).not.toBeInTheDocument();
  expect(screen.getByLabelText('Monthly allowance (USD)')).toHaveValue('20.00');
  expect(screen.getByText('$')).toBeVisible();
  expect(screen.queryByText(/Leave empty/)).not.toBeInTheDocument();
  // Nothing typed yet: Save says why it is unavailable.
  expect(screen.getByRole('button', { name: 'Save', exact: true })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Save', exact: true })).toHaveAccessibleDescription('Nothing changed yet.');
  fireEvent.change(screen.getByLabelText('Monthly allowance (USD)'), { target: { value: '' } });
  expect(screen.getByText('Monthly limit in USD.')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Save', exact: true })).toHaveAccessibleDescription('Enter an amount.');
});
it('names the page once, under a Skills breadcrumb, with one disclosure for how usage is counted', () => {
  mount();
  fireEvent.click(screen.getByRole('button', { name: 'More Skills pages' }));
  fireEvent.click(screen.getByRole('menuitem', { name: 'Workspace allowance' }));
  const crumbs = screen.getByRole('navigation', { name: 'Breadcrumb' });
  expect(within(crumbs).getByRole('link', { name: 'Skills' })).toHaveAttribute('href', paths.dashboard.skills.root);
  expect(within(crumbs).getByText('Workspace allowance')).toHaveAttribute('aria-current', 'page');
  expect(screen.getByRole('heading', { name: 'Workspace allowance' })).toBeVisible();
  expect(screen.queryByText('Spending allowance')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Accounting details' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'How usage is counted' })).toHaveAttribute('aria-expanded', 'false');
  // Two controls stay: an amount saved with paid runs off is its own server state. Nothing starts ticked.
  expect(screen.getByLabelText('Allow paid test runs up to this amount')).not.toBeChecked();
});
it('keeps one "off" control: the box, never pre-ticked; the field hint only describes the amount', () => {
  mount();
  fireEvent.click(screen.getByRole('button', { name: 'More Skills pages' }));
  fireEvent.click(screen.getByRole('menuitem', { name: 'Workspace allowance' }));
  expect(screen.getByRole('status')).toHaveTextContent('Paid test runs are off for this workspace.');
  expect(screen.getByText('Monthly limit in USD.')).toBeVisible();
  expect(screen.queryByText(/Leave empty/)).not.toBeInTheDocument();
  expect(screen.queryByText(/No amount is filled in/)).not.toBeInTheDocument();
  expect(screen.getByLabelText('Allow paid test runs up to this amount')).not.toBeChecked();
});
it('leads back to the blocked test after a save, only for a Skills return path', async () => {
  mount(`${paths.dashboard.skills.allowance}?returnTo=${encodeURIComponent('/dashboard/skills/contexts/c1?tab=tests')}`);
  expect(screen.queryByRole('link', { name: 'Back to tests' })).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Monthly allowance (USD)'), { target: { value: '20' } });
  fireEvent.click(screen.getByLabelText('Allow paid test runs up to this amount'));
  fireEvent.click(screen.getByRole('button', { name: 'Save', exact: true }));
  fireEvent.click(screen.getByRole('button', { name: 'Save allowance' }));
  const back = await screen.findByRole('link', { name: 'Back to tests' });
  expect(screen.getByText(/^Allowance saved\./)).toBeVisible();

  expect(back).toHaveAttribute('href', '/dashboard/skills/contexts/c1?tab=tests');
  expect(back).toHaveClass('MuiButton-outlined');
  fireEvent.click(back);
  expect(await screen.findByText('Context tests')).toBeVisible();
});
it('ignores a return path outside Skills', async () => {
  mount(`${paths.dashboard.skills.allowance}?returnTo=${encodeURIComponent('https://example.invalid/dashboard/skills/x')}`);
  fireEvent.change(screen.getByLabelText('Monthly allowance (USD)'), { target: { value: '20' } });
  fireEvent.click(screen.getByLabelText('Allow paid test runs up to this amount'));
  fireEvent.click(screen.getByRole('button', { name: 'Save', exact: true }));
  fireEvent.click(screen.getByRole('button', { name: 'Save allowance' }));
  expect(await screen.findByText(/^Allowance saved\./)).toBeVisible();
  expect(screen.queryByRole('link', { name: 'Back to tests' })).not.toBeInTheDocument();
  for (const value of [null, '', '/dashboard/skills', '//evil.example/dashboard/skills/', '/dashboard/skills/../settings', '/dashboard/skills/\\evil', '/dashboard/settings']) expect(safeReturnTo(value)).toBeNull();
  expect(safeReturnTo('/dashboard/skills/contexts/c1?tab=tests')).toBe('/dashboard/skills/contexts/c1?tab=tests');
});
it('uses its own load failure, never the release review wording', () => {
  const refetch = vi.fn();
  api.query = { isError: true, refetch };
  mount();
  fireEvent.click(screen.getByRole('button', { name: 'More Skills pages' }));
  fireEvent.click(screen.getByRole('menuitem', { name: 'Workspace allowance' }));
  expect(screen.getByText("Couldn't load the workspace allowance.")).toBeVisible();
  expect(screen.getByText('Nothing was changed.')).toBeVisible();
  expect(screen.queryByText('Could not load review')).not.toBeInTheDocument();
  expect(screen.queryByText(/No release action was taken/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: /Retry/ }));
  expect(refetch).toHaveBeenCalled();
});
it('does not enable navigation before the spend contract is ready', () => {
  api.ready = false;
  mount();
  expect(screen.queryByRole('tab', { name: /Workspace allowance/ })).not.toBeInTheDocument();
  // More can still hold Archived skills for an admin (A8); it never offers the allowance before its contract.
  fireEvent.click(screen.getByRole('button', { name: 'More Skills pages' }));
  expect(screen.getByRole('menuitem', { name: 'Archived skills' })).toBeVisible();
  expect(screen.queryByRole('menuitem', { name: 'Workspace allowance' })).not.toBeInTheDocument();
});
it('fails closed when the selected role belongs to another workspace', () => {
  api.workspace.currentWorkspaceId = 'old-workspace';
  mount();
  fireEvent.click(screen.getByRole('button', { name: 'More Skills pages' }));
  fireEvent.click(screen.getByRole('menuitem', { name: 'Workspace allowance' }));
  expect(screen.queryByLabelText('Monthly allowance (USD)')).not.toBeInTheDocument();
  expect(api.save).not.toHaveBeenCalled();
});
