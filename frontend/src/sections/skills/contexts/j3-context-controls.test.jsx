import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import ContextTestsPanel from './ContextTestsPanel';
import ContextLabelRequestPanel from './ContextLabelRequestPanel';
import ContextDependentsPanel from './ContextDependentsPanel';

const state = vi.hoisted(() => ({ identity: 'org:workspace:author', prepare: vi.fn(), create: vi.fn(), previewLabel: vi.fn(), submitLabel: vi.fn(), dependents: null }));
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
}));
vi.mock('src/api/skills/context-release-request', () => ({ usePreviewContextLabelRequest: () => ({ mutateAsync: state.previewLabel }), useSubmitContextLabelRequest: () => ({ mutateAsync: state.submitLabel }) }));
vi.mock('src/api/skills/context-dependents', () => ({ useContextDependents: () => state.dependents }));
const digest = `sha256:${'a'.repeat(64)}`;
const version = { id: 'parent-v2', number: 2, digest };
const preview = { fingerprint: 'preview', attemptCount: 2, estimate: { upperBoundUsd: '0.01' }, providerDisclosure: { taskProvider: 'openai' }, inventory: [], outboundFindings: [] };
const labelPreview = { fingerprint: 'packet', packet: { candidate_version_id: version.id, candidate_digest: digest, transition: { label: 'production', version: 2, expected_version: 1, expected_release_revision: 4, expected_security_revision: 0 } } };
beforeEach(() => { vi.clearAllMocks(); state.identity = 'org:workspace:author'; state.prepare.mockResolvedValue(preview); state.create.mockResolvedValue({ id: 'run' }); state.previewLabel.mockResolvedValue(labelPreview); state.submitLabel.mockResolvedValue({ id: 'request' }); });
afterEach(cleanup);
const mount = (element) => render(<MemoryRouter>{element}</MemoryRouter>);
async function prepare() {
  fireEvent.change(screen.getByRole('textbox', { name: 'Test request' }), { target: { value: 'Describe purchasing policy' } });
  fireEvent.change(screen.getByRole('textbox', { name: 'Expected answer contains' }), { target: { value: 'approved' } });
  fireEvent.click(screen.getByRole('checkbox', { name: 'I reviewed this request and its expected answer.' }));
  fireEvent.mouseDown(screen.getByRole('combobox', { name: 'Task model' }));
  fireEvent.click(await screen.findByRole('option', { name: 'gpt-4o-mini' }));
  fireEvent.change(screen.getByRole('textbox', { name: 'Maximum estimated cost (USD)' }), { target: { value: '1' } });
  fireEvent.click(screen.getByRole('button', { name: 'Preview whole-context test' }));
  await screen.findByRole('button', { name: 'Run whole-context test' });
}
function reviewInventory() {
  fireEvent.click(screen.getByRole('checkbox', { name: 'I know of no bundled answer keys or evaluation material' }));
  fireEvent.click(screen.getByRole('checkbox', { name: 'I reviewed the entire inventory and outbound inputs' }));
  fireEvent.click(screen.getByRole('checkbox', { name: 'I acknowledge provider processing of this reviewed context and test request.' }));
}
it('uses context semantics, a required without-context baseline, and explicit reviewed provider processing', async () => {
  mount(<ContextTestsPanel contextId="parent" draft={{ revision: 5, digest }} />);
  await prepare();
  expect(state.prepare.mock.calls[0][0]).toMatchObject({ harness: 'contexts-text-v1.2', candidate: { kind: 'draft', revision: 5, digest }, competitors: [], baselines: [{ kind: 'without_context' }], cases: { cases: [{ expected_trigger: null }] } });
  expect(screen.getByRole('button', { name: 'Run whole-context test' })).toBeDisabled();
  expect(state.create).not.toHaveBeenCalled();
  reviewInventory();
  fireEvent.click(screen.getByRole('button', { name: 'Run whole-context test' }));
  await waitFor(() => expect(state.create).toHaveBeenCalledOnce());
  expect(state.create.mock.calls[0][0].configuration.candidate.digest).toBe(digest);
});
it('does not let a late preview authorize a changed candidate', async () => {
  const view = mount(<ContextTestsPanel contextId="parent" version={version} />);
  await prepare(); reviewInventory();
  view.rerender(<MemoryRouter><ContextTestsPanel contextId="parent" version={{ ...version, number: 3, digest: `sha256:${'b'.repeat(64)}` }} /></MemoryRouter>);
  expect(screen.getByRole('button', { name: 'Run whole-context test' })).toBeDisabled();
  expect(screen.getByText(/saved candidate changed/)).toBeVisible();
  expect(state.create).not.toHaveBeenCalled();
});
it('recovers an uncertain admission using the identical body and key, without a second preview', async () => {
  state.create.mockRejectedValueOnce(new Error('response lost')).mockResolvedValueOnce({ id: 'original-run' });
  mount(<ContextTestsPanel contextId="parent" version={version} />);
  await prepare(); reviewInventory();
  fireEvent.click(screen.getByRole('button', { name: 'Run whole-context test' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Recover original context test' }));
  await waitFor(() => expect(state.create).toHaveBeenCalledTimes(2));
  expect(state.create.mock.calls[1][0]).toEqual(state.create.mock.calls[0][0]);
  expect(state.prepare).toHaveBeenCalledOnce();
});
it('requires an independent reviewer and only submits a label request, not a label mutation', async () => {
  mount(<ContextLabelRequestPanel contextId="parent" version={version} />);
  fireEvent.click(screen.getByRole('button', { name: 'Preview parent label move' }));
  await screen.findByRole('button', { name: 'Send parent release request' });
  fireEvent.change(screen.getByRole('textbox', { name: 'Independent reviewer user ID' }), { target: { value: '00000000-0000-4000-8000-000000000001' } });
  fireEvent.click(screen.getByRole('checkbox'));
  expect(screen.getByRole('button', { name: 'Send parent release request' })).toBeDisabled();
  fireEvent.change(screen.getByRole('textbox', { name: 'Independent reviewer user ID' }), { target: { value: '00000000-0000-4000-8000-000000000002' } });
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(screen.getByRole('button', { name: 'Send parent release request' }));
  await waitFor(() => expect(state.submitLabel).toHaveBeenCalledOnce());
  expect(state.submitLabel.mock.calls[0][0]).toMatchObject({ packet: labelPreview.packet, fingerprint: 'packet' });
  expect(await screen.findByText(/No label move has been confirmed/)).toBeVisible();
});
it('shows dependent failures rather than claiming an empty inventory', () => {
  state.dependents = { isError: true, refetch: vi.fn() };
  mount(<ContextDependentsPanel skillId="child" />);
  expect(screen.getByText('Dependents unavailable')).toBeVisible();
  expect(screen.queryByText('No published references')).not.toBeInTheDocument();
});
