import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import ContextTestsPanel from './ContextTestsPanel';

// One Tests panel carries regression cases, the application test and the answer check.
// Each section owns its loading and error states; neither hides the other.
const h = vi.hoisted(() => ({ optionsError: false, suiteError: false, runtime: true, retry: () => {}, targets: [], answerRuns: [], run: {}, results: {}, preview: null }));
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
  usePreviewContextTest: () => ({ mutate: () => {}, mutateAsync: async () => h.preview, reset: () => {}, isPending: false }), useCreateContextTest: idle, useCancelContextTest: idle,
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
// "Testing v4": the version is a VersionLabel (digest on hover), so the line is matched by its full text.
const testingLine = (text) => screen.queryByText((_, element) => Boolean(element?.classList?.contains('MuiTypography-s1')) && element.textContent === text);
beforeEach(() => { h.optionsError = false; h.suiteError = false; h.runtime = true; h.targets = []; h.answerRuns = []; h.run = {}; h.results = {}; h.preview = null; window.localStorage.clear(); });
afterEach(cleanup);

it('shows regression cases, the application test and the answer check in one panel', () => {
  h.targets = registered;
  mount();
  expect(screen.getByText('Test cases')).toBeVisible();
  expect(screen.getByRole('link', { name: 'Add test case from a trace' })).toHaveAttribute('href', '/dashboard/observe');
  expect(screen.getByRole('table', { name: 'Context regression cases' })).toHaveTextContent('Refund failure');
  expect(screen.getByText('Application target')).toBeVisible();
  expect(screen.getByRole('radio', { name: /^Test on the application/ })).toBeChecked();
  expect(screen.getByRole('radio', { name: /^Check answers with this context/ })).not.toBeChecked();
  expect(screen.queryByRole('textbox', { name: 'Test request' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('radio', { name: /^Check answers with this context/ }));
  expect(testingLine('Testing v4')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Preview answer check' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /whole-context/ })).not.toBeInTheDocument();
});
it('names the answer check "Check answers" on screen while its accessible name still starts with that text', () => {
  mount();
  const radio = screen.getByRole('radio', { name: /^Check answers with this context/ });
  // The visible words are the start of the accessible name (WCAG 2.5.3); the rest is for screen readers only.
  expect(radio.closest('label').querySelector('.MuiTypography-s1').firstChild.textContent).toBe('Check answers');
  fireEvent.click(radio);
  // The section the radio opens carries the same short name.
  const titles = screen.getAllByText('Check answers', { exact: true }).filter((element) => !element.closest('label'));
  expect(titles).toHaveLength(1);
  expect(titles[0]).toBeVisible();
});
it('keeps the application test when the answer check is unavailable', () => {
  h.optionsError = true;
  h.targets = registered;
  mount();
  expect(screen.getByText('Application target')).toBeVisible();
  fireEvent.click(screen.getByRole('radio', { name: /^Check answers with this context/ }));
  expect(screen.getByText("Couldn't load answer-check options.")).toBeVisible();
  expect(screen.getByRole('table', { name: 'Context regression cases' })).toBeVisible();
});
it('keeps the answer check when the regression suite cannot load', () => {
  h.suiteError = true;
  mount();
  expect(screen.getByText("Couldn't load test cases")).toBeVisible();
  expect(screen.getByRole('textbox', { name: 'Test request' })).toBeVisible();
});
it('switches only the answer-check candidate to the saved draft', () => {
  const onSelect = vi.fn();
  const source = { testingDraft: false, draftAvailable: true, releaseAvailable: true, onSelect };
  const view = mount({ source });
  fireEvent.click(screen.getByRole('radio', { name: /^Check answers with this context/ }));
  fireEvent.click(screen.getByRole('radio', { name: 'Saved draft' }));
  expect(onSelect).toHaveBeenCalledWith(true);
  view.rerender(<MemoryRouter><ContextTestsPanel contextId="context" version={version} versions={[version]} canWrite source={{ ...source, testingDraft: true }} draft={{ revision: 9, digest }} /></MemoryRouter>);
  expect(screen.getByText('Testing your saved draft (revision 9)')).toBeVisible();
  // The application test still names the viewed release.
  expect(screen.getByText('Test cases')).toBeVisible();
  expect(screen.getByRole('radio', { name: 'Saved draft' })).toBeChecked();
  expect(screen.getByRole('radio', { name: 'Published version' })).toBeEnabled();
});
it('never falls back to the release while the saved draft is loading or failed', () => {
  const source = { testingDraft: true, draftAvailable: true, releaseAvailable: true, onSelect: () => {}, isError: true, onRetry: () => {} };
  mount({ source });
  fireEvent.click(screen.getByRole('radio', { name: /^Check answers with this context/ }));
  expect(screen.getByText('Saved draft unavailable')).toBeVisible();
  expect(testingLine('Testing v4')).toBeNull();
  // The switch remains so the author can go back to the viewed release.
  expect(screen.getByRole('radio', { name: 'Published version' })).toBeEnabled();
});
it('hands the editor draft off to the saved-revision tests without an inline run', () => {
  const onOpen = vi.fn();
  render(<MemoryRouter><ContextTestsPanel contextId="context" canWrite onOpenSavedDraft={onOpen} /></MemoryRouter>);
  expect(screen.getByText('Test cases')).toBeVisible();
  expect(screen.queryByRole('textbox', { name: 'Test request' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('radio', { name: /^Check answers with this context/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Save and open answer check' }));
  expect(onOpen).toHaveBeenCalledTimes(1);
});

it('keeps a single first-run action and unticked acknowledgments while switching test types', () => {
  h.targets = registered;
  mount();
  // The setup is already open, so the empty state adds no second button.
  expect(screen.queryByRole('button', { name: 'Run your first test' })).not.toBeInTheDocument();
  expect(screen.getByText('No tests yet')).toBeVisible();
  fireEvent.click(screen.getByRole('radio', { name: /^Check answers with this context/ }));
  const preview = screen.getByRole('button', { name: 'Preview answer check' });
  expect(preview).toHaveClass('MuiButton-contained');
  expect(preview).not.toHaveClass('MuiButton-sizeSmall');
  expect([...document.querySelectorAll('.MuiButton-contained')].filter((el) => !el.closest('[hidden]'))).toHaveLength(1);
  expect(screen.getByRole('checkbox', { name: 'I reviewed this request and its expected answer.' })).not.toBeChecked();
  expect(screen.getByText('History')).toBeVisible();
  expect(screen.queryByText('Context test history')).not.toBeInTheDocument();
});

it('starts with the answer check, open and primary, when no application is registered', () => {
  mount();
  expect(screen.getByRole('radio', { name: /^Check answers with this context/ })).toBeChecked();
  expect(screen.getByRole('textbox', { name: 'Test request' })).toBeVisible();
  expect(screen.getByRole('button', { name: 'Preview answer check' })).toHaveClass('MuiButton-contained');
});
it('explains why the saved draft cannot be chosen', async () => {
  mount({ source: { testingDraft: false, draftAvailable: false, releaseAvailable: true, onSelect: () => {} } });
  const draft = screen.getByRole('radio', { name: 'Saved draft' });
  expect(draft).toBeDisabled();
  fireEvent.mouseOver(draft.closest('label').parentElement);
  expect(await screen.findByRole('tooltip')).toHaveTextContent('No saved draft for this context');
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
  expect(screen.getByRole('button', { name: 'Inspect v4 (testing) attempt 1' })).toHaveClass('MuiButton-outlined');
  // The payload's ad hoc case title is shown as the result heading, never as a "regression".
  expect(screen.getByRole('heading', { name: 'Answer check · v4 vs v3', level: 2 })).toBeVisible();
  expect(screen.queryByText('Reviewed context regression')).not.toBeInTheDocument();
  const request = screen.getByRole('link', { name: 'Request release review for v4' });
  expect(request).toHaveClass('MuiButton-contained');
  const params = new URL(request.href).searchParams;
  expect(params.get('evidence')).toBe('skills_text:answer-run');
  expect(params.get('candidate')).toBe('v4');
  // No application is registered, so the release review leads and setting one up is the secondary.
  expect(screen.queryByRole('button', { name: 'Test v4 on the application' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Set up application test' })).toHaveClass('MuiButton-outlined');
  expect(screen.queryByRole('button', { name: 'Set up application verification' })).not.toBeInTheDocument();
  // The result carries the one primary until the reviewer works in the setup; opening it is not enough.
  expect([...document.querySelectorAll('.MuiButton-contained')].map((el) => el.textContent)).toEqual(['Request release review for v4']);
  fireEvent.click(screen.getByRole('button', { name: 'Show setup' }));
  expect(screen.getByRole('button', { name: 'Preview answer check' })).toHaveClass('MuiButton-outlined');
  expect(request).toHaveClass('MuiButton-contained');
  fireEvent.change(screen.getByRole('textbox', { name: 'Test request' }), { target: { value: 'Refund order 7' } });
  expect(screen.getByRole('button', { name: 'Preview answer check' })).toHaveClass('MuiButton-contained');
  expect(request).toHaveClass('MuiButton-outlined');
});
it('moves focus into the application form without scrolling the page when the answer result hands off', async () => {
  h.targets = [{ ...registered[0], qualified: true, configurationDigest: digest }];
  h.answerRuns = [{ id: 'answer-run', status: 'completed', created_at: '2026-11-13T12:00:00Z', candidate: { kind: 'version', version: 4, digest } }];
  h.run = { data: { status: 'completed', created_at: '2026-11-13T12:00:00Z', candidate: { kind: 'version', version: 4, digest } } };
  h.results = { data: { total_pages: 1, results: [{ case_id: 'c1', title: 'Refund', variants: [{ variant: 'candidate', attempts: [{ id: 'a1', status: 'completed', answer_verdict: 'pass', repeat_index: 0 }] }] }] } };
  const scrollIntoView = vi.fn();
  Element.prototype.scrollIntoView = scrollIntoView;
  mount();
  fireEvent.click(screen.getByRole('button', { name: 'Test v4 on the application' }));
  await waitFor(() => expect(screen.getByRole('combobox', { name: /^Test version/ })).toHaveFocus());
  expect(scrollIntoView).not.toHaveBeenCalled();
  delete Element.prototype.scrollIntoView;
});

const answerResult = ({ reference = 3, candidate = 'pass', baseline = 'fail', status = 'completed', attemptStatus = 'completed' } = {}) => {
  h.answerRuns = [{ id: 'answer-run', status, created_at: '2026-11-13T12:00:00Z', candidate: { kind: 'version', version: 4, digest } }];
  h.run = { data: { status, created_at: '2026-11-13T12:00:00Z', candidate: { kind: 'version', version: 4, digest }, reference: reference ? { version: reference, digest } : null } };
  const attempt = (id, verdict) => ({ id, status: attemptStatus, answer_verdict: verdict, repeat_index: 0 });
  h.results = { data: { total_pages: 1, results: [{ case_id: 'c1', title: 'Reviewed context regression', variants: [{ variant: 'candidate', attempts: [attempt('a1', candidate)] }, { variant: 'reference', attempts: [attempt('a2', baseline)] }] }] } };
};
it('confirms a passing version that is already in production instead of linking to a request the composer refuses', () => {
  answerResult();
  const versions = [version, { id: 'v3', number: 3, digest }];
  render(<MemoryRouter><ContextTestsPanel contextId="context" contextName="support-agent" version={version} versions={versions} productionVersion={4} canWrite /></MemoryRouter>);
  expect(screen.getByText('Answer check passed for v4 on 1 of 1 case.')).toBeVisible();
  expect(screen.getByText('Already in production')).toBeVisible();
  expect(screen.getByText('v4 is already in production. This result confirms the release.')).toBeVisible();
  expect(screen.queryByRole('link', { name: /Request release review/ })).not.toBeInTheDocument();
  expect(document.querySelector('a[href*="/review"]')).toBeNull();
  expect(screen.queryByText('Ready for release review')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Test v4 on the application' })).not.toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'View release history' })).toHaveAttribute('href', '/dashboard/skills/contexts/context?tab=versions');
  // Nothing in the result claims the page's primary; the setup can take it.
  expect([...document.querySelectorAll('.MuiButton-contained')].filter((el) => !el.closest('[hidden]')).map((el) => el.textContent)).not.toContain('View release history');
});
it('offers a newer draft to test when production already serves the tested version', () => {
  answerResult();
  render(<MemoryRouter><ContextTestsPanel contextId="context" version={version} versions={[version]} productionVersion={4} canWrite source={{ testingDraft: false, draftAvailable: true, releaseAvailable: true, onSelect: () => {} }} /></MemoryRouter>);
  expect(screen.getByRole('link', { name: 'Test a newer draft' })).toHaveAttribute('href', '/dashboard/skills/contexts/context/edit');
  expect(screen.queryByRole('link', { name: 'View release history' })).not.toBeInTheDocument();
});
it('names the comparison row as the production baseline, in a neutral tone, and the failure being fixed', () => {
  answerResult();
  render(<MemoryRouter><ContextTestsPanel contextId="context" version={version} versions={[version, { id: 'v3', number: 3, digest }]} productionVersion={3} canWrite /></MemoryRouter>);
  const result = screen.getByRole('region', { name: 'Latest result' });
  expect(within(result).getByText('v4 (testing)')).toBeVisible();
  expect(within(result).getByText('v3 (production)')).toBeVisible();
  expect(within(result).getByText('· the failure you’re fixing')).toBeVisible();
  // The badge label stays exactly "Answer failed"; only the version under test is coloured.
  expect(within(result).getByText('Answer failed', { exact: true })).toBeVisible();
  expect(screen.queryByText(/^Compared with/)).not.toBeInTheDocument();
});
it('leads with the application test when an application is registered', () => {
  h.targets = [{ ...registered[0], qualified: true, configurationDigest: digest }];
  answerResult();
  mount();
  expect(screen.getByRole('button', { name: 'Test v4 on the application' })).toHaveClass('MuiButton-contained');
  expect(screen.getByRole('link', { name: 'Request release review for v4' })).toHaveClass('MuiButton-outlined');
  expect([...document.querySelectorAll('.MuiButton-contained')].filter((el) => !el.closest('[hidden]')).map((el) => el.textContent)).toEqual(['Test v4 on the application']);
});
it('marks ungraded attempts as not started while the run is live, never "Not graded"', () => {
  answerResult({ status: 'running', candidate: 'not_graded', baseline: 'not_graded', attemptStatus: 'not_started' });
  mount();
  expect(screen.getAllByText('Not started')).toHaveLength(2);
  expect(screen.queryByText('Not graded')).not.toBeInTheDocument();
});
it('groups the three answer-check acknowledgments under one heading without merging or pre-ticking them', async () => {
  h.preview = { fingerprint: 'sha256:preview', attemptCount: 2, estimate: { upperBoundUsd: '0.10' }, providerDisclosure: { taskProvider: 'openai' }, inventory: [], outboundFindings: [] };
  mount();
  fireEvent.change(screen.getByRole('textbox', { name: 'Test request' }), { target: { value: 'Refund order 7' } });
  fireEvent.change(screen.getByRole('textbox', { name: 'Expected answer contains' }), { target: { value: '$25' } });
  fireEvent.click(screen.getByRole('checkbox', { name: 'I reviewed this request and its expected answer.' }));
  fireEvent.change(screen.getByRole('textbox', { name: 'Maximum estimated cost (USD)' }), { target: { value: '1' } });
  fireEvent.click(screen.getByRole('button', { name: 'Preview answer check' }));
  const group = await screen.findByRole('group', { name: 'Before this runs' });
  const names = ['The files sent to the model contain no answer keys or grading material.', 'I reviewed every file and input that will be sent to the model.', 'I acknowledge provider processing of this reviewed context and test request.'];
  const boxes = within(group).getAllByRole('checkbox');
  expect(boxes.map((box) => box.closest('label').textContent)).toEqual(names);
  for (const box of boxes) expect(box).not.toBeChecked();
  fireEvent.click(boxes[2]);
  expect(boxes[2]).toBeChecked();
  expect(boxes[0]).not.toBeChecked();
  expect(boxes[1]).not.toBeChecked();
  expect(screen.getByRole('button', { name: 'Run answer check' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'About this preview' })).toBeInTheDocument();
});

// UX iteration 4 (TST1, TST2).
it('names the answer check in the result and keeps the digest out of the setup line', () => {
  answerResult();
  mount();
  expect(screen.getByRole('heading', { name: 'Answer check · v4 vs v3', level: 2 })).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Show setup' }));
  // "Testing v4" is plain text; the full digest is one hover away on the version, not a chip.
  expect(testingLine('Testing v4')).toBeVisible();
  expect(screen.getByRole('button', { name: 'v4, show full version digest' })).toBeVisible();
  expect(screen.queryByText('Digest')).not.toBeInTheDocument();
  // No paragraph of explanation before the first field.
  expect(screen.queryByText(/Test runs are private/)).not.toBeInTheDocument();
  expect(screen.queryByText(/Tests your instructions and locked linked skills/)).not.toBeInTheDocument();
});
it('says the test is private at the confirmation step, beside the provider', async () => {
  h.preview = { fingerprint: 'sha256:preview', attemptCount: 2, estimate: { upperBoundUsd: '0.10' }, providerDisclosure: { taskProvider: 'openai' }, inventory: [], outboundFindings: [] };
  mount();
  fireEvent.change(screen.getByRole('textbox', { name: 'Test request' }), { target: { value: 'Refund order 7' } });
  fireEvent.change(screen.getByRole('textbox', { name: 'Expected answer contains' }), { target: { value: '$25' } });
  fireEvent.click(screen.getByRole('checkbox', { name: 'I reviewed this request and its expected answer.' }));
  fireEvent.change(screen.getByRole('textbox', { name: 'Maximum estimated cost (USD)' }), { target: { value: '1' } });
  fireEvent.click(screen.getByRole('button', { name: 'Preview answer check' }));
  expect(await screen.findByText('Test runs are private and don’t change production. The task provider, OpenAI, receives the files and request below.')).toBeVisible();
});
it('keeps the fields open for a first test in a context, even with settings carried from another one', () => {
  window.localStorage.setItem('skills.contextTest.last:org:workspace:author', JSON.stringify({ model: 'gpt-4o-mini', limit: '2', contextId: 'another-context' }));
  mount();
  expect(screen.getByRole('textbox', { name: 'Maximum estimated cost (USD)' })).toHaveValue('2');
  expect(screen.queryByText('gpt-4o-mini · up to $2')).not.toBeInTheDocument();
});
it('shows the model and cost last used in this context as one line, with the fields behind Change', () => {
  window.localStorage.setItem('skills.contextTest.last:org:workspace:author', JSON.stringify({ model: 'gpt-4o-mini', limit: '2', contextId: 'context' }));
  mount();
  expect(screen.getByText('gpt-4o-mini · up to $2')).toBeVisible();
  expect(screen.queryByRole('textbox', { name: 'Maximum estimated cost (USD)' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Change model and cost limit' }));
  expect(screen.getByRole('textbox', { name: 'Maximum estimated cost (USD)' })).toHaveValue('2');
  expect(screen.getByRole('combobox', { name: /^Task model/ })).toHaveTextContent('gpt-4o-mini');
  expect(screen.queryByText('gpt-4o-mini · up to $2')).not.toBeInTheDocument();
});
it('keeps the model and cost fields open when nothing is remembered', () => {
  mount();
  expect(screen.getByRole('textbox', { name: 'Maximum estimated cost (USD)' })).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Change model and cost limit' })).not.toBeInTheDocument();
});
it('describes each test type in one line under its radio', () => {
  mount();
  expect(screen.getByText('Compare answers using your instructions and linked skills. No workspace allowance needed.')).toBeVisible();
  expect(screen.getByText('Run reviewed cases on your app. Uses your workspace allowance.')).toBeVisible();
});
