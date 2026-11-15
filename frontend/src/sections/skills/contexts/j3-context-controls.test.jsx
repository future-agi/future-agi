import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import ContextTestsPanel from './ContextTestsPanel';
import ContextDependentsPanel from './ContextDependentsPanel';
import ContextConsumersView from './ContextConsumersView';
import ContextDetailView from './ContextDetailView';
import ContextLibraryView from './ContextLibraryView';

const state = vi.hoisted(() => ({ isViewer: false, identity: 'org:workspace:author', prepare: vi.fn(), create: vi.fn(), previewLabel: vi.fn(), submitLabel: vi.fn(), dependents: null, history: { data: { count: 0, as_of: 'now', total_pages: 1, results: [] }, refetch: () => {} } }));
vi.mock('../utils/access', () => ({ useSkillsAccess: () => ({ isViewer: state.isViewer, canWrite: !state.isViewer }) }));
vi.mock('src/api/skills/r3-spend', () => ({ useSkillsSpendPolicy: () => ({}) }));
vi.mock('src/api/skills/testing', () => ({
  useSkillV11Scope: () => ({ identity: state.identity, scope: { userId: '00000000-0000-4000-8000-000000000001' } }),
  useSkillTestingOptions: () => ({ data: { enabled: true, models: [{ modelId: 'gpt-4o-mini', canRun: true }], limits: { profiles: { quick: { maxTotalTokens: 150000 } } } } }),
  isActiveSkillJob: () => false,
}));
vi.mock('src/api/skills/context-testing', () => ({
  contextTestCandidate: (draft) => ({ kind: 'draft', revision: draft.revision, digest: draft.digest }),
  usePreviewContextTest: () => ({ mutateAsync: state.prepare }),
  useCreateContextTest: () => ({ mutateAsync: state.create }),
  useContextTestRuns: () => ({ data: { results: [] } }),
  useContextTestRun: () => ({}), useContextTestResults: () => ({}), useContextTestAttempt: () => ({}), useCancelContextTest: () => ({}),
  // The unified Tests panel also mounts regression cases and the application test; no hosted runtime here.
  useContextTestSuite: () => ({ data: { revision: 1, cases: [] } }), useSaveContextTestSuite: () => ({ mutateAsync: vi.fn() }),
  useNativeTargets: () => ({ data: { runtime: { available: false, runtime: null, evidenceClass: null }, targets: [] } }),
  useNativeRuns: () => ({ data: [] }), useNativeRun: () => ({}), useRuntimeConfiguration: () => ({}),
  useCreateNativeTarget: () => ({}), useQualifyNativeTarget: () => ({}), usePreviewNativeRun: () => ({}), useCreateNativeRun: () => ({}), usePrepareReleasePacket: () => ({}),
}));
vi.mock('src/api/skills/evidence-annotations', () => ({ useEvidenceReference: () => ({}), useEvidenceAnnotations: () => ({}), useAppendEvidenceAnnotation: () => ({}) }));
vi.mock('src/auth/hooks', () => ({ useAuthContext: () => ({ user: { id: 'user' } }) }));
vi.mock('src/api/skills/context-dependents', () => ({ useContextDependents: (_skill, { view }) => (view === 'history' ? state.history : state.dependents) }));
vi.mock('src/api/skills', () => ({ useSkill: () => ({}) }));
vi.mock('src/api/skills/context-skill-inventory', () => ({ useLinkedSkillUpstream: () => ({}) }));
const digest = `sha256:${'a'.repeat(64)}`;
const version = { id: 'parent-v2', number: 2, digest };
const preview = { fingerprint: 'preview', attemptCount: 2, estimate: { upperBoundUsd: '0.01' }, providerDisclosure: { taskProvider: 'openai' }, inventory: [], outboundFindings: [] };
const labelPreview = { fingerprint: 'packet', packet: { candidate_version_id: version.id, candidate_digest: digest, transition: { label: 'production', version: 2, expected_version: 1, expected_release_revision: 4, expected_security_revision: 0 } } };
beforeEach(() => { state.isViewer = false; vi.clearAllMocks(); window.localStorage.clear(); state.identity = 'org:workspace:author'; state.prepare.mockResolvedValue(preview); state.create.mockResolvedValue({ id: 'run' }); state.previewLabel.mockResolvedValue(labelPreview); state.submitLabel.mockResolvedValue({ id: 'request' }); });
afterEach(cleanup);
const mount = (element) => render(<MemoryRouter>{element}</MemoryRouter>);
async function prepare() {
  fireEvent.change(screen.getByRole('textbox', { name: 'Test request' }), { target: { value: 'Describe purchasing policy' } });
  fireEvent.change(screen.getByRole('textbox', { name: 'Expected answer contains' }), { target: { value: 'approved' } });
  fireEvent.click(screen.getByRole('checkbox', { name: 'I reviewed this request and its expected answer.' }));
  fireEvent.mouseDown(screen.getByRole('combobox', { name: 'Task model' }));
  fireEvent.click(await screen.findByRole('option', { name: 'gpt-4o-mini' }));
  fireEvent.change(screen.getByRole('textbox', { name: 'Maximum estimated cost (USD)' }), { target: { value: '1' } });
  fireEvent.click(screen.getByRole('button', { name: 'Preview answer check' }));
  await screen.findByRole('button', { name: 'Run answer check' });
}
function reviewInventory() {
  fireEvent.click(screen.getByRole('checkbox', { name: 'The files sent to the model contain no answer keys or grading material.' }));
  fireEvent.click(screen.getByRole('checkbox', { name: 'I reviewed every file and input that will be sent to the model.' }));
  fireEvent.click(screen.getByRole('checkbox', { name: 'I acknowledge provider processing of this reviewed context and test request.' }));
}
it('uses context semantics, a required without-context baseline, and explicit reviewed provider processing', async () => {
  mount(<ContextTestsPanel contextId="parent" draft={{ revision: 5, digest }} />);
  await prepare();
  expect(state.prepare.mock.calls[0][0]).toMatchObject({ harness: 'contexts-text-v1.2', candidate: { kind: 'draft', revision: 5, digest }, competitors: [], baselines: [{ kind: 'without_context' }], cases: { cases: [{ expected_trigger: null }] } });
  expect(screen.getByRole('button', { name: 'Run answer check' })).toBeDisabled();
  expect(state.create).not.toHaveBeenCalled();
  reviewInventory();
  fireEvent.click(screen.getByRole('button', { name: 'Run answer check' }));
  await waitFor(() => expect(state.create).toHaveBeenCalledOnce());
  expect(state.create.mock.calls[0][0].configuration.candidate.digest).toBe(digest);
});
it('does not let a late preview authorize a changed candidate', async () => {
  const view = mount(<ContextTestsPanel contextId="parent" version={version} />);
  await prepare(); reviewInventory();
  view.rerender(<MemoryRouter><ContextTestsPanel contextId="parent" version={{ ...version, number: 3, digest: `sha256:${'b'.repeat(64)}` }} /></MemoryRouter>);
  expect(screen.getByRole('button', { name: 'Run answer check' })).toBeDisabled();
  expect(screen.getByText(/saved version changed/)).toBeVisible();
  expect(state.create).not.toHaveBeenCalled();
});
it('recovers an uncertain admission using the identical body and key, without a second preview', async () => {
  state.create.mockRejectedValueOnce(new Error('response lost')).mockResolvedValueOnce({ id: 'original-run' });
  mount(<ContextTestsPanel contextId="parent" version={version} />);
  await prepare(); reviewInventory();
  fireEvent.click(screen.getByRole('button', { name: 'Run answer check' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Recover original context test' }));
  await waitFor(() => expect(state.create).toHaveBeenCalledTimes(2));
  expect(state.create.mock.calls[1][0]).toEqual(state.create.mock.calls[0][0]);
  expect(state.prepare).toHaveBeenCalledOnce();
});
it('does not carry the last test request across logins', () => {
  window.localStorage.setItem('skills.contextTest.last:org:workspace:someone-else', JSON.stringify({ prompt: 'private request' }));
  mount(<ContextTestsPanel contextId="parent" version={version} />);
  expect(screen.getByRole('textbox', { name: 'Test request' })).toHaveValue('');
  window.localStorage.clear();
});
it('shows dependent failures rather than claiming an empty inventory', () => {
  state.dependents = { isError: true, refetch: vi.fn() };
  mount(<ContextDependentsPanel skillId="child" />);
  expect(screen.getByText('Dependents unavailable')).toBeVisible();
  expect(screen.queryByText('No published references')).not.toBeInTheDocument();
});

it('labels delivered sessions with their exact release number, never a guessed one', () => {
  const fact = (versionId, sessionId) => ({ versionId, digest, observedAt: 'now', origin: 'client_reported', sessionId });
  const row = (id, loaded) => ({ binding: { id, name: `runtime-${id}`, selector: { label: 'production' } }, desired: null, resolved: null, installed: null, discovered: null, loaded, states: ['mixed_sessions', 'old_thread_pinned'], observationAgeSeconds: 1 });
  const data = { results: [row('known', fact('parent-v2', 'fresh-session')), row('unknown', fact('foreign-version', null))], page: 1, pageSize: 25, count: 2, asOf: 'now' };
  mount(<ContextConsumersView scopeKey="scope" state="ready" data={data} versionNumbers={{ 'parent-v2': 2 }} onPageChange={() => {}} onWindowChange={() => {}} />);
  expect(screen.getByText('v2')).toBeVisible();
  expect(screen.getByTitle('foreign-version')).toBeVisible();
  expect(screen.queryByText('v1')).not.toBeInTheDocument();
  expect(screen.getAllByText('Old thread retained')).toHaveLength(2);
  fireEvent.click(screen.getByRole('button', { name: 'Inspect runtime-known' }));
  fireEvent.click(screen.getByRole('button', { name: 'Details', exact: true }));
  expect(screen.getByTitle('fresh-session')).toBeVisible();
});

it.each([['versions', 'Label request'], ['consumers', 'Consumer rows'], ['unknown-tab', null]])('opens the shared %s tab link without guessing', (tab, body) => {
  const scopeKey = 'scope';
  const detail = { scopeKey, query: { scopeKey, data: { context: { id: 'c', name: 'parent', latestVersion: 2, labels: [] } } }, versionQuery: { scopeKey, data: { version, descriptor: { description: 'Purpose', links: [] }, files: [{ path: 'AGENTS.md', origin: { kind: 'owned' } }] } }, fileQuery: { scopeKey, data: { path: 'AGENTS.md', versionId: version.id, text: '# Root' } }, versions: [version] };
  mount(<ContextDetailView {...detail} initialTab={tab} sections={{ versions: <p>Label request</p>, consumers: <p>Consumer rows</p> }} />);
  if (body) expect(screen.getByText(body)).toBeVisible();
  else expect(screen.getByRole('heading', { name: 'Root', exact: true })).toBeVisible();
});

it('hides viewer creation and import actions in both empty and populated libraries', () => {
  state.isViewer = true;
  const props = { scopeKey: 'scope', query: { scopeKey: 'scope', data: { results: [], count: 0, page: 1, pageSize: 25 } }, importActions: { onCreate: vi.fn() } };
  const view = mount(<ContextLibraryView {...props} />);
  expect(screen.queryByRole('button', { name: /Create agent context|New agent context|Import AGENTS.md/ })).not.toBeInTheDocument();
  view.rerender(<MemoryRouter><ContextLibraryView {...props} query={{ ...props.query, data: { ...props.query.data, results: [{ id: 'c', name: 'support-agent', labels: [] }], count: 1 } }} /></MemoryRouter>);
  expect(screen.queryByRole('button', { name: /New agent context|Import AGENTS.md/ })).not.toBeInTheDocument();
});
it.each([true, false])('disables editing for viewer or denied server permission (viewer=%s)', (viewer) => {
  state.isViewer = viewer;
  mount(<ContextDetailView scopeKey="scope" query={{ scopeKey: 'scope', data: { context: { id: 'c', name: 'support-agent', latestVersion: 2 }, releaseState: { permissions: { canPublish: viewer } } } }} versionQuery={{ scopeKey: 'scope', data: { version } }} fileQuery={{ scopeKey: 'scope' }} versions={[version]} onEditViewed={vi.fn()} />);
  expect(screen.getByRole('button', { name: 'Edit latest v2' })).toBeDisabled();
  if (viewer) expect(screen.getByText("Viewers can't edit. Ask a workspace admin for edit access.")).toBeVisible();
});
it('keeps a return link for a missing context (the shared not-found recipe)', () => {
  mount(<ContextDetailView scopeKey="scope" query={{ scopeKey: 'scope', error: { status: 404 } }} versionQuery={{ scopeKey: 'scope' }} fileQuery={{ scopeKey: 'scope' }} />);
  expect(screen.getByText('Agent context not found')).toBeVisible();
  expect(screen.getByText('It may have been archived or belong to another workspace.')).toBeVisible();
  expect(screen.getByRole('link', { name: 'Back to Agent contexts' })).toHaveAttribute('href', '/dashboard/skills/contexts');
});
