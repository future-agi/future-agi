import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { HelmetProvider } from 'react-helmet-async';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import ContextCampaignPage from 'src/pages/dashboard/skills/contexts/ContextCampaignPage';

const state = vi.hoisted(() => ({ ready: false, enabled: false, identity: 'scope-a', create: vi.fn() }));
vi.mock('src/api/skills/testing', () => ({ useSkillV11Scope: () => ({ identity: state.identity }) }));
vi.mock('src/api/skills/context-campaigns', () => ({
  campaignsContractReady: () => state.ready,
  useContextCampaignAvailability: () => ({ data: { enabled: state.enabled } }),
  useContextCampaigns: () => ({ data: { count: 0, results: [] } }),
  useContextCampaign: () => ({}), useContextCampaignTargets: () => ({}),
  useContextCampaignObservations: () => ({}), useAuthorizeContextCampaign: () => ({}),
  useChangeContextCampaignStatus: () => ({}), usePreviewContextCampaignRollback: () => ({}),
  useCreateContextCampaign: () => ({ mutateAsync: state.create }),
}));
const renderRoute = (scope = 'scope-a') => render(<HelmetProvider><MemoryRouter initialEntries={[{ pathname: '/dashboard/skills/contexts/rollouts', state: { campaignInventory: { scopeIdentity: scope, targets: [{ bindingId: '00000000-0000-4000-8000-000000000001', version: 4 }] } } }]}><ContextCampaignPage /></MemoryRouter></HelmetProvider>);
beforeEach(() => { state.ready = false; state.enabled = false; state.identity = 'scope-a'; state.create.mockReset(); });
afterEach(cleanup);

it('fails closed before the serializer-generated routes are integrated', () => {
  renderRoute();
  expect(screen.getByText(/campaign persistence, generated HTTP contract/)).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Review selected inventory' })).toBeNull();
});
it('does not offer an inventory from another principal or workspace', () => {
  state.ready = true; state.enabled = true;
  renderRoute('scope-other');
  expect(screen.queryByRole('button', { name: 'Review selected inventory' })).toBeNull();
});
it('respects server availability independently of contract readiness', () => {
  state.ready = true;
  renderRoute();
  expect(screen.getByRole('button', { name: 'Review selected inventory' })).toBeDisabled();
});
it('reopens an uncertain create with the original body and key', async () => {
  state.ready = true; state.enabled = true;
  state.create.mockRejectedValue(new Error('lost response'));
  renderRoute();
  fireEvent.click(screen.getByRole('button', { name: 'Review selected inventory' }));
  fireEvent.change(screen.getByLabelText('Campaign name'), { target: { value: 'Synthetic fixed inventory' } });
  fireEvent.click(screen.getByRole('button', { name: 'Create draft campaign' }));
  await waitFor(() => expect(state.create).toHaveBeenCalledTimes(1));
  const first = state.create.mock.calls[0][0];
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  fireEvent.click(screen.getByRole('button', { name: 'Review selected inventory' }));
  expect(screen.getByLabelText('Campaign name')).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Recover original submission' }));
  await waitFor(() => expect(state.create).toHaveBeenCalledTimes(2));
  expect(state.create.mock.calls[1][0]).toEqual(first);
  expect(first.targets).toEqual([{ bindingId: '00000000-0000-4000-8000-000000000001', version: 4 }]);
});
