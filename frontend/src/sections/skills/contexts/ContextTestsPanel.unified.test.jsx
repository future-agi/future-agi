import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import StatusBadge from 'src/components/status-badge';
import ContextTestsPanel from './ContextTestsPanel';
import { RETIRED_TERMS } from '../utils/terms';

// One Tests panel carries regression cases, the application test and the answer check, in the shared
// sub-tabs (Results, Run a test, Cases). Each part owns its loading and error states; none hides another.
const h = vi.hoisted(() => ({ optionsError: false, suiteError: false, runtime: true, retry: () => {}, targets: [], answerRuns: [], run: {}, results: {}, preview: null, cases: null, previews: [], created: [], suiteRevision: 1 }));
const idle = vi.hoisted(() => () => ({ mutate: () => {}, mutateAsync: async () => {}, reset: () => {}, isPending: false }));
const savedCase = { id: 'refund', title: 'Refund failure', prompt: 'Refund order 7', reviewed: true, checks: [{}] };
vi.mock('src/api/skills/r3-spend', () => ({ useSkillsSpendPolicy: () => ({}) }));
vi.mock('src/api/skills/testing', () => ({
  useSkillV11Scope: () => ({ identity: 'org:workspace:author' }),
  useSkillTestingOptions: () => (h.optionsError ? { isError: true, refetch: () => {} } : { data: { enabled: true, models: [{ modelId: 'gpt-4o-mini', canRun: true }], limits: { profiles: { quick: { maxTotalTokens: 1000 } } } } }),
  isActiveSkillJob: (status) => ['queued', 'running'].includes(status),
}));
vi.mock('src/api/skills/context-testing', () => ({
  useContextTestSuite: () => (h.suiteError ? { isError: true, refetch: () => {} } : { data: { revision: h.suiteRevision, cases: h.cases ?? [savedCase] } }),
  useSaveContextTestSuite: idle,
  contextTestCandidate: (draft) => ({ kind: 'draft', revision: draft.revision, digest: draft.digest }),
  usePreviewContextTest: () => ({ mutate: () => {}, mutateAsync: async (config) => { h.previews.push(config); return h.preview; }, reset: () => {}, isPending: false }),
  useCreateContextTest: () => ({ mutateAsync: async (input) => { h.created.push(input); return { id: 'started-run' }; }, isPending: false }),
  useCancelContextTest: idle,
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
const showTab = (name) => fireEvent.click(screen.getByRole('tab', { name }));
const showRun = () => showTab('Run a test');
const visiblePanel = () => screen.getByRole('tabpanel');
const containedNames = () => [...document.querySelectorAll('.MuiButton-contained')].filter((el) => !el.closest('[hidden]')).map((el) => el.textContent);
const answers = () => fireEvent.click(screen.getByRole('radio', { name: /^Check answers with this context/ }));
const newRequest = () => fireEvent.click(screen.getByRole('radio', { name: 'Write a new request' }));
const previewFixture = (fingerprint = 'sha256:preview', files = 3) => ({ fingerprint, attemptCount: 2, estimate: { upperBoundUsd: '0.23869440' }, providerDisclosure: { taskProvider: 'openai' },
  inventory: [{ skillId: 'context', digest, files: Array.from({ length: files }, (_, index) => ({ path: `file-${index}.md`, sha256: String(index).repeat(64).slice(0, 64), sizeBytes: 10, flags: [] })) }], outboundFindings: [] });
const sendBoxes = () => within(visiblePanel()).queryAllByRole('checkbox').filter((box) => /^Send /.test(box.closest('label')?.textContent || ''));
beforeEach(() => { h.optionsError = false; h.suiteError = false; h.runtime = true; h.targets = []; h.answerRuns = []; h.run = {}; h.results = {}; h.preview = null; h.cases = null; h.previews = []; h.created = []; h.suiteRevision = 1; window.localStorage.clear(); });
afterEach(cleanup);

it('shows regression cases, the application test and the answer check in their tabs', () => {
  h.targets = registered;
  mount();
  expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual(['Results', 'Run a test', 'Cases']);
  expect(screen.getByRole('tab', { name: 'Results' })).toHaveAttribute('aria-selected', 'true');
  showTab('Cases');
  expect(within(visiblePanel()).getByText('Test cases')).toBeVisible();
  expect(screen.getByRole('link', { name: 'Add test case from a trace' })).toHaveAttribute('href', '/dashboard/observe');
  expect(screen.getByRole('table', { name: 'Context regression cases' })).toHaveTextContent('Refund failure');
  showRun();
  expect(screen.getByText('Application target')).toBeVisible();
  expect(screen.getByRole('radio', { name: /^Test on the application/ })).toBeChecked();
  expect(screen.getByRole('radio', { name: /^Check answers with this context/ })).not.toBeChecked();
  expect(screen.queryByRole('combobox', { name: /^Saved test case/ })).not.toBeInTheDocument();
  answers();
  expect(testingLine('Testing v4')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Preview answer check' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /whole-context/ })).not.toBeInTheDocument();
});
it('names the answer check "Check answers" on screen while its accessible name still starts with that text', () => {
  mount();
  showRun();
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
  showRun();
  expect(screen.getByText('Application target')).toBeVisible();
  answers();
  expect(screen.getByText("Couldn't load answer-check options.")).toBeVisible();
  showTab('Cases');
  expect(screen.getByRole('table', { name: 'Context regression cases' })).toBeVisible();
});
it('keeps the answer check when the regression suite cannot load', () => {
  h.suiteError = true;
  mount();
  showTab('Cases');
  expect(within(visiblePanel()).getByText("Couldn't load test cases")).toBeVisible();
  showRun();
  // Without saved cases the answer check writes a new request.
  expect(screen.getByRole('textbox', { name: 'Test request' })).toBeVisible();
});
it('switches only the answer-check candidate to the saved draft', () => {
  const onSelect = vi.fn();
  const source = { testingDraft: false, draftAvailable: true, releaseAvailable: true, onSelect };
  const view = mount({ source });
  showRun();
  answers();
  fireEvent.click(screen.getByRole('radio', { name: 'Saved draft' }));
  expect(onSelect).toHaveBeenCalledWith(true);
  view.rerender(<MemoryRouter><ContextTestsPanel contextId="context" version={version} versions={[version]} canWrite source={{ ...source, testingDraft: true }} draft={{ revision: 9, digest }} /></MemoryRouter>);
  expect(screen.getByText('Testing your saved draft (revision 9)')).toBeVisible();
  // The application test and the cases still belong to the viewed release.
  expect(screen.getByText('Test cases')).toBeInTheDocument();
  expect(screen.getByRole('radio', { name: 'Saved draft' })).toBeChecked();
  expect(screen.getByRole('radio', { name: 'Published version' })).toBeEnabled();
});
it('never falls back to the release while the saved draft is loading or failed', () => {
  const source = { testingDraft: true, draftAvailable: true, releaseAvailable: true, onSelect: () => {}, isError: true, onRetry: () => {} };
  mount({ source });
  // Opened for the saved draft (the editor hand-off), so Run a test comes first.
  expect(screen.getByRole('tab', { name: 'Run a test' })).toHaveAttribute('aria-selected', 'true');
  answers();
  expect(screen.getByText('Saved draft unavailable')).toBeVisible();
  expect(testingLine('Testing v4')).toBeNull();
  // The switch remains so the author can go back to the viewed release.
  expect(screen.getByRole('radio', { name: 'Published version' })).toBeEnabled();
});
it('hands the editor draft off to the saved-revision tests without an inline run', () => {
  const onOpen = vi.fn();
  render(<MemoryRouter><ContextTestsPanel contextId="context" canWrite onOpenSavedDraft={onOpen} /></MemoryRouter>);
  showTab('Cases');
  expect(within(visiblePanel()).getByText('Test cases')).toBeVisible();
  showRun();
  expect(screen.queryByRole('textbox', { name: 'Test request' })).not.toBeInTheDocument();
  answers();
  fireEvent.click(screen.getByRole('button', { name: 'Save and open answer check' }));
  expect(onOpen).toHaveBeenCalledTimes(1);
});

it('keeps a single first-run action per tab and asks no tick for a saved, approved test case', () => {
  h.targets = registered;
  mount();
  // The saved case is in Cases, so Results says no run yet instead of "No tests yet", and leads to Run a test.
  expect(screen.getByText('No test runs yet')).toBeVisible();
  expect(screen.getByText('Your 1 saved test case is in Cases. Run a test to check answers or test the application. Neither changes production.')).toBeVisible();
  expect(screen.queryByText('No tests yet')).not.toBeInTheDocument();
  expect(containedNames()).toEqual(['Run a test']);
  fireEvent.click(within(visiblePanel()).getByRole('button', { name: 'Run a test' }));
  answers();
  const preview = screen.getByRole('button', { name: 'Preview answer check' });
  expect(preview).toHaveClass('MuiButton-contained');
  expect(preview).not.toHaveClass('MuiButton-sizeSmall');
  expect(containedNames()).toEqual(['Preview answer check']);
  // A saved, approved case is the default and carries its own approval: no tick before the preview.
  expect(screen.getByRole('radio', { name: 'Use a saved test case' })).toBeChecked();
  expect(screen.getByRole('combobox', { name: /^Saved test case/ })).toHaveTextContent('Refund failure');
  expect(screen.queryByRole('checkbox', { name: 'I checked this request and its expected answer.' })).not.toBeInTheDocument();
  showTab('Results');
  expect(screen.getByText('History')).toBeVisible();
  expect(screen.queryByText('Context test history')).not.toBeInTheDocument();
});
it('keeps the new-request tick, bound to the exact request and answer', () => {
  mount();
  showRun();
  newRequest();
  const tick = screen.getByRole('checkbox', { name: 'I checked this request and its expected answer.' });
  expect(tick).toBeDisabled();
  fireEvent.change(screen.getByRole('textbox', { name: 'Test request' }), { target: { value: 'Refund order 7' } });
  fireEvent.change(screen.getByRole('textbox', { name: 'Expected answer contains' }), { target: { value: '$25' } });
  fireEvent.click(tick);
  expect(tick).toBeChecked();
  fireEvent.change(screen.getByRole('textbox', { name: 'Expected answer contains' }), { target: { value: '$30' } });
  // A changed answer asks again.
  expect(screen.getByRole('checkbox', { name: 'I checked this request and its expected answer.' })).not.toBeChecked();
  expect(screen.getByText('The request or answer changed. Check it again.')).toBeVisible();
});

it('starts with the answer check on Run a test when no application is registered', () => {
  mount();
  showRun();
  expect(screen.getByRole('radio', { name: /^Check answers with this context/ })).toBeChecked();
  expect(screen.getByRole('button', { name: 'Preview answer check' })).toHaveClass('MuiButton-contained');
});
it('explains why the saved draft cannot be chosen', async () => {
  mount({ source: { testingDraft: false, draftAvailable: false, releaseAvailable: true, onSelect: () => {} } });
  showRun();
  const draft = screen.getByRole('radio', { name: 'Saved draft' });
  expect(draft).toBeDisabled();
  fireEvent.mouseOver(draft.closest('label').parentElement);
  expect(await screen.findByRole('tooltip')).toHaveTextContent('No saved draft for this context');
});
it('compares the answer check with a chosen published version, never a typed number', () => {
  const versions = [version, { id: 'v3', number: 3, digest }];
  render(<MemoryRouter><ContextTestsPanel contextId="context" version={version} versions={versions} productionVersion={3} canWrite /></MemoryRouter>);
  showRun();
  const compare = screen.getByRole('combobox', { name: /^Compare with/ });
  expect(compare).toHaveTextContent('Without the context');
  expect(screen.queryByRole('textbox', { name: /reference version/i })).not.toBeInTheDocument();
  fireEvent.mouseDown(compare);
  expect(screen.getByRole('option', { name: 'v3 (production)' })).toBeVisible();
  // The version under test is not offered as its own comparison.
  expect(screen.queryByRole('option', { name: /^v4/ })).not.toBeInTheDocument();
});
it('reads answer grades in plain words and offers the release request for a passing published version', () => {
  h.answerRuns = [{ id: 'answer-run', status: 'completed', created_at: '2026-11-13T12:00:00Z', candidate: { kind: 'version', version: 4, digest } }];
  h.run = { data: { status: 'completed', created_at: '2026-11-13T12:00:00Z', candidate: { kind: 'version', version: 4, digest }, reference: { version: 3, digest } } };
  const attempt = (id, verdict) => ({ id, status: 'completed', answer_verdict: verdict, repeat_index: 0 });
  h.results = { data: { total_pages: 1, results: [{ case_id: 'c1', title: 'Refund', variants: [{ variant: 'candidate', attempts: [attempt('a1', 'pass')] }, { variant: 'reference', attempts: [attempt('a2', 'not_graded')] }] }] } };
  mount({ contextName: 'support-agent' });
  expect(screen.getByText('Answer passed')).toBeVisible();
  expect(screen.getByText('Not graded')).toBeVisible();
  expect(screen.queryByText(/Answer: /)).not.toBeInTheDocument();
  expect(screen.queryByText('Whole-context result')).not.toBeInTheDocument();
  // An inline row action is a small ghost button, never outlined (UXF-12).
  expect(screen.getByRole('button', { name: 'Inspect v4 (testing) attempt 1' })).toHaveClass('MuiButton-text');
  // The payload's ad hoc case title is shown as the result heading, never as a "regression".
  expect(screen.getByRole('heading', { name: 'Answer check · v4 vs v3', level: 2 })).toBeVisible();
  expect(screen.queryByText('Reviewed context regression')).not.toBeInTheDocument();
  const request = screen.getByRole('link', { name: 'Request release for v4' });
  expect(request).toHaveClass('MuiButton-contained');
  const params = new URL(request.href).searchParams;
  expect(params.get('evidence')).toBe('skills_text:answer-run');
  expect(params.get('candidate')).toBe('v4');
  // No application is registered, so the release request leads and setting one up is the secondary.
  expect(screen.queryByRole('button', { name: 'Test v4 on the application' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Set up application test' })).toHaveClass('MuiButton-outlined');
  expect(screen.queryByRole('button', { name: 'Set up application verification' })).not.toBeInTheDocument();
  expect(containedNames()).toEqual(['Request release for v4']);
  // On Run a test the setup's preview is the one primary.
  showRun();
  expect(containedNames()).toEqual(['Preview answer check']);
  showTab('Results');
  expect(request).toHaveClass('MuiButton-contained');
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
  expect(screen.getByRole('tab', { name: 'Run a test' })).toHaveAttribute('aria-selected', 'true');
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
  expect(screen.queryByRole('link', { name: /^Request release/ })).not.toBeInTheDocument();
  expect(document.querySelector('a[href*="/review"]')).toBeNull();
  expect(screen.queryByText('Ready for a release request')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Test v4 on the application' })).not.toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'View release history' })).toHaveAttribute('href', '/dashboard/skills/contexts/context?tab=versions');
  // Nothing in the result claims the page's primary.
  expect(containedNames()).not.toContain('View release history');
});
it('offers a newer draft to test when production already serves the tested version', () => {
  answerResult();
  render(<MemoryRouter><ContextTestsPanel contextId="context" version={version} versions={[version]} productionVersion={4} canWrite source={{ testingDraft: false, draftAvailable: true, releaseAvailable: true, onSelect: () => {} }} /></MemoryRouter>);
  expect(screen.getByRole('link', { name: 'Test a newer draft' })).toHaveAttribute('href', '/dashboard/skills/contexts/context/edit');
  expect(screen.queryByRole('link', { name: 'View release history' })).not.toBeInTheDocument();
});
it('names the comparison row as the production baseline and marks its failure red, as expected', () => {
  answerResult();
  render(<MemoryRouter><ContextTestsPanel contextId="context" version={version} versions={[version, { id: 'v3', number: 3, digest }]} productionVersion={3} canWrite /></MemoryRouter>);
  const result = screen.getByRole('region', { name: 'Latest result' });
  expect(within(result).getByText('v4 (testing)')).toBeVisible();
  expect(within(result).getByText('v3 (production)')).toBeVisible();
  expect(within(result).getByText('expected', { exact: true })).toBeVisible();
  expect(within(result).queryByText(/the failure you’re fixing/)).not.toBeInTheDocument();
  // "Answer failed" is the same red failed badge on the baseline as anywhere else, never a grey dot.
  const failed = within(result).getByText('Answer failed', { exact: true });
  expect(failed).toBeVisible();
  const { container } = render(<StatusBadge status="failed" label="Answer failed" />);
  const reference = container.querySelector('span');
  expect(getComputedStyle(failed.parentElement).backgroundColor).toBe(getComputedStyle(reference).backgroundColor);
  expect(getComputedStyle(failed).fontWeight).toBe(getComputedStyle(reference.lastElementChild).fontWeight);
  expect(screen.queryByText(/^Compared with/)).not.toBeInTheDocument();
});
it('says nothing is expected when the tested version fails too', () => {
  answerResult({ candidate: 'fail', baseline: 'fail' });
  mount();
  const result = screen.getByRole('region', { name: 'Latest result' });
  expect(within(result).getAllByText('Answer failed', { exact: true })).toHaveLength(2);
  expect(within(result).queryByText('expected', { exact: true })).not.toBeInTheDocument();
});
it('leads with the application test when an application is registered', () => {
  h.targets = [{ ...registered[0], qualified: true, configurationDigest: digest }];
  answerResult();
  mount();
  expect(screen.getByRole('button', { name: 'Test v4 on the application' })).toHaveClass('MuiButton-contained');
  expect(screen.getByRole('link', { name: 'Request release for v4' })).toHaveClass('MuiButton-outlined');
  expect(containedNames()).toEqual(['Test v4 on the application']);
});
it('marks ungraded attempts as not started while the run is live, never "Not graded"', () => {
  answerResult({ status: 'running', candidate: 'not_graded', baseline: 'not_graded', attemptStatus: 'not_started' });
  mount();
  expect(screen.getAllByText('Not started')).toHaveLength(2);
  expect(screen.queryByText('Not graded')).not.toBeInTheDocument();
});

// UXF-03: one tick for a saved test case, two for a new one, plus one per flagged file or secret finding.
it('sends a saved test case by reference with one tick bound to the preview', async () => {
  h.preview = previewFixture();
  mount();
  showRun();
  fireEvent.change(screen.getByRole('textbox', { name: 'Maximum estimated cost (USD)' }), { target: { value: '1' } });
  fireEvent.click(screen.getByRole('button', { name: 'Preview answer check' }));
  const group = await screen.findByRole('group', { name: 'Before this runs' });
  expect(h.previews[0].cases).toEqual({ kind: 'saved', suite_revision: 1, case_ids: ['refund'] });
  const boxes = within(group).getAllByRole('checkbox');
  expect(boxes.map((box) => box.closest('label').textContent)).toEqual(['Send these 3 files and the test case to OpenAI, up to $0.24. They contain no answer keys or grading material.']);
  expect(within(visiblePanel()).getAllByRole('checkbox')).toHaveLength(1);
  // The file list is open beside the tick.
  expect(screen.getByRole('region', { name: 'View all files sent to the model' })).toBeVisible();
  const run = screen.getByRole('button', { name: 'Run answer check' });
  expect(run).toBeDisabled();
  fireEvent.click(boxes[0]);
  expect(run).toBeEnabled();
  fireEvent.click(run);
  await waitFor(() => expect(h.created).toHaveLength(1));
  // Every field the server requires is sent, bound to the same preview fingerprint.
  expect(h.created[0].expectedFingerprint).toBe('sha256:preview');
  expect(h.created[0].inventoryReview).toEqual({ fingerprint: 'sha256:preview', reviewedAllFiles: true, noKnownAnswerKeys: true, classifications: [], falsePositiveSecretFindings: [] });
  expect(h.created[0].configuration.cases).toEqual({ kind: 'saved', suite_revision: 1, case_ids: ['refund'] });
});
it('asks two ticks for a new request and builds its ad hoc case from the ticked text', async () => {
  h.preview = previewFixture('sha256:preview', 1);
  mount();
  showRun();
  newRequest();
  fireEvent.change(screen.getByRole('textbox', { name: 'Test request' }), { target: { value: 'Refund order 7' } });
  fireEvent.change(screen.getByRole('textbox', { name: 'Expected answer contains' }), { target: { value: '$25' } });
  fireEvent.click(screen.getByRole('checkbox', { name: 'I checked this request and its expected answer.' }));
  fireEvent.change(screen.getByRole('textbox', { name: 'Maximum estimated cost (USD)' }), { target: { value: '1' } });
  fireEvent.click(screen.getByRole('button', { name: 'Preview answer check' }));
  await screen.findByRole('group', { name: 'Before this runs' });
  expect(within(visiblePanel()).getAllByRole('checkbox')).toHaveLength(2);
  expect(sendBoxes().map((box) => box.closest('label').textContent)).toEqual(['Send this file and the request to OpenAI, up to $0.24. It contains no answer keys or grading material.']);
  const [adHoc] = h.previews[0].cases.cases;
  expect(h.previews[0].cases.kind).toBe('ad_hoc');
  expect(adHoc).toMatchObject({ prompt: 'Refund order 7', reviewed: true, checks: [{ config: { text: '$25' } }] });
});
it('adds one act per flagged file and per secret finding', async () => {
  const preview = previewFixture();
  preview.inventory[0].files[0].flags = ['evaluation_artifact'];
  preview.outboundFindings = [{ findingId: 'finding', code: 'secret_suspected', location: { kind: 'bundle_file', path: 'file-1.md' }, explanation: 'Match' }];
  h.preview = preview;
  mount();
  showRun();
  fireEvent.change(screen.getByRole('textbox', { name: 'Maximum estimated cost (USD)' }), { target: { value: '1' } });
  fireEvent.click(screen.getByRole('button', { name: 'Preview answer check' }));
  await screen.findByRole('group', { name: 'Before this runs' });
  expect(sendBoxes()).toHaveLength(1);
  expect(screen.getAllByRole('checkbox', { name: 'This is not a real secret' })).toHaveLength(1);
  expect(screen.getByRole('combobox', { name: /^Classify file-0\.md/ })).toBeInTheDocument();
  fireEvent.click(sendBoxes()[0]);
  // The send tick alone is not enough while a flagged file or a finding is open.
  expect(screen.getByRole('button', { name: 'Run answer check' })).toBeDisabled();
});
it('asks again after a new preview and drops a preview built from an older suite revision', async () => {
  h.preview = previewFixture('sha256:first');
  const view = mount();
  showRun();
  fireEvent.change(screen.getByRole('textbox', { name: 'Maximum estimated cost (USD)' }), { target: { value: '1' } });
  fireEvent.click(screen.getByRole('button', { name: 'Preview answer check' }));
  await screen.findByRole('group', { name: 'Before this runs' });
  fireEvent.click(sendBoxes()[0]);
  expect(sendBoxes()[0]).toBeChecked();
  h.preview = previewFixture('sha256:second');
  fireEvent.click(screen.getByRole('button', { name: 'Preview answer check' }));
  await waitFor(() => expect(h.previews).toHaveLength(2));
  await screen.findByRole('group', { name: 'Before this runs' });
  expect(sendBoxes()[0]).not.toBeChecked();
  expect(screen.getByRole('button', { name: 'Run answer check' })).toBeDisabled();
  // Someone changed the saved cases: the preview built from revision 1 is dropped.
  h.suiteRevision = 2;
  view.rerender(<MemoryRouter><ContextTestsPanel contextId="context" version={version} versions={[version]} canWrite /></MemoryRouter>);
  expect(screen.queryByRole('group', { name: 'Before this runs' })).not.toBeInTheDocument();
});

// UX iteration 4 (TST1, TST2).
it('names the answer check in the result and keeps the digest out of the setup line', () => {
  answerResult();
  mount();
  expect(screen.getByRole('heading', { name: 'Answer check · v4 vs v3', level: 2 })).toBeVisible();
  showRun();
  answers();
  // "Testing v4" is plain text; the full digest is one hover away on the version, not a chip.
  expect(testingLine('Testing v4')).toBeVisible();
  expect(screen.getByRole('button', { name: 'v4, show full version digest' })).toBeVisible();
  expect(screen.queryByText('Digest')).not.toBeInTheDocument();
  // No paragraph of explanation before the first field.
  expect(screen.queryByText(/Test runs are private/)).not.toBeInTheDocument();
  expect(screen.queryByText(/Tests your instructions and locked linked skills/)).not.toBeInTheDocument();
});
it('says the test is private at the confirmation step, beside the provider', async () => {
  h.preview = previewFixture();
  mount();
  showRun();
  fireEvent.change(screen.getByRole('textbox', { name: 'Maximum estimated cost (USD)' }), { target: { value: '1' } });
  fireEvent.click(screen.getByRole('button', { name: 'Preview answer check' }));
  expect(await screen.findByText('Test runs are private and don’t change production. The task provider, OpenAI, receives the files and request below.')).toBeVisible();
  expect(screen.getByText('Up to 2 runs · at most $0.24')).toBeVisible();
});
it('keeps the fields open for a first test in a context, even with settings carried from another one', () => {
  window.localStorage.setItem('skills.contextTest.last:org:workspace:author', JSON.stringify({ model: 'gpt-4o-mini', limit: '2', contextId: 'another-context' }));
  mount();
  showRun();
  expect(screen.getByRole('textbox', { name: 'Maximum estimated cost (USD)' })).toHaveValue('2');
  expect(screen.queryByText('gpt-4o-mini · up to $2')).not.toBeInTheDocument();
});
it('shows the model and cost last used in this context as one line, with the fields behind Change', () => {
  window.localStorage.setItem('skills.contextTest.last:org:workspace:author', JSON.stringify({ model: 'gpt-4o-mini', limit: '2', contextId: 'context' }));
  mount();
  showRun();
  expect(screen.getByText('gpt-4o-mini · up to $2')).toBeVisible();
  expect(screen.queryByRole('textbox', { name: 'Maximum estimated cost (USD)' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Change model and cost limit' }));
  expect(screen.getByRole('textbox', { name: 'Maximum estimated cost (USD)' })).toHaveValue('2');
  expect(screen.getByRole('combobox', { name: /^Task model/ })).toHaveTextContent('gpt-4o-mini');
  expect(screen.queryByText('gpt-4o-mini · up to $2')).not.toBeInTheDocument();
});
it('keeps the model and cost fields open when nothing is remembered', () => {
  mount();
  showRun();
  expect(screen.getByRole('textbox', { name: 'Maximum estimated cost (USD)' })).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Change model and cost limit' })).not.toBeInTheDocument();
});
it('describes each test type in one line under its radio', () => {
  mount();
  showRun();
  expect(screen.getByText('Compare answers using your instructions and linked skills. No workspace allowance needed.')).toBeVisible();
  expect(screen.getByText('Run approved test cases on your app. Uses your workspace allowance.')).toBeVisible();
});

// UXF-12 and UXF-14 in the Cases tab.
it('opens a test case from its row, says Approved or Needs approval, and uses no retired names', () => {
  h.cases = [savedCase, { id: 'new', title: 'Captured refund', prompt: 'Refund order 8', reviewed: false, checks: [] }];
  mount();
  showTab('Cases');
  const table = screen.getByRole('table', { name: 'Context regression cases' });
  expect(within(table).getByText('Approved')).toBeVisible();
  expect(within(table).getByText('Needs approval')).toBeVisible();
  expect(within(table).queryByRole('button', { name: /^(Open|Review case|Add checks)$/ })).not.toBeInTheDocument();
  fireEvent.click(within(table).getByLabelText('Actions for Captured refund'));
  expect(screen.getByRole('menuitem', { name: 'Add checks' })).toBeInTheDocument();
  fireEvent.keyDown(screen.getByRole('menu'), { key: 'Escape' });
  const text = [...document.body.querySelectorAll('*')].filter((element) => !element.closest('[hidden]') && element.children.length === 0).map((element) => element.textContent).join(' ');
  for (const term of RETIRED_TERMS) expect(text).not.toContain(term);
  fireEvent.click(within(table).getByText('Refund failure'));
  expect(screen.getByRole('dialog')).toBeInTheDocument();
});
