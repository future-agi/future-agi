import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import RetainedMigrationPanel from './RetainedMigrationPanel';

const api = vi.hoisted(() => ({ preview: vi.fn(), reload: vi.fn(), navigate: vi.fn() }));
vi.mock('src/api/skills/migration', () => ({
  useMigrationPreview: () => ({ mutateAsync: api.preview, isPending: false }),
  useMigrationDecision: () => ({ mutateAsync: vi.fn() }),
  useMigrationPublish: () => ({ mutateAsync: vi.fn() }),
  useMigrationPlanReload: () => ({ mutateAsync: api.reload, isPending: false }),
}));
vi.mock('src/api/skills/testing', () => ({ useSkillV11Scope: () => ({ identity: 'scope' }) }));
vi.mock('react-router-dom', async (original) => ({ ...(await original()), useNavigate: () => api.navigate }));

const digest = `sha256:${'a'.repeat(64)}`;
const item = { key: 'one', name: 'policy', sourceOrigin: 'o', sourceProject: 'p', sourceResource: 'r', destinationId: 'dest', expectedHeadVersion: 2, hasDraft: true, sourceHubChanges: [] };
const plan = { id: 'plan-1', fingerprint: digest, expiresAt: '2099-01-01T00:00:00Z', items: [item] };
const view = (props = {}) => render(<MemoryRouter><RetainedMigrationPanel onBusyChange={vi.fn()} onClose={vi.fn()} {...props} /></MemoryRouter>);
afterEach(() => { cleanup(); vi.clearAllMocks(); });

it('treats choosing a private folder as the folder format and asks for inventory identities', () => {
  view();
  expect(screen.queryByLabelText('Immutable source identities JSON')).not.toBeInTheDocument();
  const file = new File(['x'], 'SKILL.md');
  fireEvent.change(screen.getByLabelText('Retained private folder'), { target: { files: [file] } });
  expect(screen.getByLabelText('Immutable source identities JSON')).toBeVisible();
  expect(screen.getByText('1 source files selected')).toBeVisible();
});

it('carries the retained plans through draft preservation and reloads the same plan without a new upload', async () => {
  api.reload.mockResolvedValueOnce([plan]);
  view({ initialPlanIds: ['plan-1'] });
  fireEvent.click(await screen.findByRole('button', { name: 'Preserve draft for policy' }));
  expect(api.navigate).toHaveBeenCalledWith('/dashboard/skills/dest/edit?preserve=migration', { state: { reopenMigration: { planIds: ['plan-1'] } } });
  cleanup();
  api.reload.mockResolvedValueOnce([{ ...plan, items: [{ ...item, hasDraft: false, diverged: true }] }]);
  view({ initialPlanIds: ['plan-1'] });
  await screen.findByRole('combobox', { name: 'Decision for policy' });
  expect(api.reload).toHaveBeenCalledTimes(2);
  expect(api.reload).toHaveBeenLastCalledWith({ planIds: ['plan-1'] });
  expect(api.preview).not.toHaveBeenCalled();
  await waitFor(() => expect(screen.getByRole('button', { name: 'Import and publish 1 item' })).toBeDisabled());
});
