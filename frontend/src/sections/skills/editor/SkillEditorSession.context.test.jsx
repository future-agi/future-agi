vi.mock('./useDraftChecks', () => ({ default: () => ({ current: true, pending: false, checks: { summary: { errors: 0, warnings: 0 }, items: [] } }) }));
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom';
import SkillEditorSession from './SkillEditorSession';

vi.mock('src/api/skills/context-skill-inventory', () => ({ useLinkedSkillUpstream: () => ({}) }));

const legacy = vi.hoisted(() => ({ save: vi.fn(), discard: vi.fn() }));
vi.mock('src/api/skills', () => ({ useSaveSkillDraft: () => ({ mutateAsync: legacy.save }), useDiscardSkillDraft: () => ({ mutateAsync: legacy.discard }), getSkillErrorCode: (error) => error.code }));
vi.mock('src/api/skills/testing', () => ({ useSkillV11Scope: () => ({ identity: 'org:workspace:user:context', enabled: true }), useSkillTestingOptions: () => ({ data: { assistEnabled: false } }) }));
vi.mock('./SkillFileEditor', () => ({ default: ({ file, readOnly, onChange }) => <textarea aria-label="Context file source" value={file.content} readOnly={readOnly} onChange={(event) => onChange(event.target.value)} /> }));
vi.mock('./PublishDialog', () => ({ default: () => <p>Legacy publication must not mount</p> }));
vi.mock('../tests/EditorTestsPanel', () => ({ default: () => null }));
vi.mock('../assist/AssistPanel', () => ({ default: () => null }));
vi.mock('./DraftChecksPanel', () => ({ default: () => null }));
vi.mock('src/sections/skills/components/FileTree', () => ({ default: ({ files, onSelect, onDelete }) => <nav>{files.map((file) => <button key={file.path} onClick={() => onSelect(file.path)}>{file.path}</button>)}<button onClick={() => onDelete('references/policy.md')}>Confirm supporting-file deletion</button></nav> }));

const recovery = `sha256:${'b'.repeat(64)}`;
const initial = { revision: 4, baseVersion: 2, expectedHeadVersion: 6, files: [{ path: 'AGENTS.md', content: 'Standing policy' }, { path: 'references/policy.md', content: 'Keep this until checkpointed' }], note: '', description: 'Purpose', contextSpec: { description: 'Purpose', links: [] }, resolvedLock: { schema: 1, links: [] }, digest: `sha256:${'a'.repeat(64)}`, recoveryDigest: recovery };
const skill = { id: 'context', name: 'refund-context', latestVersion: 6 };
function mount(overrides = {}, props = {}) {
  const resourceApi = {
    saveDraft: vi.fn(async (input) => ({ ...initial, ...input, revision: input.revision + 1 })),
    saveInput: (draft, revision) => ({ revision, baseVersion: draft.baseVersion, files: draft.files, note: draft.note, description: draft.description }),
    discardDraft: vi.fn(), checkpointDraft: vi.fn(async () => ({ checkpoint: { id: 'checkpoint' } })),
    ...overrides,
  };
  render(<MemoryRouter><SkillEditorSession kind="agent_context" scopeKey="org:workspace:user:context" skill={skill} initialDraft={initial} resourceApi={resourceApi} refetchDraft={vi.fn()} {...props} /></MemoryRouter>);
  return resourceApi;
}
beforeEach(() => { vi.clearAllMocks(); });
afterEach(cleanup);

it('keeps the AGENTS root editable and never invokes the legacy publication API', () => {
  mount();
  expect(screen.getByLabelText('Context file source')).toHaveValue('Standing policy');
  expect(screen.getByRole('button', { name: 'Continue to publish' })).toBeDisabled();
  expect(screen.queryByText('Legacy publication must not mount')).not.toBeInTheDocument();
  expect(legacy.save).not.toHaveBeenCalled();
});

it('does not display the owned root or a stale linked chunk under a mounted path', async () => {
  const select = vi.fn();
  mount({}, { readOnlyFiles: [{ path: 'skills/refunds/SKILL.md' }], readOnlyFileQuery: { scopeKey: 'org:workspace:user:context', data: { path: 'skills/refunds/SKILL.md', revision: 3, text: 'Stale linked bytes' } }, onSelectReadOnlyFile: select });
  fireEvent.click(screen.getByRole('button', { name: 'skills/refunds/SKILL.md' }));
  await waitFor(() => expect(select).toHaveBeenCalledWith('skills/refunds/SKILL.md', 4));
  expect(screen.queryByLabelText('Context file source')).not.toBeInTheDocument();
  expect(screen.queryByText('Stale linked bytes')).not.toBeInTheDocument();
});

it('deletes only after a checkpoint of the acknowledged recovery digest succeeds', async () => {
  const api = mount();
  fireEvent.click(screen.getByRole('button', { name: 'Confirm supporting-file deletion' }));
  await waitFor(() => expect(api.checkpointDraft).toHaveBeenCalledOnce());
  expect(api.checkpointDraft.mock.calls[0][0]).toEqual({ revision: 4, expectedDigest: recovery, reason: 'deletion', suspend: false });
  await waitFor(() => expect(screen.queryByRole('button', { name: 'references/policy.md' })).not.toBeInTheDocument());
  expect(legacy.save).not.toHaveBeenCalled();
});

it('retains the original operation key after an unknown checkpoint and never deletes newer content', async () => {
  const checkpointDraft = vi.fn().mockRejectedValueOnce(new Error('Response lost')).mockResolvedValueOnce({ checkpoint: { id: 'checkpoint' } });
  mount({ checkpointDraft });
  fireEvent.click(screen.getByRole('button', { name: 'Confirm supporting-file deletion' }));
  await screen.findByText(/Deletion was not applied/);
  expect(screen.getByRole('button', { name: 'Retry' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'references/policy.md' })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Confirm supporting-file deletion' }));
  await waitFor(() => expect(checkpointDraft).toHaveBeenCalledTimes(2));
  expect(checkpointDraft.mock.calls[1]).toEqual(checkpointDraft.mock.calls[0]);
  await screen.findByText(/Deletion was not applied/);
  expect(screen.getByRole('button', { name: 'references/policy.md' })).toBeInTheDocument();
});

function ReviewRoute(props) {
  const navigate = useNavigate();
  return <SkillEditorSession kind="agent_context" scopeKey="org:workspace:user:context" skill={skill} initialDraft={initial} onReview={() => navigate('/review')} {...props} />;
}

it('opens the review after saving without asking to leave unsaved changes', async () => {
  const resourceApi = { saveDraft: vi.fn(async (input) => ({ ...initial, ...input, revision: input.revision + 1 })),
    saveInput: (draft, revision) => ({ revision, baseVersion: draft.baseVersion, files: draft.files, note: draft.note, description: draft.description }), discardDraft: vi.fn() };
  render(<MemoryRouter initialEntries={['/edit']}><Routes>
    <Route path="/edit" element={<ReviewRoute resourceApi={resourceApi} refetchDraft={vi.fn()} />} />
    <Route path="/review" element={<p>Review page</p>} />
  </Routes></MemoryRouter>);
  fireEvent.change(screen.getByLabelText('Context file source'), { target: { value: 'Edited policy' } });
  fireEvent.click(screen.getByRole('button', { name: 'Continue to publish' }));
  expect(await screen.findByText('Review page')).toBeInTheDocument();
  expect(screen.queryByText('Leave with unsaved changes?')).not.toBeInTheDocument();
  expect(resourceApi.saveDraft).toHaveBeenCalledOnce();
});

it('keeps the unsaved-changes guard when no review route is available', async () => {
  mount({}, { onReview: undefined });
  fireEvent.change(screen.getByLabelText('Context file source'), { target: { value: 'Edited policy' } });
  expect(screen.getByRole('button', { name: 'Continue to publish' })).toBeDisabled();
});

it('describes the checks in plain words, without internal terms', () => {
  mount();
  expect(screen.getByText('Checks cover the saved context, including its linked skills.')).toBeInTheDocument();
  expect(screen.queryByText(/accepted dependencies/)).not.toBeInTheDocument();
});
