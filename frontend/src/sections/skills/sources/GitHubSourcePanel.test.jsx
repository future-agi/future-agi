import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import GitHubSourcePanel from './GitHubSourcePanel';

const api = vi.hoisted(() => ({ draft: {}, preview: {}, source: {}, check: vi.fn(), stage: vi.fn(), refreshDraft: vi.fn(), refreshSource: vi.fn() }));
vi.mock('src/api/skills', () => ({
  useSkillDraft: () => ({ ...api.draft, refetch: api.refreshDraft }),
  getSkillErrorCode: (error) => error?.code,
}));
vi.mock('src/api/skills/github', () => ({
  useSkillSource: () => ({ data: api.source, refetch: api.refreshSource }),
  useCheckSkillSource: () => ({ mutateAsync: api.check }),
  useStageSkillSourceUpdate: () => ({ mutateAsync: api.stage }),
  useGitHubPreview: (id) => ({ data: id ? api.preview : undefined }),
  useCreateGitHubPreview: () => ({}),
  useStageGitHubImport: () => ({}),
}));
const panel = () => <MemoryRouter><GitHubSourcePanel skill={{ id: 'synthetic-skill' }} /></MemoryRouter>;
const stageButton = () => screen.getByRole('button', { name: 'Stage replacement draft', exact: true });

beforeEach(() => {
  vi.clearAllMocks();
  api.draft = { data: { revision: 3, files: [] }, isPending: false, isError: false, isFetching: false };
  api.source = { revision: 2, refKind: 'branch', ref: 'main', repo: 'synthetic/policy', path: 'policy', importedSha: 'a'.repeat(40) };
  api.preview = { id: 'retained-preview', status: 'completed', outcome: 'changed', operation: 'update',
    canStage: true, localDiverged: true, sourceRevision: 3, expectedLatestVersion: 2, expectedLatestDigest: 'sha256:reviewed',
    source: { repo: 'synthetic/policy', path: 'policy', ref: 'main', refKind: 'branch', commitSha: 'b'.repeat(40) },
    license: { requiresAdminReview: false }, bundle: null, diff: null };
  api.check.mockResolvedValue({ id: api.preview.id });
  api.stage.mockResolvedValue({});
  api.refreshDraft.mockResolvedValue({ data: null });
});
afterEach(cleanup);

async function review() {
  fireEvent.click(screen.getByRole('button', { name: 'Check for updates', exact: true }));
  await screen.findByRole('button', { name: 'Stage replacement draft', exact: true });
  fireEvent.click(screen.getByRole('checkbox', { name: 'Replace my reviewed local changes. They will not be merged.', exact: true }));
  fireEvent.click(screen.getByRole('checkbox', { name: 'I confirm that I have the rights to import these files.', exact: true }));
  fireEvent.change(screen.getByRole('textbox', { name: 'Rights-review note', exact: true }), { target: { value: 'Retain this reviewed note.' } });
}

describe('retained GitHub update after another-tab draft removal', () => {
  it('refreshes only draft status and stages the same preview with review inputs intact', async () => {
    const view = render(panel());
    await review();
    expect(stageButton()).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Refresh draft status', exact: true }));
    expect(api.refreshDraft).toHaveBeenCalledTimes(1);
    expect(api.check).toHaveBeenCalledTimes(1);
    expect(api.stage).not.toHaveBeenCalled();
    api.draft = { ...api.draft, data: null };
    view.rerender(panel());
    expect(screen.getByRole('textbox', { name: 'Rights-review note', exact: true })).toHaveValue('Retain this reviewed note.');
    expect(stageButton()).toBeEnabled();
    fireEvent.click(stageButton());
    await waitFor(() => expect(api.stage).toHaveBeenCalledWith({
      previewId: 'retained-preview', revision: 0, expectedSourceRevision: 3,
      expectedLatestVersion: 2, expectedLatestDigest: 'sha256:reviewed', acceptReplacement: true,
      rights: { confirmed: true, reviewNote: 'Retain this reviewed note.' },
    }));
    expect(api.check).toHaveBeenCalledTimes(1);
    expect(api.refreshSource).not.toHaveBeenCalled();
  });

  it('keeps the draft conflict on a pending or failed refresh', async () => {
    const view = render(panel());
    await review();
    api.draft = { ...api.draft, isFetching: true };
    view.rerender(panel());
    expect(screen.getByRole('button', { name: 'Refresh draft status', exact: true })).toBeDisabled();
    expect(stageButton()).toBeDisabled();
    api.draft = { ...api.draft, isFetching: false, isError: true };
    view.rerender(panel());
    expect(screen.getByText('Could not check your draft', { exact: true })).toBeVisible();
    expect(stageButton()).toBeDisabled();
    expect(api.stage).not.toHaveBeenCalled();
  });

  it('blocks staging while an explicitly refreshed empty-draft result is still pending', async () => {
    api.draft = { ...api.draft, data: null };
    const view = render(panel());
    await review();
    expect(stageButton()).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Refresh draft status', exact: true })).toBeEnabled();
    api.draft = { ...api.draft, isFetching: true };
    view.rerender(panel());
    expect(stageButton()).toBeDisabled();
    expect(api.stage).not.toHaveBeenCalled();
  });
});
