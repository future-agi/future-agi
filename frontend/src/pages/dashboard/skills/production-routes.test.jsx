/* eslint-env node */
import fs from 'node:fs';
import ts from 'typescript';
import PropTypes from 'prop-types';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, useRoutes } from 'react-router-dom';
import { HelmetProvider } from 'react-helmet-async';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import axios from 'src/utils/axios';
import SkillsWorkspaceLayout from 'src/sections/skills/components/SkillsWorkspaceLayout';
import ContextLibraryPage from './contexts/ContextLibraryPage';
import ContextDetailPage from './contexts/ContextDetailPage';
import ContextEditorPage from './contexts/ContextEditorPage';
import ContextReviewPage from './contexts/ContextReviewPage';
import ContextCampaignPage from './contexts/ContextCampaignPage';
import SkillsReviewsPage from './SkillsReviewsPage';

const identity = vi.hoisted(() => ({ workspace: '00000000-0000-4000-8000-000000000001', campaigns: false }));
vi.mock('src/auth/hooks', () => ({ useAuthContext: () => ({ authenticated: true, user: { id: 'author' } }) }));
vi.mock('src/contexts/OrganizationContext', () => ({ useOrganization: () => ({ isReady: true, currentOrganizationId: '00000000-0000-4000-8000-000000000002' }) }));
vi.mock('src/contexts/WorkspaceContext', () => ({ useWorkspace: () => ({ isReady: true, currentWorkspaceId: identity.workspace }) }));
// Only the HTTP boundary is replaced; route/provider/controllers/hooks stay real.
vi.mock('src/utils/axios', () => ({ default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() } }));
// jsdom cannot mount Monaco's canvas. Only the editor primitive is replaced;
// the production controller, draft session and revision fences remain real.
vi.mock('@monaco-editor/react', () => ({ useMonaco: () => null }));
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
const mount = (path) => render(<QueryClientProvider client={client}><HelmetProvider><MemoryRouter initialEntries={[path]}><Routes /></MemoryRouter></HelmetProvider></QueryClientProvider>);
beforeEach(() => {
  vi.clearAllMocks(); identity.campaigns = false; context.latest_version = 4;
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
  fireEvent.click(screen.getByRole('tab', { name: 'Consumers' }));
  expect(await screen.findByText('No declared consumers')).toBeVisible();
  fireEvent.click(screen.getByRole('tab', { name: 'Use' }));
  expect(screen.getByText(`v4 · ${versionId}`)).toBeVisible();
  expect(screen.getByRole('tab', { name: 'Agent contexts' })).not.toBeDisabled();
});
it('retains the initially viewed release when background metadata advances latest', async () => {
  mount(`/dashboard/skills/contexts/${id}`);
  await screen.findByText('# Exact policy');
  context.latest_version = 5;
  await act(async () => { await client.invalidateQueries(); });
  await waitFor(() => expect(screen.getByText('v5')).toBeVisible());
  expect(screen.getByRole('button', { name: 'Edit viewed v4' })).toBeVisible();
  expect(axios.get.mock.calls.some(([url]) => url.includes('/versions/5/inspection/'))).toBe(false);
});

it('mounts the real reviewer inbox without an injected Controller', async () => {
  mount('/dashboard/skills/contexts');
  fireEvent.click(screen.getByRole('tab', { name: 'Reviews' }));
  expect(await screen.findByText('No assigned reviews')).toBeVisible();
  expect(axios.get.mock.calls.some(([url]) => url.endsWith('/review-inbox/'))).toBe(true);
});
it('keeps rollouts unavailable when the backend switch is off', async () => {
  mount('/dashboard/skills/contexts');
  await screen.findByRole('button', { name: 'policy-context' });
  expect(screen.getByRole('tab', { name: 'Repository rollouts (unavailable)' })).toBeDisabled();
});
it('exposes rollouts only after the server capability is enabled', async () => {
  identity.campaigns = true;
  mount('/dashboard/skills/contexts');
  expect(await screen.findByRole('tab', { name: 'Repository rollouts' })).not.toBeDisabled();
});
it('starts editing from the explicitly viewed release, recognizing normalized missing-draft errors', async () => {
  mount(`/dashboard/skills/contexts/${id}?version=4`);
  await screen.findByText('# Exact policy');
  fireEvent.click(screen.getByRole('button', { name: 'Edit viewed v4' }));
  expect(await screen.findByRole('button', { name: 'Start selected draft' })).toBeEnabled();
  expect(screen.getByText(/exact viewed version v4/)).toBeVisible();
  expect(axios.post).not.toHaveBeenCalled();
});
it('loads the production draft review and sends the exact head acknowledgment', async () => {
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
  const confirm = await screen.findByRole('checkbox', { name: /I reviewed the saved candidate/ });
  expect(screen.getByRole('button', { name: 'Acknowledge reviewed head' })).toBeDisabled();
  fireEvent.click(confirm);
  fireEvent.click(screen.getByRole('button', { name: 'Acknowledge reviewed head' }));
  await waitFor(() => expect(axios.post.mock.calls.find(([url]) => url.endsWith('/acknowledge-head/'))?.[1]).toEqual({ revision: 3, expected_digest: digest, expected_head_version: 4, review_fingerprint: digest }));
  expect(axios.post.mock.calls.some(([url]) => url.endsWith('/publish/'))).toBe(false);
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
  fireEvent.mouseDown(await screen.findByRole('combobox', { name: /Published leaf skill/ }));
  fireEvent.click(await screen.findByRole('option', { name: 'Later published skill' }));
  fireEvent.click(screen.getByRole('button', { name: 'Add to proposed links' }));
  expect(screen.getByText('Later published skill')).toBeVisible();
  fireEvent.click(await screen.findByRole('treeitem', { name: 'SKILL.md' }));
  expect(await screen.findByRole('textbox', { name: 'skills/policy/SKILL.md' })).toHaveValue('Accepted linked bytes');
  expect(screen.getByRole('textbox', { name: 'skills/policy/SKILL.md' })).toHaveAttribute('readonly');
  fireEvent.click(screen.getByRole('treeitem', { name: 'context.json' }));
  expect(await screen.findByRole('textbox', { name: '.futureagi/context.json' })).toHaveAttribute('readonly');
  expect(axios.get.mock.calls.filter(([url]) => url.endsWith('/draft/files/')).every(([, config]) => config.params.revision === 3)).toBe(true);
  expect(axios.put).not.toHaveBeenCalled();
});

function installRequest(kind, { current = true, canMove = true, authorId = 'another-author' } = {}) {
  const read = axios.get.getMockImplementation();
  const requestId = '00000000-0000-4000-8000-000000000090';
  const packet = { schema: 1, subject_kind: kind, subject_id: id, candidate_version_id: versionId, candidate_digest: digest, transition: { label: 'production', expected_version: 3, version: 4, expected_release_revision: 1, expected_security_revision: 0, expected_impact_fingerprint: digest }, target: null, evidence: [], external_pipeline: null };
  const request = { id: requestId, revision: 1, status: 'pending', author_id: authorId, subject_id: id, subject_kind: kind, name: 'Assigned candidate', unread: true };
  axios.get.mockImplementation(async (url, config) => {
    if (url.endsWith('/review-inbox/')) return envelope({ ...page([request]), unread_count: 1 });
    if (url.endsWith(`/release-requests/${requestId}/`)) return envelope({ ...request, packet, fingerprint: digest });
    if (url === `/model-hub/skills/${id}/`) return envelope({ id, labels: [{ name: 'production', version: 3, can_move: canMove }], permissions: {} });
    if (url === `/model-hub/skills/${id}/versions/4/`) return envelope({ id: versionId, number: 4, digest, checks: { items: [], summary: { errors: 0 } } });
    if (url.endsWith('/compare/')) return envelope({ from: 3, to: 4, files: [{ path: 'SKILL.md', status: 'modified', from_text: 'Old policy', to_text: 'Reviewed candidate policy' }] });
    if (url.endsWith('/inspection/files/')) {
      const before = url.includes('/versions/3/');
      return envelope({ path: 'AGENTS.md', version_id: before ? 'version-3' : versionId, digest, sha256: before ? 'before' : 'after', offset: 0, next_offset: null, eof: true, text: before ? 'Old policy' : 'Reviewed candidate policy' });
    }
    return read(url, config);
  });
  axios.post.mockImplementation(async (url, body) => {
    if (url.endsWith('/release-requests/preview/')) return envelope({ packet, fingerprint: current ? digest : 'changed' });
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
  const acknowledge = await screen.findByRole('checkbox', { name: /I reviewed this exact candidate/ });
  expect(acknowledge).toBeEnabled();
  if (kind === 'agent_context') expect(screen.getByText(/2 followers · 1 intentional pins/)).toBeVisible();
  fireEvent.click(acknowledge);
  fireEvent.click(screen.getByRole('button', { name: 'Approve exact packet' }));
  await waitFor(() => expect(axios.post.mock.calls.some(([url]) => url.endsWith('/decisions/'))).toBe(true));
  await waitFor(() => expect(screen.getByRole('checkbox', { name: /I reviewed this exact candidate/ })).not.toBeChecked());
  fireEvent.click(screen.getByRole('checkbox', { name: /I reviewed this exact candidate/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Release approved packet' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Move production', exact: true }));
  await waitFor(() => expect(axios.post.mock.calls.some(([url]) => url.endsWith('/execute/'))).toBe(true));
});
it.each(['agent_context', 'skill'])('keeps a stale %s packet fail-closed', async (kind) => {
  installRequest(kind, { current: false });
  mount('/dashboard/skills/reviews');
  fireEvent.click(await screen.findByRole('button', { name: 'Assigned candidate' }));
  expect(await screen.findByRole('checkbox', { name: /I reviewed this exact candidate/ })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Approve exact packet' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Release approved packet' })).toBeDisabled();
});
it.each(['agent_context', 'skill'])('does not grant %s release authority from readable content', async (kind) => {
  installRequest(kind, { canMove: false, authorId: 'author' });
  mount('/dashboard/skills/reviews');
  fireEvent.click(await screen.findByRole('button', { name: 'Assigned candidate' }));
  fireEvent.click(await screen.findByRole('checkbox', { name: /I reviewed this exact candidate/ }));
  expect(screen.getByRole('button', { name: 'Approve exact packet' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Release approved packet' })).toBeDisabled();
});

it('does not substitute latest when a malformed explicit release was requested', async () => {
  mount(`/dashboard/skills/contexts/${id}?version=bad`);
  await screen.findByText(/No latest-version substitution was made/);
  await waitFor(() => expect(axios.get.mock.calls.some(([url]) => url.endsWith(`/contexts/${id}/`))).toBe(true));
  expect(axios.get.mock.calls.some(([url]) => url.includes('/inspection/'))).toBe(false);
});
