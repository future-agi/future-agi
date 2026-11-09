import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { HelmetProvider } from 'react-helmet-async';
import { paths } from 'src/routes/paths';
import SkillsWorkspaceLayout from 'src/sections/skills/components/SkillsWorkspaceLayout';
import SkillsSpendPolicyPage from './SkillsSpendPolicyPage';

const api = vi.hoisted(() => ({ scope: {}, org: {}, workspace: {}, query: {}, save: vi.fn(), ready: true }));
vi.mock('src/api/skills/testing', () => ({ useSkillV11Scope: () => api.scope }));
vi.mock('src/api/skills/context-campaigns', () => ({ campaignsContractReady: () => false, useContextCampaignAvailability: () => ({}) }));
vi.mock('src/api/skills/r3-spend', () => ({ spendPolicyReady: () => api.ready, useSkillsSpendPolicy: () => api.query, useUpdateSkillsSpendPolicy: () => ({ mutateAsync: api.save }) }));
vi.mock('src/contexts/OrganizationContext', () => ({ useOrganization: () => api.org }));
vi.mock('src/contexts/WorkspaceContext', () => ({ useWorkspace: () => api.workspace }));
const policy = { revision: 0, enabled: false, periodLimitUsd: null, startsAt: '2026-11-01', endsAt: '2026-12-01', knownEstimateUsd: '0', committedUsd: '0', remainingUsd: null, unknownReservedUsd: '0', priorPeriodUnknownUsd: '0' };
const mount = () => render(<HelmetProvider><MemoryRouter initialEntries={[paths.dashboard.skills.root]}><Routes>
  <Route path="/dashboard/skills" element={<SkillsWorkspaceLayout />}>
    <Route index element={<div>Library</div>} />
    <Route path="allowance" element={<SkillsSpendPolicyPage />} />
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
  fireEvent.click(screen.getByRole('tab', { name: 'Workspace allowance' }));
  expect(screen.getByLabelText('Period allowance (USD)')).toHaveValue('');
  expect(api.save).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText('Period allowance (USD)'), { target: { value: '12.3400' } });
  fireEvent.click(screen.getByLabelText('Enable paid-work admission under this allowance'));
  fireEvent.click(screen.getByRole('button', { name: 'Review policy change' }));
  expect(api.save).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Apply policy change' }));
  await waitFor(() => expect(api.save).toHaveBeenCalledOnce());
  expect(api.save.mock.calls[0][0]).toEqual({ revision: 0, enabled: true, periodLimitUsd: '12.3400', idempotencyKey: expect.any(String) });
});
it('allows members to discover the requirement but not authorize spending', () => {
  api.workspace.wsLevel = 3;
  mount();
  fireEvent.click(screen.getByRole('tab', { name: 'Workspace allowance' }));
  expect(screen.getByLabelText('Period allowance (USD)')).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Review policy change' })).toBeDisabled();
  expect(api.save).not.toHaveBeenCalled();
});
it('does not enable navigation before the spend contract is ready', () => {
  api.ready = false;
  mount();
  expect(screen.getByRole('tab', { name: 'Workspace allowance (unavailable)' })).toBeDisabled();
});
it('fails closed when the selected role belongs to another workspace', () => {
  api.workspace.currentWorkspaceId = 'old-workspace';
  mount();
  fireEvent.click(screen.getByRole('tab', { name: 'Workspace allowance' }));
  expect(screen.queryByLabelText('Period allowance (USD)')).not.toBeInTheDocument();
  expect(api.save).not.toHaveBeenCalled();
});
