vi.mock('./useDraftChecks', () => ({ default: () => ({ current: true, pending: false, checks: { summary: { errors: 0, warnings: 0 }, items: [] } }) }));
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom';
import SkillEditorSession, { CONTEXT_EDITOR_LINES, contextEditorLayout } from './SkillEditorSession';

vi.mock('src/api/skills/context-skill-inventory', () => ({ useLinkedSkillUpstream: () => ({}) }));

const legacy = vi.hoisted(() => ({ save: vi.fn(), discard: vi.fn() }));
vi.mock('src/api/skills', () => ({ useSaveSkillDraft: () => ({ mutateAsync: legacy.save }), useDiscardSkillDraft: () => ({ mutateAsync: legacy.discard }), getSkillErrorCode: (error) => error.code }));
vi.mock('src/api/skills/testing', () => ({ useSkillV11Scope: () => ({ identity: 'org:workspace:user:context', enabled: true }), useSkillTestingOptions: () => ({ data: { assistEnabled: false } }) }));
vi.mock('./SkillFileEditor', () => ({ default: ({ file, readOnly, onChange, minHeight }) => <textarea aria-label="Context file source" data-min-height={minHeight} value={file.content} readOnly={readOnly} onChange={(event) => onChange(event.target.value)} /> }));
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

// jsdom does no layout and ignores media queries, so the layout is checked on what the page declares:
// the floors computed from the acceptance line counts, the rules emotion writes for them, and the
// editor's own height floor. The 20-line and 12-line captures on the VM are the proof of the result.
const declaredCss = () => [...document.querySelectorAll('style')].map((node) => node.textContent || '').join('')
  + [...document.styleSheets].flatMap((sheet) => [...sheet.cssRules]).map((rule) => rule.cssText).join('');
const lineHeight = 21;

it('budgets at least 20 desktop lines and 12 phone lines for the AGENTS.md text area', () => {
  expect(CONTEXT_EDITOR_LINES).toEqual({ desktop: 20, phone: 12 });
  // The text itself, without the file-name bar or the Edit/Preview switch around it.
  expect(contextEditorLayout.desktopFloor).toBeGreaterThanOrEqual(CONTEXT_EDITOR_LINES.desktop * lineHeight);
  expect(contextEditorLayout.mdFloor).toBeGreaterThanOrEqual(contextEditorLayout.desktopFloor + 200);
  expect(contextEditorLayout.phoneRow).toBeGreaterThanOrEqual(CONTEXT_EDITOR_LINES.phone * lineHeight);
  expect(contextEditorLayout.editorFloor).toBeGreaterThanOrEqual(CONTEXT_EDITOR_LINES.phone * lineHeight);
});

it('gives the editor grid a fixed, non-shrinking height and the text area its own floor', () => {
  mount();
  const grid = document.querySelector('[data-slot="editor-grid"]');
  expect(grid).toHaveStyle({ flexShrink: '0' });
  expect(screen.getByLabelText('Context file source')).toHaveAttribute('data-min-height', String(contextEditorLayout.editorFloor));
  const css = declaredCss().replace(/\s+/g, '');
  expect(css).toContain(`height:clamp(${contextEditorLayout.desktopFloor}px,`);
  expect(css).toContain(`height:clamp(${contextEditorLayout.mdFloor}px,`);
  expect(css).toContain(`minmax(${contextEditorLayout.phoneRow}px,1fr)`);
});

it('keeps linked skills and settings in a closed summary row above the editor', () => {
  mount();
  const row = document.querySelector('[data-slot="context-details"]');
  const grid = document.querySelector('[data-slot="editor-grid"]');
  expect(row.compareDocumentPosition(grid) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(row).not.toContainElement(grid);
  const toggle = screen.getByRole('button', { name: 'Linked skills and settings' });
  expect(toggle).toHaveAttribute('aria-expanded', 'false');
  expect(row).toHaveTextContent('0 linked skills');
  // The one-line purpose is edited in the summary row itself, never inside the closed panel.
  expect(screen.getByLabelText('Context purpose', { selector: 'input' })).toBeVisible();
  expect(document.getElementById('context-details-panel')).not.toContainElement(screen.getByLabelText('Context purpose', { selector: 'input' }));
  // Closed means hidden, not unmounted: the panels keep their state and the editor never moves into them.
  expect(screen.queryByRole('region', { name: 'Linked skills and settings' })).not.toBeInTheDocument();
  expect(screen.getByLabelText('Context file source')).toBeVisible();
});

it('opens the row to show the linked skills, the head review and the purpose, and closes it again', () => {
  mount();
  const toggle = screen.getByRole('button', { name: 'Linked skills and settings' });
  fireEvent.click(toggle);
  expect(toggle).toHaveAttribute('aria-expanded', 'true');
  const region = screen.getByRole('region', { name: 'Linked skills and settings' });
  expect(region).toBeVisible();
  expect(region).toHaveTextContent('Linked skills (0)');
  expect(region).toHaveTextContent('Compare with the latest version');
  expect(screen.getByLabelText('Context purpose')).toBeVisible();
  fireEvent.click(toggle);
  expect(screen.queryByRole('region', { name: 'Linked skills and settings' })).not.toBeInTheDocument();
});

it('opens the row on arrival when migration-proposed skills are waiting for review, and names pending work', () => {
  mount({}, { contextControls: { proposedLinks: [{ skillId: 'refunds', version: 3 }] } });
  expect(screen.getByRole('button', { name: 'Linked skills and settings' })).toHaveAttribute('aria-expanded', 'true');
  expect(document.querySelector('[data-slot="context-details"]')).toHaveTextContent('Proposed skills need review');
  expect(document.querySelector('[data-slot="context-details"]')).toHaveTextContent('Latest is v6');
});

it('edits the purpose in the summary row without opening it, and keeps it across opening and closing', () => {
  mount();
  const toggle = screen.getByRole('button', { name: 'Linked skills and settings' });
  fireEvent.change(screen.getByLabelText('Context purpose'), { target: { value: 'New purpose' } });
  expect(toggle).toHaveAttribute('aria-expanded', 'false');
  fireEvent.click(toggle);
  fireEvent.click(toggle);
  expect(screen.getByLabelText('Context purpose')).toHaveValue('New purpose');
});

it('offers the head comparison in the behind-latest notice while the row is closed', async () => {
  const previewHead = vi.fn(async () => null);
  mount({}, { contextControls: { previewHead } });
  const toggle = screen.getByRole('button', { name: 'Linked skills and settings' });
  expect(toggle).toHaveAttribute('aria-expanded', 'false');
  // One visible control: the notice's while closed, the head review's once open.
  expect(screen.getAllByRole('button', { name: 'Compare with latest version' })).toHaveLength(1);
  fireEvent.click(screen.getByRole('button', { name: 'Compare with latest version' }));
  expect(toggle).toHaveAttribute('aria-expanded', 'true');
  expect(screen.getAllByRole('button', { name: 'Compare with latest version' })).toHaveLength(1);
  // It starts the same comparison the panel offers; nothing is acknowledged by it.
  await waitFor(() => expect(previewHead).toHaveBeenCalledTimes(1));
});
