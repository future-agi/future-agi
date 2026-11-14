import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import ContextTestsPanel from './ContextTestsPanel';

// One Tests panel carries regression cases, application verification and whole-context tests.
// Each section owns its loading and error states; neither hides the other.
const h = vi.hoisted(() => ({ optionsError: false, suiteError: false, runtime: true, retry: () => {}, targets: [], answerRuns: [], run: {}, results: {} }));
const idle = vi.hoisted(() => () => ({ mutate: () => {}, mutateAsync: async () => {}, reset: () => {}, isPending: false }));
vi.mock('src/api/skills/r3-spend', () => ({ useSkillsSpendPolicy: () => ({}) }));
vi.mock('src/api/skills/testing', () => ({
  useSkillV11Scope: () => ({ identity: 'org:workspace:author' }),
  useSkillTestingOptions: () => (h.optionsError ? { isError: true, refetch: () => {} } : { data: { enabled: true, models: [{ modelId: 'gpt-4o-mini', canRun: true }], limits: { profiles: { quick: { maxTotalTokens: 1000 } } } } }),
  isActiveSkillJob: (status) => ['queued', 'running'].includes(status),
}));
vi.mock('src/api/skills/context-testing', () => ({
  useContextTestSuite: () => (h.suiteError ? { isError: true, refetch: () => {} } : { data: { revision: 1, cases: [{ id: 'refund', title: 'Refund failure', prompt: 'Refund order 7', reviewed: true, checks: [{}] }] } }),
  useSaveContextTestSuite: idle,
  contextTestCandidate: (draft) => ({ kind: 'draft', revision: draft.revision, digest: draft.digest }),
  usePreviewContextTest: idle, useCreateContextTest: idle, useCancelContextTest: idle,
  useContextTestRuns: () => ({ data: { results: h.answerRuns } }), useContextTestRun: () => h.run, useContextTestResults: () => h.results, useContextTestAttempt: () => ({}),
  useNativeTargets: () => ({ data: { runtime: { available: h.runtime, runtime: 'container', evidenceClass: 'synthetic fixture' }, targets: h.targets } }),
  useNativeRuns: () => ({ data: [] }), useNativeRun: () => ({}), useRuntimeConfiguration: () => ({}),
  useCreateNativeTarget: idle, useQualifyNativeTarget: idle, usePreviewNativeRun: idle, useCreateNativeRun: idle, usePrepareReleasePacket: idle,
}));
vi.mock('src/api/skills/evidence-annotations', () => ({ useEvidenceReference: () => ({}), useAllEvidenceAnnotations: () => ({}), useAppendEvidenceAnnotation: idle }));
vi.mock('src/auth/hooks', () => ({ useAuthContext: () => ({ user: { id: 'user' } }) }));
const digest = `sha256:${'a'.repeat(64)}`;
const version = { id: 'v4', number: 4, digest };
const mount = (props = {}) => render(<MemoryRouter><ContextTestsPanel contextId="context" version={version} versions={[version]} canWrite {...props} /></MemoryRouter>);
const registered = [{ bindingId: 'binding', name: 'support-app', revision: 1, qualified: false }];
beforeEach(() => { h.optionsError = false; h.suiteError = false; h.runtime = true; h.targets = []; h.answerRuns = []; h.run = {}; h.results = {}; window.localStorage.clear(); });
afterEach(cleanup);

it('shows regression cases, application verification and whole-context tests in one panel', () => {
  h.targets = registered;
  mount();
  expect(screen.getByText('Reviewed cases')).toBeVisible();
  expect(screen.getByRole('link', { name: 'Add case from a trace' })).toHaveAttribute('href', '/dashboard/observe');
  expect(screen.getByRole('table', { name: 'Context regression cases' })).toHaveTextContent('Refund failure');
  expect(screen.getByText('Application target')).toBeVisible();
  expect(screen.getByRole('radio', { name: /^Verify on the application/ })).toBeChecked();
  expect(screen.getByRole('radio', { name: /^Check answers with this context/ })).not.toBeChecked();
  expect(screen.queryByRole('textbox', { name: 'Test request' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('radio', { name: /^Check answers with this context/ }));
  expect(screen.getByText('Testing v4')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Preview whole-context test' })).toBeInTheDocument();
});
it('keeps application verification when whole-context testing is unavailable', () => {
  h.optionsError = true;
  h.targets = registered;
  mount();
  expect(screen.getByText('Application target')).toBeVisible();
  fireEvent.click(screen.getByRole('radio', { name: /^Check answers with this context/ }));
  expect(screen.getByText("Couldn't load answer-check options.")).toBeVisible();
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
  fireEvent.click(screen.getByRole('radio', { name: /^Check answers with this context/ }));
  fireEvent.click(screen.getByRole('radio', { name: 'Saved draft' }));
  expect(onSelect).toHaveBeenCalledWith(true);
  view.rerender(<MemoryRouter><ContextTestsPanel contextId="context" version={version} versions={[version]} canWrite source={{ ...source, testingDraft: true }} draft={{ revision: 9, digest }} /></MemoryRouter>);
  expect(screen.getByText('Testing your saved draft (revision 9)')).toBeVisible();
  // Application verification still names the viewed release.
  expect(screen.getByText('Reviewed cases')).toBeVisible();
  expect(screen.getByRole('radio', { name: 'Saved draft' })).toBeChecked();
  expect(screen.getByRole('radio', { name: 'Published version' })).toBeEnabled();
});
it('never falls back to the release while the saved draft is loading or failed', () => {
  const source = { testingDraft: true, draftAvailable: true, releaseAvailable: true, onSelect: () => {}, isError: true, onRetry: () => {} };
  mount({ source });
  fireEvent.click(screen.getByRole('radio', { name: /^Check answers with this context/ }));
  expect(screen.getByText('Saved draft unavailable')).toBeVisible();
  expect(screen.queryByText('Testing v4')).not.toBeInTheDocument();
  // The switch remains so the author can go back to the viewed release.
  expect(screen.getByRole('radio', { name: 'Published version' })).toBeEnabled();
});
it('hands the editor draft off to the saved-revision tests without an inline run', () => {
  const onOpen = vi.fn();
  render(<MemoryRouter><ContextTestsPanel contextId="context" canWrite onOpenSavedDraft={onOpen} /></MemoryRouter>);
  expect(screen.getByText('Reviewed cases')).toBeVisible();
  expect(screen.queryByRole('textbox', { name: 'Test request' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('radio', { name: /^Check answers with this context/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Save and open whole-context tests' }));
  expect(onOpen).toHaveBeenCalledTimes(1);
});

it('keeps a single first-run action and unticked acknowledgments while switching test types', () => {
  h.targets = registered;
  mount();
  expect(screen.getByRole('button', { name: 'Run your first test' })).toHaveClass('MuiButton-contained');
  fireEvent.click(screen.getByRole('radio', { name: /^Check answers with this context/ }));
  expect(screen.queryByRole('button', { name: 'Run your first test' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Preview whole-context test' })).toHaveClass('MuiButton-contained');
  expect(screen.getByRole('checkbox', { name: 'I reviewed this request and its expected answer.' })).not.toBeChecked();
  expect(screen.getByText('History')).toBeVisible();
  expect(screen.queryByText('Context test history')).not.toBeInTheDocument();
});

it('moves the first-run action into the selected form rather than its hidden sibling', async () => {
  h.targets = [{ ...registered[0], qualified: true, configurationDigest: digest }];
  mount();
  fireEvent.click(screen.getByRole('button', { name: 'Run your first test' }));
  await waitFor(() => expect(screen.getByRole('combobox', { name: /^Test version/ })).toHaveFocus());
  expect(screen.queryByRole('textbox', { name: 'Test request' })).not.toBeInTheDocument();
});
it('starts with the answer check when no application is registered', async () => {
  mount();
  expect(screen.getByRole('radio', { name: /^Check answers with this context/ })).toBeChecked();
  fireEvent.click(screen.getByRole('button', { name: 'Run your first test' }));
  await waitFor(() => expect(screen.getByRole('textbox', { name: 'Test request' })).toHaveFocus());
});
it('compares the answer check with a chosen published version, never a typed number', () => {
  const versions = [version, { id: 'v3', number: 3, digest }];
  render(<MemoryRouter><ContextTestsPanel contextId="context" version={version} versions={versions} productionVersion={3} canWrite /></MemoryRouter>);
  const compare = screen.getByRole('combobox', { name: /^Compare with/ });
  expect(compare).toHaveTextContent('Without the context');
  expect(screen.queryByRole('textbox', { name: /reference version/i })).not.toBeInTheDocument();
  fireEvent.mouseDown(compare);
  expect(screen.getByRole('option', { name: 'v3 (production)' })).toBeVisible();
  // The version under test is not offered as its own comparison.
  expect(screen.queryByRole('option', { name: /^v4/ })).not.toBeInTheDocument();
});
it('reads answer grades in plain words and offers the release review for a passing published version', () => {
  h.answerRuns = [{ id: 'answer-run', status: 'completed', created_at: '2026-11-13T12:00:00Z', candidate: { kind: 'version', version: 4, digest } }];
  h.run = { data: { status: 'completed', created_at: '2026-11-13T12:00:00Z', candidate: { kind: 'version', version: 4, digest }, reference: { version: 3, digest } } };
  const attempt = (id, verdict) => ({ id, status: 'completed', answer_verdict: verdict, repeat_index: 0 });
  h.results = { data: { total_pages: 1, results: [{ case_id: 'c1', title: 'Refund', variants: [{ variant: 'candidate', attempts: [attempt('a1', 'pass')] }, { variant: 'reference', attempts: [attempt('a2', 'not_graded')] }] }] } };
  mount({ contextName: 'support-agent' });
  expect(screen.getByText('Answer passed')).toBeVisible();
  expect(screen.getByText('Not graded')).toBeVisible();
  expect(screen.queryByText(/Answer: /)).not.toBeInTheDocument();
  expect(screen.queryByText('Whole-context result')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Inspect support-agent v4 attempt 1' })).toHaveClass('MuiButton-outlined');
  const request = screen.getByRole('link', { name: 'Request release review for v4' });
  expect(request).toHaveClass('MuiButton-contained');
  const params = new URL(request.href).searchParams;
  expect(params.get('evidence')).toBe('skills_text:answer-run');
  expect(params.get('candidate')).toBe('v4');
  expect(screen.getByRole('button', { name: 'Verify v4 on the application' })).toHaveClass('MuiButton-outlined');
  // The run form steps back while the result carries the primary action.
  fireEvent.click(screen.getByRole('button', { name: 'Show setup' }));
  expect(screen.getByRole('button', { name: 'Preview whole-context test' })).toHaveClass('MuiButton-outlined');
});
