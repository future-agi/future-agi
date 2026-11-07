import { webcrypto } from 'node:crypto';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import SkillEditorView from './SkillEditorView';

const api = vi.hoisted(() => ({ scope: 'org:workspace:owner', save: vi.fn(), preview: vi.fn(), create: vi.fn(), apply: vi.fn(), snapshot: null }));
const files = [{ path: 'SKILL.md', content: 'Saved approved policy' }];
const initial = { files, note: '', revision: 4, baseVersion: 1 };
const skill = { id: 'skill', name: 'policy', latestVersion: 1, permissions: { canRunTests: true } };
const query = (data) => ({ data, refetch: vi.fn() });
const mutation = (mutateAsync = vi.fn()) => ({ mutateAsync, isPending: false });
const plan = { inputFingerprint: 'reviewed-plan', providerDisclosure: { provider: 'fixture-provider' }, estimate: { upperBoundUsd: '0.50' }, allowance: { inputBytes: 100, maxOutputTokens: 4096, deadlineSeconds: 60 }, outboundFindings: [] };

vi.mock('src/api/skills', () => ({
  useSkill: () => query(skill), useSkillDraft: () => query(initial),
  useSaveSkillDraft: () => mutation(api.save), useDiscardSkillDraft: () => mutation(),
  getSkillErrorCode: (error) => error.code,
}));
vi.mock('src/api/skills/testing', () => ({
  useSkillV11Scope: () => ({ identity: api.scope, enabled: true }),
  useSkillTestingOptions: () => query({ enabled: true, models: [{ modelId: 'fixture-model', canRun: true }] }),
  useSkillTestResults: () => query({ results: [] }), useSkillTestAttempt: () => query(null),
  isActiveSkillJob: (status) => ['queued', 'running', 'cancelling'].includes(status),
}));
vi.mock('src/api/skills/assist', () => ({
  usePreviewSkillAssist: () => mutation(api.preview), useCreateSkillAssist: () => mutation(api.create),
  useApplySkillAssist: () => mutation(api.apply), useCancelSkillAssist: () => mutation(),
  useSkillAssist: (_skillId, id) => query(id ? { id, kind: 'draft', status: 'completed', baseRevision: api.snapshot.revision, baseDigest: api.snapshot.expectedDigest, proposal: { files: [{ path: 'SKILL.md', content: 'Reviewed AI proposal' }] }, missingInformation: ['Confirm the actual refund window.'], exposedCaseIds: [], payloadExpired: false } : null),
}));
vi.mock('./SkillFileEditor', () => ({ default: ({ file, onChange }) => <textarea aria-label="File content" value={file.content} onChange={(event) => onChange(event.target.value)} /> }));
vi.mock('./DraftChecksPanel', () => ({ default: () => null }));
vi.mock('./PublishDialog', () => ({ default: () => null }));
vi.mock('src/sections/skills/components/FileTree', () => ({ default: () => null }));
vi.mock('src/sections/skills/components/TextDiff', () => ({ default: ({ fromText, toText }) => <div>{fromText} → {toText}</div> }));
vi.mock('src/sections/skills/tests/EditorTestsPanel', () => ({ default: ({ draft, isDirty }) => <output aria-label="Session snapshot">r{draft.revision} {isDirty ? 'dirty' : 'saved'}</output> }));
vi.mock('notistack', () => ({ enqueueSnackbar: vi.fn() }));

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal('crypto', webcrypto);
  api.scope = 'org:workspace:owner'; api.snapshot = null;
  api.save.mockImplementation(async (input) => ({ ...input, revision: input.revision + 1 }));
  api.preview.mockImplementation(async (input) => { api.snapshot = input; return plan; });
  api.create.mockResolvedValue({ id: 'proposal' });
  api.apply.mockResolvedValue({ ...initial, revision: 6, files: [{ path: 'SKILL.md', content: 'Reviewed AI proposal' }] });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const tree = () => <MemoryRouter initialEntries={['/dashboard/skills/skill/edit?assist=draft']}><Routes><Route path="/dashboard/skills/:skillId/edit" element={<SkillEditorView />} /></Routes></MemoryRouter>;
async function mount() {
  const view = render(tree());
  await screen.findByLabelText('Task and use / non-use boundary');
  fireEvent.change(screen.getByLabelText('Task and use / non-use boundary'), { target: { value: 'Answer refund questions only from the supplied policy.' } });
  fireEvent.change(screen.getByLabelText('Actual policy, examples and constraints'), { target: { value: 'Ask questions for missing facts.' } });
  fireEvent.mouseDown(screen.getByLabelText('Workspace model'));
  fireEvent.click(await screen.findByRole('option', { name: 'fixture-model' }));
  fireEvent.change(screen.getByLabelText('Estimated-cost stop (USD)'), { target: { value: '1.00' } });
  return view;
}
const preview = () => fireEvent.click(screen.getByRole('button', { name: 'Preview data and spend' }));
async function generate() {
  await screen.findByText('Confirm outbound processing');
  fireEvent.click(screen.getByRole('checkbox', { name: /I reviewed the outbound data/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Generate proposal' }));
  await screen.findByText('Review the suggestion');
}

it('flushes an older save plus later local edits before preparing assistance', async () => {
  let finish;
  api.save.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  await mount();
  fireEvent.change(screen.getByLabelText('File content'), { target: { value: 'Older edit' } });
  await waitFor(() => expect(api.save).toHaveBeenCalledTimes(1), { timeout: 2500 });
  fireEvent.change(screen.getByLabelText('File content'), { target: { value: 'Latest edit' } });
  preview();
  expect(api.preview).not.toHaveBeenCalled();
  await act(async () => { finish({ ...initial, revision: 5, files: [{ path: 'SKILL.md', content: 'Older edit' }] }); });
  await screen.findByText('Confirm outbound processing');
  expect(api.save).toHaveBeenLastCalledWith(expect.objectContaining({ revision: 5, files: [{ path: 'SKILL.md', content: 'Latest edit' }] }));
  expect(api.preview).toHaveBeenCalledWith(expect.objectContaining({ revision: 6, expectedDigest: expect.stringMatching(/^sha256:[a-f0-9]{64}$/) }));
  expect(api.create).not.toHaveBeenCalled();
});

it('does not preview or generate after a flush conflict and keeps authored inputs', async () => {
  api.save.mockRejectedValueOnce({ code: 'draft_conflict' });
  await mount();
  preview();
  await screen.findByText(/Save the draft and resolve any conflict/);
  expect(api.preview).not.toHaveBeenCalled();
  expect(api.create).not.toHaveBeenCalled();
  expect(screen.getByLabelText('Task and use / non-use boundary')).toHaveValue('Answer refund questions only from the supplied policy.');
});

it('marks an in-flight preview stale on the next local edit without losing inputs', async () => {
  let finish;
  api.preview.mockImplementationOnce((input) => { api.snapshot = input; return new Promise((resolve) => { finish = resolve; }); });
  await mount();
  preview();
  await waitFor(() => expect(api.preview).toHaveBeenCalled());
  fireEvent.change(screen.getByLabelText('File content'), { target: { value: 'Changed while previewing' } });
  await act(async () => { finish(plan); });
  expect(await screen.findByText(/For earlier content/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('checkbox', { name: /I reviewed the outbound data/ }));
  expect(screen.getByRole('button', { name: 'Generate proposal' })).toBeDisabled();
  expect(api.create).not.toHaveBeenCalled();
});

it('applies only after explicit review and reloads the single live editor session', async () => {
  await mount();
  preview();
  await generate();
  expect(screen.getByText('Confirm the actual refund window.')).toBeInTheDocument();
  expect(screen.getByLabelText('File content')).toHaveValue('Saved approved policy');
  expect(api.apply).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Apply to draft' }));
  await waitFor(() => expect(screen.getByLabelText('File content')).toHaveValue('Reviewed AI proposal'));
  expect(api.apply).toHaveBeenCalledWith({ revision: 5, expectedDigest: api.snapshot.expectedDigest });
  expect(screen.getByLabelText('Session snapshot')).toHaveTextContent('r6 saved');
  expect(api.save).toHaveBeenCalledTimes(1);
});

it('does not repopulate assistance after switching the session scope during preview', async () => {
  let finish;
  api.preview.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  const view = await mount();
  preview();
  await waitFor(() => expect(api.preview).toHaveBeenCalled());
  api.scope = 'org:other-workspace:other-owner';
  view.rerender(tree());
  await screen.findByLabelText('Task and use / non-use boundary');
  await act(async () => { finish(plan); });
  expect(screen.queryByText('Confirm outbound processing')).not.toBeInTheDocument();
  expect(screen.getByLabelText('Task and use / non-use boundary')).toHaveValue('');
  expect(api.create).not.toHaveBeenCalled();
});
