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
  fireEvent.click(screen.getByRole('button', { name: 'Source identity' }));
  expect(screen.getByLabelText('Immutable source identities JSON')).toBeVisible();
  expect(screen.getByText('1 source files selected')).toBeVisible();
});

it('names the import options in plain words', () => {
  view();
  expect(screen.getByRole('button', { name: 'Supported formats' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'List made with the command line' })).toBeInTheDocument();
  expect(screen.queryByText(/CLI inventory|source identity|inventory packet/i)).not.toBeInTheDocument();
});

it('carries the retained plans through draft preservation and reloads the same plan without a new upload', async () => {
  api.reload.mockResolvedValueOnce([plan]);
  view({ initialPlanIds: ['plan-1'] });
  fireEvent.click(await screen.findByRole('button', { name: 'Preserve draft for policy' }));
  expect(api.navigate).toHaveBeenCalledWith('/dashboard/skills/dest/edit?preserve=migration', { state: { reopenMigration: { planIds: ['plan-1'] } } });
  cleanup();
  api.reload.mockResolvedValueOnce([{ ...plan, items: [{ ...item, hasDraft: false, diverged: true }] }]);
  view({ initialPlanIds: ['plan-1'] });
  await screen.findByRole('radiogroup', { name: 'Decision for policy' });
  expect(api.reload).toHaveBeenCalledTimes(2);
  expect(api.reload).toHaveBeenLastCalledWith({ planIds: ['plan-1'] });
  expect(api.preview).not.toHaveBeenCalled();
  await waitFor(() => expect(screen.getByRole('button', { name: 'Import and publish 1 item' })).toBeDisabled());
});

it('previews every source of one inventory packet into one reviewed set', async () => {
  const exchangePlan = { ...plan, id: 'plan-x', items: [{ ...item, key: 'x', name: 'exchange-skill', sourceResource: 'x', hasDraft: false, destinationId: null }] };
  const folderPlan = { ...plan, id: 'plan-f', items: [{ ...item, key: 'f', name: 'folder-skill', sourceResource: 'f', hasDraft: false, destinationId: null }] };
  api.preview.mockResolvedValueOnce(exchangePlan).mockResolvedValueOnce(folderPlan);
  view();
  const packet = { schema: 1, kind: 'futureagi-migration-packet', context: { name: 'trial', description: 'Trial', agents_md: 'Root\n' }, sources: [
    { kind: 'langfuse', export: { format: 'langfuse-skills-v1' } },
    { kind: 'folder', files: [{ path: 'private/f/SKILL.md', content_base64: btoa('skill') }], source_identities: { key: { resource: 'private/f' } } }] };
  fireEvent.change(screen.getByLabelText('Inventory migration packet'), { target: { files: [new File([JSON.stringify(packet)], 'packet.json')] } });
  const table = await screen.findByRole('table', { name: 'Retained migration items' });
  await waitFor(() => expect(table).toHaveTextContent('folder-skill'));
  expect(table).toHaveTextContent('exchange-skill');
  expect(screen.getByText(/2 sources · list of skills to import/)).toBeInTheDocument();
  expect(api.preview).toHaveBeenCalledTimes(2);
  expect(api.preview.mock.calls[0][0]).toEqual({ exportData: { format: 'langfuse-skills-v1' } });
  const form = api.preview.mock.calls[1][0].form;
  expect(form.getAll('files')[0].name).toBe('private/f/SKILL.md');
  expect(JSON.parse(form.get('source_identities'))).toEqual({ key: { resource: 'private/f' } });
});

it('collapses source format after preview and restores it only when changing sources', async () => {
  api.reload.mockResolvedValueOnce([plan]);
  view({ initialPlanIds: ['plan-1'] });
  await screen.findByRole('button', { name: 'Change sources' });
  expect(screen.queryByRole('combobox', { name: 'Source' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Change sources' }));
  expect(screen.getByRole('combobox', { name: 'Source' })).toBeVisible();
});

it('rejects a file that is not an inventory packet without previewing anything', async () => {
  view();
  fireEvent.change(screen.getByLabelText('Inventory migration packet'), { target: { files: [new File(['{"kind":"other"}'], 'x.json')] } });
  await screen.findByText('Choose a migration packet written by the local inventory.');
  expect(api.preview).not.toHaveBeenCalled();
});

it('explains exchange-file preparation beside the picker before any upload', () => {
  view();
  expect(screen.getByRole('combobox', { name: 'Source' })).toHaveTextContent('Langfuse exchange file (JSON)');
  expect(screen.getByText(/Native Langfuse exports need converting first/)).toBeVisible();
  expect(screen.getByRole('link', { name: 'How to prepare the file' })).toHaveAttribute('href', 'https://docs.futureagi.com/docs/skills/migration#before-migration');
  expect(screen.getByRole('button', { name: 'Choose export file' })).toBeVisible();
});

it('offers one footer, reason then Cancel then Preview import, and no button inside the body', () => {
  view();
  const previews = screen.getAllByRole('button', { name: 'Preview import', exact: true });
  expect(previews).toHaveLength(1);
  expect(previews[0]).toBeDisabled();
  expect(previews[0]).toHaveAccessibleDescription('Choose a file or folder first');
  const cancel = screen.getByRole('button', { name: 'Cancel' });
  expect(cancel.compareDocumentPosition(previews[0]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  fireEvent.change(screen.getByLabelText('Retained private folder'), { target: { files: [new File(['x'], 'SKILL.md')] } });
  expect(screen.getByRole('button', { name: 'Preview import', exact: true })).toBeEnabled();
  expect(screen.queryByText('Choose a file or folder first')).not.toBeInTheDocument();
});

it('keeps the reviewed set mounted while sources change, with a way back to it', async () => {
  api.reload.mockResolvedValueOnce([plan]);
  view({ initialPlanIds: ['plan-1'] });
  await screen.findByRole('button', { name: 'Change sources' });
  expect(screen.queryByRole('button', { name: 'Preview import', exact: true })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Change sources' }));
  expect(screen.getByRole('button', { name: 'Preview import', exact: true })).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Back to review' }));
  expect(screen.queryByRole('button', { name: 'Preview import', exact: true })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Change sources' })).toBeVisible();
});
