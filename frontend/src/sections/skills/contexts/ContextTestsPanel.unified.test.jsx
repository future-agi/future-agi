import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import ContextTestsPanel from './ContextTestsPanel';

// One Tests panel carries regression cases, application verification and whole-context tests.
// Each section owns its loading and error states; neither hides the other.
const h = vi.hoisted(() => ({ optionsError: false, suiteError: false, runtime: true, retry: () => {} }));
const idle = vi.hoisted(() => () => ({ mutate: () => {}, mutateAsync: async () => {}, reset: () => {}, isPending: false }));
vi.mock('src/api/skills/testing', () => ({
  useSkillV11Scope: () => ({ identity: 'org:workspace:author' }),
  useSkillTestingOptions: () => (h.optionsError ? { isError: true, refetch: () => {} } : { data: { enabled: true, models: [{ modelId: 'gpt-4o-mini', canRun: true }], limits: { profiles: { quick: { maxTotalTokens: 1000 } } } } }),
  isActiveSkillJob: () => false,
}));
vi.mock('src/api/skills/context-testing', () => ({
  useContextTestSuite: () => (h.suiteError ? { isError: true, refetch: () => {} } : { data: { revision: 1, cases: [{ id: 'refund', title: 'Refund failure', prompt: 'Refund order 7', reviewed: true, checks: [{}] }] } }),
  useSaveContextTestSuite: idle,
  contextTestCandidate: (draft) => ({ kind: 'draft', revision: draft.revision, digest: draft.digest }),
  usePreviewContextTest: idle, useCreateContextTest: idle, useCancelContextTest: idle,
  useContextTestRuns: () => ({ data: { results: [] } }), useContextTestRun: () => ({}), useContextTestResults: () => ({}), useContextTestAttempt: () => ({}),
  useNativeTargets: () => ({ data: { runtime: { available: h.runtime, runtime: 'container', evidenceClass: 'synthetic fixture' }, targets: [] } }),
  useNativeRuns: () => ({ data: [] }), useNativeRun: () => ({}), useRuntimeConfiguration: () => ({}),
  useCreateNativeTarget: idle, useQualifyNativeTarget: idle, usePreviewNativeRun: idle, useCreateNativeRun: idle, usePrepareReleasePacket: idle,
}));
vi.mock('src/api/skills/evidence-annotations', () => ({ useEvidenceReference: () => ({}), useAllEvidenceAnnotations: () => ({}), useAppendEvidenceAnnotation: idle }));
vi.mock('src/auth/hooks', () => ({ useAuthContext: () => ({ user: { id: 'user' } }) }));
const digest = `sha256:${'a'.repeat(64)}`;
const version = { id: 'v4', number: 4, digest };
const mount = (props = {}) => render(<MemoryRouter><ContextTestsPanel contextId="context" version={version} versions={[version]} canWrite {...props} /></MemoryRouter>);
beforeEach(() => { h.optionsError = false; h.suiteError = false; h.runtime = true; window.localStorage.clear(); });
afterEach(cleanup);

it('shows regression cases, application verification and whole-context tests in one panel', () => {
  mount();
  expect(screen.getByText('Context regression cases · viewed v4')).toBeVisible();
  expect(screen.getByRole('table', { name: 'Context regression cases' })).toHaveTextContent('Refund failure');
  expect(screen.getByText('Application target')).toBeVisible();
  expect(screen.getByText('Verify on the application')).toBeVisible();
  expect(screen.getByText('Check answers with this context')).toBeVisible();
  expect(screen.getByText('Release v4')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Preview whole-context test' })).toBeInTheDocument();
});
it('keeps application verification when whole-context testing is unavailable', () => {
  h.optionsError = true;
  mount();
  expect(screen.getByText('Context testing unavailable')).toBeVisible();
  expect(screen.getByText('Application target')).toBeVisible();
  expect(screen.getByRole('table', { name: 'Context regression cases' })).toBeVisible();
});
it('keeps whole-context tests when the regression suite cannot load', () => {
  h.suiteError = true;
  mount();
  expect(screen.getByText('Could not load context regression cases')).toBeVisible();
  expect(screen.getByRole('textbox', { name: 'Test request' })).toBeVisible();
});
it('switches only the whole-context candidate to the saved draft', () => {
  const onSelect = vi.fn();
  const source = { testingDraft: false, draftAvailable: true, releaseAvailable: true, onSelect };
  const view = mount({ source });
  fireEvent.click(screen.getByRole('radio', { name: 'Saved draft' }));
  expect(onSelect).toHaveBeenCalledWith(true);
  view.rerender(<MemoryRouter><ContextTestsPanel contextId="context" version={version} versions={[version]} canWrite source={{ ...source, testingDraft: true }} draft={{ revision: 9, digest }} /></MemoryRouter>);
  expect(screen.getByText('Saved draft revision 9')).toBeVisible();
  // Application verification still names the viewed release.
  expect(screen.getByText('Context regression cases · viewed v4')).toBeVisible();
  expect(screen.getByRole('radio', { name: 'Saved draft' })).toBeChecked();
  expect(screen.getByRole('radio', { name: 'Published version' })).toBeEnabled();
});
it('never falls back to the release while the saved draft is loading or failed', () => {
  const source = { testingDraft: true, draftAvailable: true, releaseAvailable: true, onSelect: () => {}, isError: true, onRetry: () => {} };
  mount({ source });
  expect(screen.getByText('Saved draft unavailable')).toBeVisible();
  expect(screen.queryByText('Release v4')).not.toBeInTheDocument();
  // The switch remains so the author can go back to the viewed release.
  expect(screen.getByRole('radio', { name: 'Published version' })).toBeEnabled();
});
it('hands the editor draft off to the saved-revision tests without an inline run', () => {
  const onOpen = vi.fn();
  render(<MemoryRouter><ContextTestsPanel contextId="context" canWrite onOpenSavedDraft={onOpen} /></MemoryRouter>);
  expect(screen.getByText(/Context regression cases/)).toBeVisible();
  expect(screen.queryByRole('textbox', { name: 'Test request' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Save and open whole-context tests' }));
  expect(onOpen).toHaveBeenCalledTimes(1);
});
