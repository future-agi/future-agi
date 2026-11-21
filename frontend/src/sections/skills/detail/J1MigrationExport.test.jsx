import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { saveAs } from 'file-saver';
import J1MigrationExport from './J1MigrationExport';
const api = vi.hoisted(() => ({ prepare: vi.fn(), scope: 'org:workspace:owner', calls: [] }));
vi.mock('file-saver', () => ({ saveAs: vi.fn() }));
vi.mock('src/api/skills/j1-migration-export', () => ({ useMigrationExport: (id, number) => { api.calls.push([id, number]); return { mutateAsync: api.prepare }; } }));
vi.mock('src/api/skills/testing', () => ({ useSkillV11Scope: () => ({ identity: api.scope }) }));
const files = { archive: new Blob(['zip']), receipt: new Blob(['receipt']), versionId: 'immutable-version', digest: 'exact-digest', mappingCount: 1 };
beforeEach(() => { vi.clearAllMocks(); api.calls = []; api.scope = 'org:workspace:owner'; api.prepare.mockResolvedValue(files); });
afterEach(cleanup);
it('prepares only the selected immutable version and requires separate download actions', async () => {
  render(<J1MigrationExport resourceId="leaf" number={2} />);
  expect(api.prepare).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Prepare v2 migration export' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Download exact ZIP' }));
  fireEvent.click(screen.getByRole('button', { name: 'Download mapping receipt' }));
  expect(api.calls.every(([id, version]) => id === 'leaf' && version === 2)).toBe(true);
  expect(saveAs.mock.calls).toEqual([[files.archive, 'skill-leaf-v2.zip'], [files.receipt, 'skill-leaf-v2-mapping.json']]);
});
it('drops prepared private bytes when version or workspace identity changes', async () => {
  const view = render(<J1MigrationExport resourceId="leaf" number={2} />);
  fireEvent.click(screen.getByRole('button', { name: 'Prepare v2 migration export' }));
  await screen.findByRole('button', { name: 'Download exact ZIP' });
  view.rerender(<J1MigrationExport resourceId="leaf" number={1} />);
  expect(screen.queryByRole('button', { name: 'Download exact ZIP' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Prepare v1 migration export' }));
  await screen.findByRole('button', { name: 'Download exact ZIP' });
  api.scope = 'org:other:owner';
  view.rerender(<J1MigrationExport resourceId="leaf" number={1} />);
  expect(screen.queryByRole('button', { name: 'Download exact ZIP' })).not.toBeInTheDocument();
});
it('fails closed after an export error and makes no cleanup claim', async () => {
  api.prepare.mockRejectedValue(new Error('denied'));
  render(<J1MigrationExport resourceId="leaf" number={2} />);
  fireEvent.click(screen.getByRole('button', { name: 'Prepare v2 migration export' }));
  await screen.findByText('Could not prepare this exact export. Check your access and try again.');
  expect(screen.queryByRole('button', { name: 'Download exact ZIP' })).not.toBeInTheDocument();
  expect(saveAs).not.toHaveBeenCalled();
});
it('calls the export by what it keeps, the folder structure, not source paths', () => {
  render(<J1MigrationExport resourceId="leaf" number={2} />);
  expect(screen.getByText('Export with folder structure')).toBeInTheDocument();
  expect(screen.queryByText(/source paths/)).not.toBeInTheDocument();
});
