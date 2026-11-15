/* eslint-env node */
import fs from 'node:fs';
import ts from 'typescript';
import PropTypes from 'prop-types';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, useLocation, useRoutes } from 'react-router-dom';
import { HelmetProvider } from 'react-helmet-async';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import axios from 'src/utils/axios';
import SkillsWorkspaceLayout from 'src/sections/skills/components/SkillsWorkspaceLayout';
import ContextLibraryPage from './contexts/ContextLibraryPage';
import ContextDetailPage from './contexts/ContextDetailPage';
import ContextEditorPage from './contexts/ContextEditorPage';
import ContextReviewPage from './contexts/ContextReviewPage';
import ContextCampaignPage from './contexts/ContextCampaignPage';
import SkillsReviewsPage from './SkillsReviewsPage';
import ContextTraceCaptureAction from 'src/sections/skills/contexts/ContextTraceCaptureAction';
import FalconAIFab from 'src/sections/falcon-ai/components/FalconAIFab';

const identity = vi.hoisted(() => ({ workspace: '00000000-0000-4000-8000-000000000001', campaigns: false }));
vi.mock('src/auth/hooks', () => ({ useAuthContext: () => ({ authenticated: true, user: { id: 'author' } }) }));
// The signed-in author is a workspace member (role level 3), so Skills shows write actions; the server stays the authority.
vi.mock('src/contexts/OrganizationContext', () => ({ useOrganization: () => ({ isReady: true, currentOrganizationId: '00000000-0000-4000-8000-000000000002', orgLevel: 3 }) }));
vi.mock('src/contexts/WorkspaceContext', () => ({ useWorkspace: () => ({ isReady: true, currentWorkspaceId: identity.workspace }) }));
// Only the HTTP boundary is replaced; route/provider/controllers/hooks stay real.
vi.mock('src/utils/axios', () => ({ default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() } }));
// jsdom cannot mount Monaco's canvas. Only the editor primitive is replaced;
// the production controller, draft session and revision fences remain real.
vi.mock('@monaco-editor/react', () => ({ useMonaco: () => null, loader: { init: () => Promise.resolve(null) } }));
vi.mock('src/components/form-code-editor', () => {
  function FormCodeEditor({ value, readOnly, label, onChange }) { return <textarea aria-label={label} value={value} readOnly={readOnly} onChange={(event) => onChange(event.target.value)} />; }
  FormCodeEditor.propTypes = { value: PropTypes.string, readOnly: PropTypes.bool, label: PropTypes.string, onChange: PropTypes.func };
  return { FormCodeEditor };
});
const id = '00000000-0000-4000-8000-000000000003';
const versionId = '00000000-0000-4000-8000-000000000004';
const digest = `sha256:${'a'.repeat(64)}`;
const context = { id, kind: 'agent_context', name: 'policy-context', description: 'Standing policy', tags: [], labels: [], latest_version: 4, metadata_revision: 1, release_revision: 1, security_revision: 0, protected_label_names: [], has_draft: false };
const permissions = { can_publish: true, can_promote: true, can_manage_protection: false, can_quarantine: false, can_issue_credentials: false };
const releaseState = { latest_version: 4, release_revision: 1, security_revision: 0, protected_label_names: [], labels: [], permissions };
const page = (results) => ({ results, count: results.length, page: 1, page_size: 25, total_pages: results.length ? 1 : 0 });
const envelope = (result) => ({ data: { result } });
const components = { ContextLibraryPage, ContextDetailPage, ContextEditorPage, ContextReviewPage, ContextCampaignPage, SkillsReviewsPage };
// Read the production dashboard declarations instead of maintaining a second
// route table or injecting test-only authoring/reviewer controllers.
function productionChildren() {
  const source = ts.createSourceFile('dashboard.jsx', fs.readFileSync('src/routes/sections/dashboard.jsx', 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.JSX);
  let skills;
  const visit = (node) => {
    if (ts.isObjectLiteralExpression(node) && node.properties.some((property) => property.name?.getText(source) === 'path' && property.initializer?.text === 'skills')) skills = node;
    ts.forEachChild(node, visit);
  };
  visit(source);
  const children = skills.properties.find((property) => property.name?.getText(source) === 'children').initializer;
  return children.elements.flatMap((node) => {
    const routePath = node.properties.find((property) => property.name?.getText(source) === 'path')?.initializer.text;
    const tag = node.properties.find((property) => property.name?.getText(source) === 'element')?.initializer.tagName?.getText(source);
    const Component = components[tag];
    return Component ? [{ path: routePath, element: <Component /> }] : [];
  });
}
function Routes() { return useRoutes([{ path: '/dashboard/skills', element: <SkillsWorkspaceLayout />, children: productionChildren() }]); }
let client;
const theme = createTheme();
const mount = (path) => render(<QueryClientProvider client={client}><HelmetProvider><ThemeProvider theme={theme}><MemoryRouter initialEntries={[path]}><Routes /></MemoryRouter></ThemeProvider></HelmetProvider></QueryClientProvider>);
beforeEach(() => {
  vi.clearAllMocks(); identity.campaigns = false; context.latest_version = 4;
  localStorage.setItem('context-file-view', 'source');
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  axios.get.mockImplementation(async (url) => {
    if (url.endsWith('/capabilities/')) return envelope({ campaigns_enabled: identity.campaigns });
    if (url.endsWith('/review-inbox/')) return envelope({ ...page([]), unread_count: 0 });
    if (url === '/model-hub/skills/contexts/') return envelope(page([context]));
    if (url === `/model-hub/skills/contexts/${id}/`) return envelope({ context, release_state: releaseState, latest: { id: versionId, number: 4, digest }, own_draft: null });
    if (url.endsWith('/versions/')) return envelope(page([{ id: versionId, number: 4, digest }]));
    if (url.endsWith('/consumers/')) return envelope({ ...page([]), as_of: '2026-11-09', coverage: 'declared_bindings' });
    if (url.endsWith('/inspection/')) return envelope({ descriptor: { kind: 'agent_context', name: context.name, description: context.description, links: [] }, manifest: { kind: 'agent_context', name: context.name, version: 4, version_id: versionId, digest, files: [{ path: 'AGENTS.md', sha256: 'hash', size_bytes: 15, origin: { kind: 'owned' } }], next_file_offset: null }, distribution_blocked: false });
    if (url.endsWith('/inspection/files/')) return envelope({ path: 'AGENTS.md', version_id: versionId, digest, sha256: 'hash', total_chars: 15, offset: 0, next_offset: null, eof: true, text: '# Exact policy' });
    if (url.endsWith('/draft/')) throw { statusCode: 404, code: 'draft_not_found' };
    if (url === '/model-hub/skills/') return envelope(page([]));
    throw new Error(`Unexpected HTTP read ${url}`);
  });
});
afterEach(() => { cleanup(); client.clear(); });
it('opens the real context controller through production navigation and reads exact-version bytes', async () => {
  mount('/dashboard/skills/contexts');
  fireEvent.click(await screen.findByRole('button', { name: 'policy-context' }));
  expect(await screen.findByText('# Exact policy')).toBeVisible();
  fireEvent.click(screen.getByRole('tab', { name: 'Connected apps' }));
  expect(await screen.findByText('No apps connected yet')).toBeVisible();
  fireEvent.click(screen.getByRole('tab', { name: 'Install' }));
  fireEvent.click(screen.getByRole('button', { name: 'Before you install' }));
  expect(screen.getByTitle(versionId)).toBeVisible();
  expect(screen.getByRole('tab', { name: 'Agent contexts' })).not.toBeDisabled();
});
// Capture starts from the selected span (SpanDetailPane mounts this action), not from a
// context page form: the physical identity is carried, never transcribed.
const traceRef = { projectId: id, traceId: 'trace', spanId: 'span', startTime: '2026-11-10T00:00:00.123456Z' };
const mountTraceCapture = () => render(<QueryClientProvider client={client}><MemoryRouter><ContextTraceCaptureAction traceRef={traceRef} contextHint={id} /></MemoryRouter></QueryClientProvider>);
it('captures an authorized trace from the span into its delivered context and shares only the reviewed redaction', async () => {
  const read = axios.get.getMockImplementation();
  const suite = { revision: 2, digest, cases: [], updated_at: null, updated_by: null, visibility: 'workspace' };
  axios.get.mockImplementation((url, config) => url.endsWith('/tests/') ? Promise.resolve(envelope(suite)) : read(url, config));
  const wireRef = { project_id: id, trace_id: 'trace', span_id: 'span', start_time: traceRef.startTime };
  const segments = [{ id: 'request-1', kind: 'request', text: 'Customer private request' }];
  axios.post.mockImplementation(async (url, body) => {
    if (url.endsWith('/captures/source/')) { expect(body).toEqual({ trace_ref: wireRef }); return envelope({ trace_ref: wireRef, source_fingerprint: digest, segments }); }
    if (url.endsWith('/captures/preview/')) { expect(body).toEqual({ trace_ref: wireRef, selected_segment_ids: ['request-1'] }); return envelope({ capture_id: versionId, expires_at: '2099-01-01T00:00:00Z', source_fingerprint: digest, recorded_context: null, segments, omitted: [], outbound_findings: [], replay_scope: 'text_only' }); }
    if (url.endsWith(`/captures/${versionId}/accept/`)) return envelope({ ...suite, revision: 3 });
    throw new Error(`Unexpected write ${url}`);
  });
  mountTraceCapture();
  fireEvent.click(screen.getByRole('button', { name: 'Save as test case' }));
  // No identity fields to transcribe: the span's identity is loaded into the authorized review.
  expect(screen.queryByRole('textbox', { name: 'Source trace ID' })).not.toBeInTheDocument();
  // Every supported segment starts selected; the reviewer deselects anything irrelevant.
  expect(await screen.findByRole('checkbox', { name: 'Customer request' })).toBeChecked();
  fireEvent.click(screen.getByRole('button', { name: 'Preview selected text' }));
  fireEvent.change(await screen.findByRole('textbox', { name: 'Redacted request' }), { target: { value: 'Redacted request' } });
  fireEvent.change(screen.getByRole('textbox', { name: 'Case name' }), { target: { value: 'Regression' } });
  fireEvent.change(screen.getByRole('textbox', { name: 'Expected answer must contain' }), { target: { value: 'Reviewed answer' } });
  fireEvent.click(screen.getByRole('checkbox', { name: /confirmed the expected behavior independently/ }));
  fireEvent.click(screen.getByRole('checkbox', { name: /Share this redacted request/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Save test case' }));
  await screen.findByText(/Test case saved/);
  const saved = axios.post.mock.calls.find(([url]) => url.endsWith('/accept/'));
  expect(saved[1]).toMatchObject({ suite_revision: 2, source_fingerprint: digest, acknowledge_workspace_sharing: true, case: { prompt: 'Redacted request', expected_trigger: null, reviewed: true, checks: [{ config: { text: 'Reviewed answer', case_sensitive: true } }] } });
  expect(saved[2].headers['Idempotency-Key']).toMatch(/^[a-f0-9-]{36}$/);
  expect(JSON.stringify(saved[1])).not.toContain('Customer private request');
  expect(axios.post.mock.calls.some(([url]) => /test-runs|publish/.test(url))).toBe(false);
});

it('does not offer context capture when the authoritative write permission is absent', async () => {
  const read = axios.get.getMockImplementation();
  axios.get.mockImplementation(async (url, config) => {
    const value = await read(url, config);
    if (url === `/model-hub/skills/contexts/${id}/`) value.data.result.release_state = { ...releaseState, permissions: { ...permissions, can_publish: false } };
    return value;
  });
  mountTraceCapture();
  fireEvent.click(screen.getByRole('button', { name: 'Save as test case' }));
  expect(await screen.findByText('Only a workspace author can capture cases into this context.')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Review selected span' })).toBeDisabled();
  expect(axios.post).not.toHaveBeenCalled();
});

it('retains the initially viewed release when background metadata advances latest', async () => {
  mount(`/dashboard/skills/contexts/${id}`);
  await screen.findByText('# Exact policy');
  context.latest_version = 5;
  await act(async () => { await client.invalidateQueries(); });
  await waitFor(() => expect(screen.getByText(/Latest v5/)).toBeVisible());
  expect(screen.getByRole('button', { name: 'Edit viewed v4' })).toBeVisible();
  expect(axios.get.mock.calls.some(([url]) => url.includes('/versions/5/inspection/'))).toBe(false);
});

it('mounts the real reviewer inbox without an injected Controller', async () => {
  mount('/dashboard/skills/contexts');
  fireEvent.click(screen.getByRole('tab', { name: 'Reviews' }));
  expect(await screen.findByText('No reviews waiting for you')).toBeVisible();
  expect(axios.get.mock.calls.some(([url]) => url.endsWith('/review-inbox/'))).toBe(true);
});
it('omits unavailable destinations and moves secondary pages into More', async () => {
  mount('/dashboard/skills/contexts');
  await screen.findByRole('button', { name: 'policy-context' });
  expect(screen.queryByRole('tab', { name: /Repository rollouts/ })).not.toBeInTheDocument();
  expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual(['Skills', 'Agent contexts', 'Reviews']);
  expect(screen.queryByText(/\(unavailable\)/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'More Skills pages' }));
  expect(await screen.findByRole('menuitem', { name: 'Workspace allowance' })).toBeVisible();
});
it('exposes rollouts only after the server capability is enabled', async () => {
  identity.campaigns = true;
  mount('/dashboard/skills/contexts');
  expect(await screen.findByRole('tab', { name: 'Repository rollouts' })).not.toBeDisabled();
});
it('keeps the full rollouts label on phones and keeps tabs scrollable', async () => {
  identity.campaigns = true;
  const original = window.matchMedia;
  // Every max-width query matches: the viewport is below sm.
  window.matchMedia = (query) => ({ matches: /max-width/.test(query), media: query, onchange: null, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {}, dispatchEvent: () => false });
  try {
    mount('/dashboard/skills/contexts');
    // One label at every width (UX iteration 4, SH8); the strip scrolls and fades a cut tab.
    const tab = await screen.findByRole('tab', { name: 'Repository rollouts' });
    expect(tab).toHaveTextContent(/^Repository rollouts$/);
    const tablist = screen.getByRole('tablist', { name: 'Skills views' });
    expect(tablist.closest('.MuiTabs-scrollableX')).not.toBeNull();
    expect(screen.getByRole('tab', { name: 'Agent contexts' })).toHaveAttribute('aria-selected', 'true');
  } finally {
    window.matchMedia = original;
  }
});
// The assistant on Skills routes (UX iteration 4, SH1): at every width it sits in the navigation row
// and nothing floats over content; Cmd+K keeps working. Other pages keep the floating button.
function Where() { return <output aria-label="Current path">{useLocation().pathname}</output>; }
const mountWithAssistant = (path) => render(<QueryClientProvider client={client}><HelmetProvider><ThemeProvider theme={theme}><MemoryRouter initialEntries={[path]}><Routes /><FalconAIFab /><Where /></MemoryRouter></ThemeProvider></HelmetProvider></QueryClientProvider>);
it('puts the assistant in the Skills navigation row instead of a floating button, and keeps Cmd+K', async () => {
  mountWithAssistant('/dashboard/skills/contexts');
  await screen.findByRole('button', { name: 'policy-context' });
  const nav = screen.getByRole('navigation', { name: 'Skills navigation' });
  const assistant = within(nav).getByRole('button', { name: 'Ask Falcon AI' });
  // Just left of More.
  expect(assistant.compareDocumentPosition(within(nav).getByRole('button', { name: 'More Skills pages' })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(document.querySelector('.MuiFab-root')).toBeNull();
  // Same action as the floating button: this deployment has no Falcon AI licence, so it opens the full page.
  fireEvent.click(assistant);
  await waitFor(() => expect(screen.getByRole('status', { name: 'Current path' })).toHaveTextContent('/dashboard/falcon-ai'));
  cleanup();
  mountWithAssistant('/dashboard/skills/contexts');
  await screen.findByRole('button', { name: 'policy-context' });
  fireEvent.keyDown(window, { key: 'k', ctrlKey: true });
  await waitFor(() => expect(screen.getByRole('status', { name: 'Current path' })).toHaveTextContent('/dashboard/falcon-ai'));
});
it('shows no floating assistant on Skills routes on phones either, only the navigation-row button', async () => {
  const original = window.matchMedia;
  window.matchMedia = (query) => ({ matches: /max-width/.test(query), media: query, onchange: null, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {}, dispatchEvent: () => false });
  try {
    mountWithAssistant('/dashboard/skills/contexts');
    await screen.findByRole('button', { name: 'policy-context' });
    expect(within(screen.getByRole('navigation', { name: 'Skills navigation' })).getByRole('button', { name: 'Ask Falcon AI' })).toBeVisible();
    expect(document.querySelector('.MuiFab-root')).toBeNull();
    fireEvent.keyDown(window, { key: 'k', ctrlKey: true });
    await waitFor(() => expect(screen.getByRole('status', { name: 'Current path' })).toHaveTextContent('/dashboard/falcon-ai'));
  } finally {
    window.matchMedia = original;
  }
});
it('keeps the floating assistant on pages outside Skills', () => {
  render(<QueryClientProvider client={client}><HelmetProvider><ThemeProvider theme={theme}><MemoryRouter initialEntries={['/dashboard/settings/integrations']}><FalconAIFab /></MemoryRouter></ThemeProvider></HelmetProvider></QueryClientProvider>);
  expect(document.querySelector('.MuiFab-root')).not.toBeNull();
});
it('starts editing from the explicitly viewed release, recognizing normalized missing-draft errors', async () => {
  mount(`/dashboard/skills/contexts/${id}?version=4`);
  await screen.findByText('# Exact policy');
  fireEvent.click(screen.getByRole('button', { name: 'Edit latest v4' }));
  expect(await screen.findByRole('button', { name: 'Start selected draft' })).toBeEnabled();
  expect(screen.getByText(/Start a draft from v4/)).toBeVisible();
  expect(screen.getByText(/starts from exactly v4; it is never swapped for the latest version/)).toBeVisible();
  expect(axios.post).not.toHaveBeenCalled();
});
it('loads the production draft review and sends the exact head acknowledgment, then publishes, on one click', async () => {
  const read = axios.get.getMockImplementation();
  const draft = { id: 'draft', revision: 3, base_version: 4, files: [{ path: 'AGENTS.md', content: '# Candidate' }], context_spec: { schema: 1, description: 'Standing policy', links: [] }, resolved_lock: { schema: 1, links: [] }, digest, recovery_digest: digest, checks: { items: [], summary: { errors: 0, warnings: 0 } }, note: '', expected_head_version: 4, head_acknowledged: false, review_fingerprint: null };
  axios.get.mockImplementation((url, config) => url.endsWith('/draft/') ? Promise.resolve(envelope(draft)) : read(url, config));
  const diff = { from: 4, files: [], total_pages: 1 };
  axios.post.mockImplementation(async (url) => {
    if (url.endsWith('/release/review/')) return envelope({ candidate: { kind: 'draft', revision: 3, digest }, base_version: 4, latest_version: 4, release_state: releaseState, changes: diff, head_changes: diff, checks: draft.checks, impact: {}, review_fingerprint: digest, head_review: { subject_id: id, kind: 'agent_context', draft_revision: 3, candidate_digest: digest, base_version: 4, expected_head_version: 4, head_digest: digest, review_fingerprint: digest } });
    if (url.endsWith('/acknowledge-head/')) return envelope({ ...draft, head_acknowledged: true, review_fingerprint: digest });
    throw new Error(`Unexpected write ${url}`);
  });
  mount(`/dashboard/skills/contexts/${id}/review`);
  const confirm = await screen.findByRole('checkbox', { name: /I reviewed these changes against the current published version/ });
  // One publish click acknowledges the reviewed head first: there is no separate confirm step.
  expect(screen.queryByRole('button', { name: 'Confirm review' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Publish version' })).toBeDisabled();
  fireEvent.click(confirm);
  expect(screen.getByRole('button', { name: 'Publish version' })).toBeEnabled();
  fireEvent.click(screen.getByRole('button', { name: 'Publish version' }));
  await waitFor(() => expect(axios.post.mock.calls.find(([url]) => url.endsWith('/acknowledge-head/'))?.[1]).toEqual({ revision: 3, expected_digest: digest, expected_head_version: 4, review_fingerprint: digest }));
  await waitFor(() => expect(axios.post.mock.calls.some(([url]) => url.endsWith('/publish/'))).toBe(true));
  const writes = axios.post.mock.calls.map(([url]) => url).filter((url) => /\/(acknowledge-head|publish)\/$/.test(url));
  expect(writes[0]).toMatch(/\/acknowledge-head\/$/);
  expect(writes[1]).toMatch(/\/publish\/$/);
});

it.each(['Publish version', 'Publish and request review'])('replays %s with the exact post-acknowledgment body even after the server removed the draft', async (button) => {
  const read = axios.get.getMockImplementation();
  let removed = false;
  let sends = 0;
  const acknowledgedFingerprint = `sha256:${'b'.repeat(64)}`;
  let draft = { id: 'draft', revision: 3, base_version: 4, files: [{ path: 'AGENTS.md', content: '# Candidate' }], context_spec: { schema: 1, description: 'Standing policy', links: [] }, resolved_lock: { schema: 1, links: [] }, digest, recovery_digest: digest, checks: { items: [], summary: { errors: 0, warnings: 0 } }, note: '', expected_head_version: 4, head_acknowledged: false, review_fingerprint: null };
  axios.get.mockImplementation(async (url, config) => {
    if (!url.endsWith('/draft/')) return read(url, config);
    if (removed) throw { statusCode: 404, code: 'draft_not_found' };
    return envelope(draft);
  });
  const diff = { from: 4, files: [], total_pages: 1 };
  axios.post.mockImplementation(async (url) => {
    if (url.endsWith('/release/review/')) {
      const fingerprint = draft.review_fingerprint || digest;
      return envelope({ candidate: { kind: 'draft', revision: draft.revision, digest }, base_version: 4, latest_version: 4, release_state: releaseState, changes: diff, head_changes: diff, checks: draft.checks, impact: {}, review_fingerprint: fingerprint, head_review: { subject_id: id, kind: 'agent_context', draft_revision: draft.revision, candidate_digest: digest, base_version: 4, expected_head_version: 4, head_digest: digest, review_fingerprint: fingerprint } });
    }
    if (url.endsWith('/acknowledge-head/')) {
      if (removed) throw { statusCode: 404, code: 'draft_not_found' };
      draft = { ...draft, revision: 4, head_acknowledged: true, review_fingerprint: acknowledgedFingerprint };
      return envelope(draft);
    }
    if (url.endsWith('/publish/')) {
      sends += 1;
      // Both responses are lost: keep the review mounted and prove repeated
      // reconciliation sends one frozen body, not a new acknowledgment or key.
      throw new Error('The publication response was lost');
    }
    throw new Error(`Unexpected write ${url}`);
  });
  mount(`/dashboard/skills/contexts/${id}/review`);
  fireEvent.click(await screen.findByRole('checkbox', { name: /I reviewed these changes against the current published version/ }));
  fireEvent.click(screen.getByRole('button', { name: button }));
  const check = await screen.findByRole('button', { name: 'Check status' });
  await waitFor(() => expect(sends).toBe(1));
  removed = true;
  fireEvent.click(check);
  await waitFor(() => expect(sends).toBe(2));
  const publications = axios.post.mock.calls.filter(([url]) => url.endsWith('/publish/'));
  expect(publications[0][1]).toMatchObject({ revision: 4, expected_digest: digest, review_fingerprint: acknowledgedFingerprint, expected_head_version: 4 });
  expect(publications[1][1]).toEqual(publications[0][1]);
  expect(publications[1][2].headers['Idempotency-Key']).toBe(publications[0][2].headers['Idempotency-Key']);
  expect(publications[0][2].headers['Idempotency-Key']).toEqual(expect.any(String));
  expect(axios.post.mock.calls.filter(([url]) => url.endsWith('/acknowledge-head/'))).toHaveLength(1);
  expect(await screen.findByRole('button', { name: 'Check status' })).toBeEnabled();
});

it('does not adopt a newly acknowledged same-bytes draft after an intervening revision change', async () => {
  const read = axios.get.getMockImplementation();
  const draft = { id: 'draft', revision: 3, base_version: 4, files: [{ path: 'AGENTS.md', content: '# Candidate' }], context_spec: { schema: 1, description: 'Standing policy', links: [] }, resolved_lock: { schema: 1, links: [] }, digest, recovery_digest: digest, checks: { items: [], summary: { errors: 0, warnings: 0 } }, note: '', expected_head_version: 4, head_acknowledged: false, review_fingerprint: null };
  let moved = false;
  axios.get.mockImplementation((url, config) => url.endsWith('/draft/')
    ? Promise.resolve(envelope(moved ? { ...draft, revision: 6, head_acknowledged: true, review_fingerprint: `sha256:${'b'.repeat(64)}` } : draft))
    : read(url, config));
  const diff = { from: 4, files: [], total_pages: 1 };
  axios.post.mockImplementation(async (url) => {
    // The server reviews whichever draft revision is current, so the reload after the conflict sees revision 6.
    if (url.endsWith('/release/review/')) {
      const revision = moved ? 6 : 3;
      return envelope({ candidate: { kind: 'draft', revision, digest }, base_version: 4, latest_version: 4, release_state: releaseState, changes: diff, head_changes: diff, checks: draft.checks, impact: {}, review_fingerprint: digest, head_review: { subject_id: id, kind: 'agent_context', draft_revision: revision, candidate_digest: digest, base_version: 4, expected_head_version: 4, head_digest: digest, review_fingerprint: digest } });
    }
    if (url.endsWith('/acknowledge-head/')) throw { statusCode: 409, code: 'draft_conflict' };
    throw new Error(`Unexpected write ${url}`);
  });
  mount(`/dashboard/skills/contexts/${id}/review`);
  fireEvent.click(await screen.findByRole('checkbox', { name: /I reviewed these changes against the current published version/ }));
  moved = true;
  fireEvent.click(screen.getByRole('button', { name: 'Publish version' }));
  expect(await screen.findByText('The draft changed since you reviewed it. Review the changes again.')).toBeVisible();
  const ack = axios.post.mock.calls.find(([url]) => url.endsWith('/acknowledge-head/'));
  expect(ack[1]).toMatchObject({ revision: 3, review_fingerprint: digest });
  expect(axios.post.mock.calls.some(([url]) => url.endsWith('/publish/'))).toBe(false);
  expect(screen.getByRole('checkbox', { name: /I reviewed these changes against the current published version/ })).not.toBeChecked();
});

it('loads later-page dependencies and reads linked/generated saved bytes through the production editor', async () => {
  const read = axios.get.getMockImplementation();
  const draft = { id: 'draft', revision: 3, base_version: 4, files: [{ path: 'AGENTS.md', content: '# Owned instructions' }], context_spec: { schema: 1, description: 'Standing policy', links: [{ skill_id: 'linked', selector: { version: 1 } }] }, resolved_lock: { schema: 1, links: [{ skill_id: 'linked', mount: 'policy', version: 1, version_id: 'child-version', digest, requested: { version: 1 } }] }, digest, recovery_digest: digest, checks: { items: [], summary: { errors: 0, warnings: 0 } }, note: '', expected_head_version: 4, head_acknowledged: false, review_fingerprint: null };
  axios.get.mockImplementation(async (url, config) => {
    if (url.endsWith('/draft/')) return envelope(draft);
    if (url === '/model-hub/skills/') return envelope({ results: config.params.page === 1 ? Array.from({ length: 100 }, (_, n) => ({ id: `unpublished-${n}`, name: `Unpublished ${n}`, latest_version: null })) : [{ id: 'outside-first-page', name: 'Later published skill', latest_version: 1 }], count: 101, page: config.params.page, page_size: 100, total_pages: 2 });
    if (url.endsWith('/draft/files/')) return envelope({ path: config.params.path, revision: 3, digest, sha256: 'hash', total_chars: 20, offset: 0, next_offset: null, eof: true, text: 'Accepted linked bytes' });
    return read(url, config);
  });
  axios.post.mockImplementation(async (url, body) => {
    if (url.endsWith('/release/review/')) {
      expect(body.revision).toBe(3);
      const files = ['AGENTS.md', 'skills/policy/SKILL.md', '.futureagi/context.json'].map((path) => ({ path, status: 'unchanged', from_sha256: 'hash', to_sha256: 'hash' }));
      return envelope({ candidate: { kind: 'draft', revision: 3, digest }, changes: { files, total_pages: 1 }, head_changes: { files, total_pages: 1 }, review_fingerprint: digest, head_review: { draft_revision: 3, candidate_digest: digest } });
    }
    throw new Error(`Unexpected write ${url}`);
  });
  mount(`/dashboard/skills/contexts/${id}/edit`);
  await screen.findByRole('textbox', { name: 'AGENTS.md' });
  fireEvent.mouseDown(await screen.findByRole('combobox', { name: /Add a published skill/ }));
  fireEvent.click(await screen.findByRole('option', { name: 'Later published skill' }));
  fireEvent.click(screen.getByRole('button', { name: 'Add to proposed links' }));
  expect(screen.getByText('Later published skill')).toBeVisible();
  // The editor starts with the linked skills folder closed; open it to reach the locked bytes.
  fireEvent.click(await screen.findByRole('treeitem', { name: 'skills' }));
  fireEvent.click(await screen.findByRole('treeitem', { name: 'SKILL.md' }));
  expect(await screen.findByRole('textbox', { name: 'skills/policy/SKILL.md' })).toHaveValue('Accepted linked bytes');
  expect(screen.getByRole('textbox', { name: 'skills/policy/SKILL.md' })).toHaveAttribute('readonly');
  fireEvent.click(screen.getByRole('treeitem', { name: 'context.json' }));
  expect(await screen.findByRole('textbox', { name: '.futureagi/context.json' })).toHaveAttribute('readonly');
  expect(axios.get.mock.calls.filter(([url]) => url.endsWith('/draft/files/')).every(([, config]) => config.params.revision === 3)).toBe(true);
  expect(axios.put).not.toHaveBeenCalled();
});

function installRequest(kind, { current = true, canMove = true, authorId = 'another-author', status = 'pending' } = {}) {
  const read = axios.get.getMockImplementation();
  const requestId = '00000000-0000-4000-8000-000000000090';
  const packet = { schema: 1, subject_kind: kind, subject_id: id, candidate_version_id: versionId, candidate_digest: digest, transition: { label: 'production', expected_version: 3, version: 4, expected_release_revision: 1, expected_security_revision: 0, expected_impact_fingerprint: digest }, target: null, evidence: [], external_pipeline: null };
  const request = { id: requestId, revision: 1, status, author_id: authorId, subject_id: id, subject_kind: kind, name: 'Assigned candidate', unread: true };
  axios.get.mockImplementation(async (url, config) => {
    if (url.endsWith('/review-inbox/')) return envelope({ ...page([request]), unread_count: 1 });
    if (url.endsWith(`/release-requests/${requestId}/`)) return envelope({ ...request, packet, fingerprint: digest });
    if (url === `/model-hub/skills/${id}/`) return envelope({ id, labels: [{ name: 'production', version: 3, can_move: canMove }], permissions: {} });
    if (url === `/model-hub/skills/${id}/versions/4/`) return envelope({ id: versionId, number: 4, digest, checks: { items: [], summary: { errors: 0 } } });
    if (url.endsWith('/compare/')) return envelope({ from: 3, to: 4, files: [{ path: 'SKILL.md', status: 'modified', from_text: 'Old policy', to_text: 'Reviewed candidate policy' }] });
    if (url.endsWith('/inspection/')) {
      const value = await read(url, config);
      const number = Number(url.match(/\/versions\/(\d+)\//)[1]);
      return envelope({ ...value.data.result, manifest: { ...value.data.result.manifest, version: number, version_id: number === 3 ? 'version-3' : versionId } });
    }
    if (url.endsWith('/inspection/files/')) {
      const before = url.includes('/versions/3/');
      return envelope({ path: 'AGENTS.md', version_id: before ? 'version-3' : versionId, digest, sha256: before ? 'before' : 'after', offset: 0, next_offset: null, eof: true, text: before ? 'Old policy' : 'Reviewed candidate policy' });
    }
    return read(url, config);
  });
  axios.post.mockImplementation(async (url, body) => {
    if (url.endsWith('/release-requests/preview/')) return envelope({ packet, fingerprint: current ? digest : 'changed' });
    // J4: the reviewer's server-side inspection of the exact packet (no evidence attached here).
    if (url.endsWith('/release-requests/inspection/')) return envelope({ fingerprint: body.expected_fingerprint, capabilities: { before_digest: 'before', after_digest: digest, changes: [] }, summaries: [], target_configuration: null });
    if (url.endsWith('/release/review/')) return envelope({ candidate: { kind: 'version', version: 4, digest }, release_state: { ...releaseState, labels: [{ name: 'production', version: 3, can_move: canMove }] }, changes: { from: 3, files: [{ path: 'AGENTS.md', status: 'modified', from_sha256: 'before', to_sha256: 'after' }], total_pages: 1 }, head_changes: { from: 4, files: [], total_pages: 1 }, checks: { items: [] }, impact: { following: 2, pinned: 1, unobserved: 0, more_available: false }, review_fingerprint: digest, head_review: null });
    if (url.endsWith('/decisions/')) { expect(body.expected_fingerprint).toBe(digest); request.status = 'approved'; request.revision = 2; return envelope(request); }
    if (url.endsWith('/execute/')) { expect(body.revision).toBe(2); request.status = 'released'; request.revision = 3; return envelope({ request_id: requestId, decision_id: 'decision', revision: 3, executed_by: 'author', currentness_at_action: { fingerprint: digest }, post_move_state: { label: 'production', version: 4, release_revision: 2, event_id: 'event' } }); }
    throw new Error(`Unexpected write ${url}`);
  });
}
it.each(['agent_context', 'skill'])('opens, approves and executes a %s request through the production inbox', async (kind) => {
  installRequest(kind);
  mount('/dashboard/skills/reviews');
  fireEvent.click(await screen.findByRole('button', { name: 'Assigned candidate' }));
  const acknowledge = await screen.findByRole('checkbox', { name: /I've checked this version/ });
  await waitFor(() => expect(acknowledge).toBeEnabled());
  // The conclusion card and the Who is affected section both state the impact.
  if (kind === 'agent_context') screen.getAllByText(/2 connected apps move with this release\. 1 is pinned and stays put\./).forEach((node) => expect(node).toBeVisible());
  fireEvent.click(acknowledge);
  fireEvent.click(screen.getByRole('button', { name: 'Approve release' }));
  await waitFor(() => expect(axios.post.mock.calls.some(([url]) => url.endsWith('/decisions/'))).toBe(true));
  await waitFor(() => expect(screen.getByRole('checkbox', { name: /I've checked this version/ })).not.toBeChecked());
  fireEvent.click(screen.getByRole('checkbox', { name: /I've checked this version/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Release to production' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Move production', exact: true }));
  await waitFor(() => expect(axios.post.mock.calls.some(([url]) => url.endsWith('/execute/'))).toBe(true));
});
it.each(['agent_context', 'skill'].flatMap((kind) => ['pending', 'approved'].map((status) => [kind, status])))('keeps a stale %s %s packet fail-closed', async (kind, status) => {
  installRequest(kind, { current: false, status });
  mount('/dashboard/skills/reviews');
  fireEvent.click(await screen.findByRole('button', { name: 'Assigned candidate' }));
  expect(await screen.findByRole('checkbox', { name: /I've checked this version/ })).toBeDisabled();
  if (status === 'pending') {
    expect(screen.getByRole('button', { name: 'Approve release' })).toBeDisabled();
    // A paused request keeps its release action in place, disabled until renewed and approved.
    expect(screen.getByRole('button', { name: 'Release to production' })).toBeDisabled();
  } else {
    expect(screen.queryByRole('button', { name: 'Approve release' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Release to production' })).toBeDisabled();
  }
  expect(axios.post.mock.calls.some(([url]) => /\/(decisions|execute)\//.test(url))).toBe(false);
});
it.each(['agent_context', 'skill'].flatMap((kind) => ['pending', 'approved'].map((status) => [kind, status])))('does not grant %s %s release authority from readable content', async (kind, status) => {
  installRequest(kind, { canMove: false, authorId: 'author', status });
  mount('/dashboard/skills/reviews');
  fireEvent.click(await screen.findByRole('button', { name: 'Assigned candidate' }));
  fireEvent.click(await screen.findByRole('checkbox', { name: /I've checked this version/ }));
  if (status === 'pending') {
    expect(screen.getByRole('button', { name: 'Approve release' })).toBeDisabled();
    expect(screen.queryByRole('button', { name: 'Release to production' })).not.toBeInTheDocument();
  } else {
    expect(screen.queryByRole('button', { name: 'Approve release' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Release to production' })).toBeDisabled();
  }
  expect(axios.post.mock.calls.some(([url]) => /\/(decisions|execute)\//.test(url))).toBe(false);
});

it('does not substitute latest when a malformed explicit release was requested', async () => {
  mount(`/dashboard/skills/contexts/${id}?version=bad`);
  await screen.findByText(/No latest-version substitution was made/);
  await waitFor(() => expect(axios.get.mock.calls.some(([url]) => url.endsWith(`/contexts/${id}/`))).toBe(true));
  expect(axios.get.mock.calls.some(([url]) => url.includes('/inspection/'))).toBe(false);
});
