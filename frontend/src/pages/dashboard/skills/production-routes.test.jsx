/* eslint-env node */
import fs from 'node:fs';
import ts from 'typescript';
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

it('does not substitute latest when a malformed explicit release was requested', async () => {
  mount(`/dashboard/skills/contexts/${id}?version=bad`);
  await screen.findByText(/No latest-version substitution was made/);
  await waitFor(() => expect(axios.get.mock.calls.some(([url]) => url.endsWith(`/contexts/${id}/`))).toBe(true));
  expect(axios.get.mock.calls.some(([url]) => url.includes('/inspection/'))).toBe(false);
});
