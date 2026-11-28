import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ContextNativePanel, { pinSentence } from './ContextNativePanel';
import { noReviewsYet } from '../utils/conclusions';

const h = vi.hoisted(() => ({ preview: { data: undefined, plans: [] }, create: vi.fn(), runs: [], detail: undefined, annotations: [], annotationIds: [], annotationError: undefined, prepared: undefined, spend: {}, access: { canAdmin: true }, targets: undefined }));
vi.mock('src/api/skills/r3-spend', () => ({ useSkillsSpendPolicy: () => h.spend }));
vi.mock('../utils/access', async (original) => ({ ...(await original()), useSkillsAccess: () => h.access }));
vi.mock('src/api/skills/context-testing', () => ({
  useNativeTargets: () => (h.targetsLoading ? { isLoading: true, data: undefined } : { data: { runtime: { available: true, runtime: 'container', evidenceClass: 'synthetic fixture' }, targets: h.targets ?? [{ bindingId: 'binding', name: 'support-app', revision: 2, qualified: true, configurationDigest: 'sha256:abc' }] } }),
  useNativeRuns: () => ({ data: h.runs }),
  useNativeRun: (_, id) => (h.detailError ? { isError: true, isLoading: false, refetch: () => {} } : { data: id ? h.detail : undefined, isLoading: false }),
  usePreviewNativeRun: () => ({
    get data() { return h.preview.data; }, isPending: false, isError: false, reset: () => { h.preview.data = undefined; },
    mutate: (plan, options) => { h.preview.plans.push(plan); h.preview.data = { executionFingerprint: 'sha256:plan', reproducible: true, missingInputs: [], cells: 6, ceilingUsd: 1.5, arms: [{ variant: 'candidate', version: plan.candidateVersion }] }; options?.onSuccess?.(); },
  }),
  useCreateNativeRun: () => ({ mutateAsync: h.create, isPending: false, isError: false }),
  useCreateNativeTarget: () => ({ mutate: () => {}, isPending: false }), useQualifyNativeTarget: () => ({ mutateAsync: (...args) => (h.qualify ? h.qualify(...args) : Promise.resolve()), isPending: false }),
  useRuntimeConfiguration: () => h.configuration ?? { data: undefined }, usePrepareReleasePacket: () => ({ data: h.prepared, mutate: () => {}, isPending: false }),
}));
vi.mock('src/api/skills/evidence-annotations', () => ({ useEvidenceReference: () => (h.reference ?? {}), useAllEvidenceAnnotations: (_, id) => { h.annotationIds.push(id ?? null); if (!id) return { isLoading: false, data: undefined }; return h.annotationError ? { isError: true, error: h.annotationError, refetch: () => {} } : { data: { results: h.annotations } }; }, useAppendEvidenceAnnotation: () => ({ mutateAsync: h.append ?? (async () => {}) }) }));
vi.mock('src/api/skills/context-authoring', () => ({ useContextDetail: () => ({ data: { name: 'Support context' } }) }));
vi.mock('src/api/skills/j6-recovery', () => ({ useRecoveryReviewers: () => ({ data: [{ id: 'reviewer', name: 'Case reviewer' }] }) }));
vi.mock('src/auth/hooks', () => ({ useAuthContext: () => ({ user: { id: 'user' } }) }));
const versions = [7, 6, 4].map((number) => ({ number, id: `v${number}` }));
const cases = [{ id: 'refund', title: 'Original refund failure', reviewed: true }, { id: 'chargeback', title: 'Working chargeback case', reviewed: true }, { id: 'draft', title: 'Unreviewed', reviewed: false }];
const renderPanel = (props = {}) => render(<MemoryRouter><ContextNativePanel contextId="context" versions={versions} viewed={{ number: 7 }} cases={cases} canWrite productionVersion={6} {...props} /></MemoryRouter>);
beforeEach(() => {
  h.configuration = undefined; h.qualify = undefined; h.reference = undefined; h.append = undefined; h.annotations = []; h.annotationIds = []; h.annotationError = undefined; h.prepared = undefined; h.spend = {}; h.access = { canAdmin: true }; h.targets = undefined; h.targetsLoading = false;
  h.preview = { data: undefined, plans: [] }; h.create = vi.fn().mockResolvedValue('evidence');
  h.runs = [{ id: 'previous', arms: [{ variant: 'candidate', version: 6 }, { variant: 'reference', version: 4 }] }];
  h.detail = { id: 'previous', status: 'completed', executionFingerprint: 'sha256:previous', received: 2, planned: 2, arms: h.runs[0].arms, cases: [{ id: 'refund', historical: true }, { id: 'chargeback', historical: false }] };
});
afterEach(cleanup);

// The Tests sub-tabs (UXF-04): Results is the default; the setup is under "Run a test".
const showTab = (name) => fireEvent.click(screen.getByRole('tab', { name }));
const showRun = () => showTab('Run a test');
const visiblePanel = () => screen.getByRole('tabpanel');
const containedNames = () => [...document.querySelectorAll('.MuiButton-contained')].filter((el) => !el.closest('[hidden]')).map((el) => el.textContent);
const cellRow = (name) => screen.getByRole('row', { name });

it('plans the matched run from stored data and starts it only after the reviewer accepts the paid ceiling', async () => {
  renderPanel();
  showRun();
  const button = await screen.findByRole('button', { name: 'Run application test' });
  expect(button).toBeDisabled();
  fireEvent.click(screen.getByRole('checkbox', { name: 'I accept up to $1.5 in provider charges for this run.' }));
  await waitFor(() => expect(button).toBeEnabled());
  expect(h.preview.plans.at(-1)).toMatchObject({ candidateVersion: 7, baselines: { reference: 4, production: 6 }, caseIds: ['refund', 'chargeback'], historicalCaseIds: ['refund'], repetitions: 1 });
  expect(screen.getByRole('checkbox', { name: 'Original refund failure' })).toBeChecked();
  expect(screen.getByRole('checkbox', { name: 'Working chargeback case' })).toBeChecked();
  expect(screen.queryByRole('button', { name: 'Preview application run' })).not.toBeInTheDocument();
  fireEvent.click(button);
  await waitFor(() => expect(h.create).toHaveBeenCalledOnce());
  expect(h.create.mock.calls[0][0]).toMatchObject({ acknowledgeSpend: true, executionFingerprint: 'sha256:plan' });
});
it('reproduces only the example just captured against its recorded version', async () => {
  h.runs = [];
  renderPanel({ viewed: { number: 6 }, productionVersion: 6, seed: { caseId: 'refund', reference: 4 } });
  // A reproduction hand-off opens on Run a test.
  expect(screen.getByRole('tab', { name: 'Run a test' })).toHaveAttribute('aria-selected', 'true');
  await screen.findByRole('button', { name: 'Run application test' });
  expect(h.preview.plans.at(-1)).toMatchObject({ candidateVersion: 6, baselines: { reference: 4 }, caseIds: ['refund'], historicalCaseIds: ['refund'] });
  expect(h.preview.plans.at(-1).baselines.production).toBeUndefined();
  expect(screen.getByRole('checkbox', { name: 'Working chargeback case' })).not.toBeChecked();
});
it('re-plans after the reviewer deselects an example and refuses a stale plan', async () => {
  renderPanel();
  showRun();
  await screen.findByRole('button', { name: 'Run application test' });
  fireEvent.click(screen.getByRole('checkbox', { name: 'Working chargeback case' }));
  await waitFor(() => expect(h.preview.plans.at(-1).caseIds).toEqual(['refund']));
});
it('plans nothing without a recorded reference or production baseline', async () => {
  h.runs = [];
  renderPanel({ productionVersion: undefined });
  showRun();
  expect((await screen.findAllByText('Was failing on')).length).toBeGreaterThan(0);
  expect(h.preview.plans).toHaveLength(0);
  expect(screen.queryByRole('button', { name: 'Run application test' })).not.toBeInTheDocument();
});

it('uses one baseline when recorded reference is also production and retains the fingerprint gate', async () => {
  h.runs = [];
  renderPanel({ seed: { caseId: 'refund', reference: 6 } });
  const button = await screen.findByRole('button', { name: 'Run application test' });
  expect(h.preview.plans).toHaveLength(1);
  expect(h.preview.plans[0].baselines).toEqual({ reference: 6 });
  expect(screen.getByText('v6 is also production, so it runs once.')).toBeVisible();
  expect(screen.getByText(/Up to 6 runs · at most/)).toBeVisible();
  expect(button).toBeDisabled();
  fireEvent.click(screen.getByRole('checkbox', { name: 'I accept up to $1.5 in provider charges for this run.' }));
  fireEvent.click(button);
  await waitFor(() => expect(h.create).toHaveBeenCalledOnce());
  expect(h.create.mock.calls[0][0].executionFingerprint).toBe('sha256:plan');
});
it('prevents previews with the candidate duplicated as the recorded reference', async () => {
  h.runs = [];
  renderPanel({ seed: { caseId: 'refund', reference: 7 } });
  expect(h.preview.plans).toHaveLength(0);
  // A handoff that defaults both to the recorded version asks a question instead of opening in an error state.
  expect(screen.getByText('v7 is where it was failing. Choose the version that should fix it.')).toBeVisible();
  expect(screen.queryByText('Choose a version different from the one being tested.')).not.toBeInTheDocument();
});
it('opens an attempt from its row with the keyboard, shows disagreement scope and returns focus on Close', async () => {
  const user = userEvent.setup();
  h.detail.cells = [{ caseId: 'refund', variant: 'candidate', repeatIndex: 0, attemptId: 'attempt', answer: 'pass', selection: 'pass', delivery: 'pass', tools: 'pass' }];
  h.detail.cases[0].title = 'Refund';
  h.annotations = [{ id: 'annotation', attemptId: 'attempt', actorId: 'reviewer', verdict: 'disagree', comment: 'Wrong business action', createdAt: '2026-11-12T12:00:00Z' }];
  renderPanel();
  const row = cellRow('Inspect Refund Version tested repeat 1');
  row.focus();
  await user.keyboard('{Enter}');
  expect(screen.getByRole('heading', { name: 'Refund', level: 2 })).toBeVisible();
  // An assessment is recorded against this one run of the case, and says so.
  expect(screen.getByText('Applies to this run only: version tested v6 · Refund · run 1')).toBeVisible();
  expect(screen.queryByText(/whole run/)).not.toBeInTheDocument();
  expect(screen.getByText('Version tested v6 · Run 1 of 1')).toBeVisible();
  expect(screen.getAllByText('Human check').some((node) => node.closest('[role=dialog]'))).toBe(true);
  expect(screen.getByText("Your assessment is added next to the automatic result; it doesn't change it.")).toBeVisible();
  expect(screen.getByText('Disagrees')).toBeVisible();
  expect(screen.getByText('Wrong business action')).toBeVisible();
  expect(screen.getByText('Case reviewer')).toBeVisible();
  expect(screen.getByText('1 disagreement below')).toBeVisible();
  await user.click(screen.getByRole('button', { name: 'Close', exact: true }));
  await waitFor(() => expect(row).toHaveFocus());
});
it('opens an attempt from the row ⋯ menu and returns focus to that button', async () => {
  h.detail.cells = [{ caseId: 'refund', variant: 'candidate', repeatIndex: 0, attemptId: 'attempt', answer: 'pass', selection: 'pass', delivery: 'pass', tools: 'pass' }];
  h.detail.cases[0].title = 'Refund';
  renderPanel();
  const row = cellRow('Inspect Refund Version tested repeat 1');
  // No outlined per-row button: the row opens, and Inspect is the first ⋯ item (UXF-12).
  expect(within(row).queryByRole('button', { name: /^Inspect/ })).not.toBeInTheDocument();
  const menu = within(row).getByRole('button', { name: 'Actions for Refund Version tested repeat 1' });
  fireEvent.click(menu);
  fireEvent.click(screen.getByRole('menuitem', { name: 'Inspect' }));
  expect(screen.getByRole('heading', { name: 'Refund', level: 2 })).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Close', exact: true }));
  await waitFor(() => expect(menu).toHaveFocus());
});
it('continues to the composer with the complete candidate, target and shared run identity', () => {
  h.detail.decision = { state: 'release_ready', reasons: [] };
  h.detail.arms[0] = { variant: 'candidate', version: 7, versionId: 'candidate-id', digest: 'sha256:full-digest' };
  h.prepared = { fingerprint: 'sha256:packet', targetDigest: 'sha256:target' };
  renderPanel();
  const url = new URL(screen.getByRole('link', { name: 'Request release for v7' }).href);
  expect(Object.fromEntries(url.searchParams)).toMatchObject({ kind: 'agent_context', subject: 'context', name: 'Support context', candidate: 'candidate-id', number: '7', digest: 'sha256:full-digest', evidence: 'simulation_native:previous', target: 'binding', label: 'production' });
  expect(url.searchParams.has('annotation_revision')).toBe(false);
  // The CI attachment lives under the card's Technical details, kept mounted while folded.
  expect(screen.getByText(/Packet ready/)).not.toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Technical details' }));
  expect(screen.getByText(/Packet ready/)).toBeVisible();
});

it('chooses the newest result across both run types and keeps a combined history', () => {
  h.runs[0].createdAt = '2026-11-11T12:00:00Z';
  renderPanel({ answerHistory: { data: { results: [{ id: 'answer', status: 'completed', created_at: '2026-11-12T12:00:00Z', candidate: { version: 7 } }] } }, renderAnswer: (id) => <p>Answer result {id}</p> });
  expect(screen.getByText('Answer result answer')).toBeVisible();
  expect(screen.getByRole('table', { name: 'Test history' })).toHaveTextContent('Answer check');
  expect(screen.getByRole('table', { name: 'Test history' })).toHaveTextContent('Application test');
  expect(screen.getByRole('table', { name: 'Test history' })).not.toHaveTextContent('Verify on the application');
});
it('explains disabled packet prerequisites and marks the earlier-run scope', () => {
  h.detail.decision = { state: 'release_ready', reasons: [] };
  // The earlier run tested v6 while production is v4, so its release packet can still be prepared.
  renderPanel({ productionVersion: 4 });
  fireEvent.click(screen.getByRole('button', { name: 'Technical details' }));
  expect(screen.getByRole('group', { name: 'Attach your CI run (optional)' })).toBeVisible();
  expect(screen.getByText('Fill in the three CI fields to attach your CI run.')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Share run and attach CI result' })).toBeDisabled();
  expect(screen.getByText('This applies to the earlier run, not your current selection.')).toBeVisible();
  // An earlier result leads with the rerun on the current selection, even though it passed: its release
  // request steps back and says how much of the context it left untested.
  expect(screen.getByRole('button', { name: 'Run on current selection' })).toHaveClass('MuiButton-contained');
  expect(screen.getByRole('link', { name: 'Request release for v6' })).toHaveClass('MuiButton-outlined');
  expect(screen.getByText('1 of 3 cases not tested')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Run on current selection' }));
  expect(screen.getByRole('tab', { name: 'Run a test' })).toHaveAttribute('aria-selected', 'true');
  expect(screen.getByRole('button', { name: 'Run application test' })).toHaveClass('MuiButton-contained');
});
it('reconciles preparing plus returned work without announcing the result grid', () => {
  h.detail.jobState = 'provisioning';
  h.detail.received = 1;
  h.detail.decision = { state: 'release_ready', reasons: [] };
  h.detail.cells = [{ caseId: 'refund', variant: 'candidate', repeatIndex: 0, status: 'running' }];
  renderPanel();
  expect(within(visiblePanel()).getByRole('status')).toHaveTextContent('1 of 2 runs finished');
  expect(within(visiblePanel()).getByRole('progressbar')).toHaveAttribute('aria-valuenow', '50');
  expect(screen.getByRole('region', { name: 'Latest result' })).not.toHaveAttribute('aria-live');
  expect(screen.queryByText('Ready for a release request')).not.toBeInTheDocument();
  expect(screen.getAllByText('Pending')).toHaveLength(4);
});

it('continues to the release request without CI and keeps CI an optional attachment under Technical details', () => {
  h.detail.arms[0] = { variant: 'candidate', version: 7, versionId: 'v7-id', digest: 'sha256:v7' };
  h.detail.decision = { state: 'release_ready', reasons: [] };
  renderPanel();
  const proceed = screen.getByRole('link', { name: 'Request release for v7' });
  expect(proceed).toHaveClass('MuiButton-contained');
  expect(new URL(proceed.href).searchParams.get('evidence')).toBe('simulation_native:previous');
  expect(screen.getByText('Ready for a release request')).toBeVisible();
  // Folded under the result card's Technical details; the fields keep their text while folded.
  const disclosure = screen.getByRole('button', { name: 'Technical details' });
  expect(disclosure).toHaveAttribute('aria-expanded', 'false');
  expect(screen.getByRole('textbox', { name: 'CI run link', hidden: true })).not.toBeVisible();
  fireEvent.click(disclosure);
  for (const label of ['CI run link', 'CI pipeline name', 'CI result (pass or hold)']) expect(screen.getByRole('textbox', { name: label })).toBeVisible();
  fireEvent.change(screen.getByRole('textbox', { name: 'CI run link' }), { target: { value: 'https://ci.example/run/1' } });
  fireEvent.click(disclosure);
  fireEvent.click(disclosure);
  expect(screen.getByRole('textbox', { name: 'CI run link' })).toHaveValue('https://ci.example/run/1');
  expect(screen.queryByRole('textbox', { name: 'CI configuration ID' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Share run and attach CI result' })).toHaveClass('MuiButton-outlined');
  // Opening the release request never shares or prepares anything by itself.
  expect(screen.queryByText(/Packet ready/)).not.toBeInTheDocument();
  // The CI form is no longer a separate block after the per-case table.
  expect(screen.queryByRole('button', { name: 'Attach your CI run (optional)' })).not.toBeInTheDocument();
});

it('explains an automatically omitted production comparison', () => {
  renderPanel({ viewed: { number: 6 } });
  showRun();
  expect(screen.getByRole('combobox', { name: /^Compare with/ })).toHaveTextContent('No comparison');
  expect(screen.getByText('v6 is production, so it runs once as the version tested.')).toBeVisible();
});

it('keeps the latest retained result visible when application options fail', () => {
  h.detail.arms[0] = { variant: 'candidate', version: 7 };
  renderPanel({ state: 'error' });
  expect(screen.getByRole('region', { name: 'Latest result' })).toHaveTextContent('2 of 2 runs finished');
  showRun();
  expect(screen.getByText('You can still open past results and test cases.')).toBeVisible();
});

it('leads with the server result and opens the first disagreeing attempt from its count', async () => {
  h.detail.result = { repair_verified: true, reproduction: 'reproduced', regressed_cases: 0 };
  h.detail.decision = { state: 'release_ready', reasons: ['All required checks passed'] };
  h.detail.cells = ['refund', 'chargeback'].map((caseId) => ({ caseId, variant: 'candidate', repeatIndex: 0, status: 'completed', attemptId: caseId, answer: 'pass', selection: 'pass', delivery: 'pass', tools: 'pass' }));
  h.detail.cases[0].title = 'Refund';
  h.annotations = [{ id: 'annotation', attemptId: 'refund', actorId: 'reviewer', verdict: 'disagree', comment: 'Inspect receipt', createdAt: '2026-11-12T12:00:00Z' }];
  renderPanel();
  // Two of the context's three cases ran, so the regression sentence is scoped to them.
  expect(screen.getByText('Repair verified: v6 passed all 2 cases; the recorded failure reproduced on v4. No regressions in the 2 tested cases; 1 other case was not tested.')).toBeVisible();
  const link = screen.getByRole('button', { name: '1 disagreement to inspect' });
  fireEvent.click(link);
  expect(screen.getByRole('heading', { name: 'Refund' })).toBeVisible();
  expect(screen.getByText('Inspect receipt')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Close', exact: true }));
  await waitFor(() => expect(link).toHaveFocus());
});

it('does not infer a verified repair or release readiness from a passing answer', () => {
  h.detail.result = { repair_verified: false, reproduction: 'not_reproduced', regressed_cases: 1 };
  h.detail.decision = { state: 'hold', reasons: ['Business action failed'] };
  h.detail.cells = [{ caseId: 'refund', variant: 'candidate', repeatIndex: 0, status: 'completed', answer: 'pass', tools: 'fail', selection: 'pass', delivery: 'pass' }];
  renderPanel();
  expect(screen.getByText('Repair not verified: v6 passed 0 of 2 cases; the recorded failure did not reproduce. 1 case regressed; 1 other case was not tested.')).toBeVisible();
  expect(screen.queryByText('Ready for a release request')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Share run and attach CI result', hidden: true })).not.toBeInTheDocument();
});

it('offers Run a test as the one primary in the empty preview, and the setup takes it there', async () => {
  renderPanel({ state: 'empty' });
  expect(screen.queryByRole('button', { name: 'Run your first test' })).not.toBeInTheDocument();
  expect(screen.getByText('No test runs yet')).toBeVisible();
  expect(screen.queryByText('No tests yet')).not.toBeInTheDocument();
  expect(containedNames()).toEqual(['Run a test']);
  fireEvent.click(within(visiblePanel()).getByRole('button', { name: 'Run a test' }));
  const run = await screen.findByRole('button', { name: 'Run application test' });
  expect(run).toHaveClass('MuiButton-contained');
  expect(run).not.toHaveClass('MuiButton-sizeSmall');
  expect(containedNames()).toEqual(['Run application test']);
});

it('hands the primary to the chosen answer form in Run a test', () => {
  h.detail.decision = { state: 'release_ready', reasons: [] };
  h.prepared = { fingerprint: 'sha256:packet', targetDigest: 'sha256:target' };
  const renderForm = vi.fn(() => null);
  renderPanel({ children: renderForm, productionVersion: 4 });
  // On Results the answer form is not the primary.
  expect(renderForm).toHaveBeenLastCalledWith(false);
  showRun();
  fireEvent.click(screen.getByRole('radio', { name: /^Check answers with this context/ }));
  expect(renderForm).toHaveBeenLastCalledWith(true);
  showTab('Results');
  expect(screen.getByRole('button', { name: 'Run on current selection' })).toHaveClass('MuiButton-contained');
});

it('reads human checks only once the run has finished, so the first finished render has no error', () => {
  h.detail = { ...h.detail, status: 'running', jobState: 'running', received: 1 };
  const { rerender } = renderPanel();
  expect(h.annotationIds).not.toContain('previous');
  expect(screen.queryByText("Couldn't load human checks")).not.toBeInTheDocument();
  h.detail = { ...h.detail, status: 'completed', jobState: 'completed', received: 2, result: { repair_verified: true, reproduction: 'reproduced' }, cells: [] };
  rerender(<MemoryRouter><ContextNativePanel contextId="context" versions={versions} viewed={{ number: 7 }} cases={cases} canWrite productionVersion={6} /></MemoryRouter>);
  expect(h.annotationIds).toContain('previous');
  expect(screen.queryByText("Couldn't load human checks")).not.toBeInTheDocument();
  expect(screen.getByText('No human checks yet')).toBeVisible();
});
it('treats a missing review record as no human checks and keeps real failures visible', () => {
  const missing = noReviewsYet({ isError: true, error: { response: { status: 404 } } });
  expect(missing).toMatchObject({ isError: false, data: { results: [] } });
  const failed = noReviewsYet({ isError: true, error: { response: { status: 500 } } });
  expect(failed.isError).toBe(true);
});
it('opens a reproduction handoff with its run form as the only primary', async () => {
  h.runs = [];
  renderPanel({ seed: { caseId: 'refund', reference: 4 } });
  await screen.findByRole('button', { name: 'Run application test' });
  expect(screen.queryByRole('button', { name: 'Run your first test' })).not.toBeInTheDocument();
  expect(containedNames()).toEqual(['Run application test']);
});

it('names the case a version fixes against production and counts untested cases plainly', () => {
  h.runs = [{ id: 'matched', arms: [{ variant: 'candidate', version: 7 }, { variant: 'production', version: 6 }] }];
  const pass = { status: 'completed', answer: 'pass', selection: 'pass', delivery: 'pass', tools: 'pass' };
  h.detail = { id: 'matched', status: 'completed', received: 2, planned: 2, arms: h.runs[0].arms, cases: [{ id: 'refund', title: 'Damaged mug refund' }], result: { reproduction: 'not_requested', regressed_cases: 0 }, decision: { state: 'release_ready', reasons: [] },
    cells: [{ caseId: 'refund', variant: 'candidate', repeatIndex: 0, attemptId: 'a1', ...pass }, { caseId: 'refund', variant: 'production', repeatIndex: 0, attemptId: 'a2', ...pass, tools: 'fail' }] };
  renderPanel();
  expect(screen.getByText('v7 passed 1 of 1 case and fixes Damaged mug refund, which fails on production v6. No regression in the tested case; 2 other cases were not tested.')).toBeVisible();
  expect(screen.getByText(/1 case tested \(2 others not selected\)/)).toBeVisible();
});
it('shows a missing allowance as a calm prerequisite before consent, with the admin action', async () => {
  h.spend = { data: { enabled: false, periodLimitUsd: null } };
  renderPanel();
  showRun();
  const run = await screen.findByRole('button', { name: 'Run application test' });
  expect(screen.getByText('Paid runs need a workspace allowance')).toBeVisible();
  expect(screen.getByRole('link', { name: 'Set allowance' })).toHaveAttribute('href', expect.stringContaining('/skills/allowance'));
  // The spend acceptance can't be used yet, so it is held back with its reason, never ticked.
  const accept = screen.getByRole('checkbox', { name: /I accept up to/ });
  expect(accept).toBeDisabled();
  expect(accept).not.toBeChecked();
  expect(accept).toHaveAccessibleDescription('Set an allowance first');
  fireEvent.click(accept);
  expect(accept).not.toBeChecked();
  expect(run).toBeDisabled();
  expect(run).toHaveAccessibleDescription('Paid runs are off for this workspace.');
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});
it('gives a member a request to copy for an admin instead of a page they cannot change', async () => {
  h.spend = { data: { enabled: false, periodLimitUsd: null } };
  h.access = { canAdmin: false };
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
  try {
    render(<MemoryRouter initialEntries={['/dashboard/skills/contexts/context?tab=tests&version=7']}><ContextNativePanel contextId="context" versions={versions} viewed={{ number: 7 }} cases={cases} canWrite productionVersion={6} /></MemoryRouter>);
    showRun();
    await screen.findByRole('button', { name: 'Run application test' });
    expect(screen.getByText('Ask a workspace admin to set an allowance.')).toBeVisible();
    expect(screen.queryByRole('link', { name: 'Set allowance' })).not.toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: /I accept up to/ })).toBeDisabled();
    const copy = screen.getByRole('button', { name: 'Copy request for admin' });
    expect(copy).toHaveClass('MuiButton-outlined');
    fireEvent.click(copy);
    expect(await screen.findByText('Copied the request for an admin.')).toHaveAttribute('role', 'status');
    const text = writeText.mock.calls[0][0];
    expect(text).toMatch(/^Please set a Skills workspace allowance so I can run an application test \(up to \$1\.5\): http/);
    const url = new URL(text.slice(text.indexOf('http')));
    expect(url.pathname).toBe('/dashboard/skills/allowance');
    expect(url.searchParams.get('returnTo')).toBe('/dashboard/skills/contexts/context?tab=tests&version=7');
  } finally { delete navigator.clipboard; }
});
it('brings the spend acceptance back unticked once an admin sets an allowance', async () => {
  h.spend = { data: { enabled: false, periodLimitUsd: null } };
  const view = renderPanel({ viewed: { number: 7 } });
  showRun();
  const accept = await screen.findByRole('checkbox', { name: /I accept up to/ });
  expect(accept).toBeDisabled();
  h.spend = { data: { enabled: true, periodLimitUsd: '20.00' } };
  view.rerender(<MemoryRouter><ContextNativePanel contextId="context" versions={versions} viewed={{ number: 7 }} cases={cases} canWrite productionVersion={6} /></MemoryRouter>);
  expect(accept).toBeEnabled();
  expect(accept).not.toBeChecked();
  expect(accept).not.toHaveAccessibleDescription('Set an allowance first');
  expect(screen.queryByText('Set an allowance first')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Run application test' })).toBeDisabled();
});
it('defaults to the answer check when no application is registered', () => {
  h.runs = []; h.targets = [];
  renderPanel();
  showRun();
  expect(screen.getByRole('radio', { name: /^Check answers with this context/ })).toBeChecked();
  expect(screen.getByRole('radio', { name: /^Test on the application/ })).not.toBeChecked();
});
it('draws verification rules with the divider colour at every width', () => {
  h.detail.cells = [{ caseId: 'refund', variant: 'candidate', repeatIndex: 0, status: 'completed', attemptId: 'a', answer: 'pass', selection: 'pass', delivery: 'pass', tools: 'pass' }];
  renderPanel();
  const cell = screen.getByRole('table', { name: 'Verification cells' }).querySelector('tbody td');
  expect(getComputedStyle(cell).borderBottomColor).not.toMatch(/^(currentcolor|rgb\(0, 0, 0\))$/i);
});

it('waits for the registered targets before choosing what to test, then keeps the reviewer\'s choice', () => {
  h.runs = []; h.detail = undefined; h.targetsLoading = true;
  const view = renderPanel({ state: 'empty' });
  showRun();
  // No provisional default that could flip under the reviewer while targets load.
  expect(screen.queryByRole('radio', { name: /^Test on the application/ })).not.toBeInTheDocument();
  expect(screen.getByLabelText('Loading test options')).toBeInTheDocument();
  h.targetsLoading = false; h.targets = [];
  view.rerender(<MemoryRouter><ContextNativePanel contextId="context" versions={versions} viewed={{ number: 7 }} cases={cases} canWrite productionVersion={6} state="empty" /></MemoryRouter>);
  // Nothing registered: the answer check is the default, and choosing the application test sticks.
  expect(screen.getByRole('radio', { name: /^Check answers with this context/ })).toBeChecked();
  fireEvent.click(screen.getByRole('radio', { name: /^Test on the application/ }));
  view.rerender(<MemoryRouter><ContextNativePanel contextId="context" versions={versions} viewed={{ number: 7 }} cases={cases} canWrite productionVersion={6} state="empty" /></MemoryRouter>);
  expect(screen.getByRole('radio', { name: /^Test on the application/ })).toBeChecked();
});

// UX iteration 2 (TST1-TST3, TST5), now per visible tab.
it('keeps exactly one contained primary in each tab', async () => {
  h.detail.arms[0] = { variant: 'candidate', version: 7, versionId: 'v7-id', digest: 'sha256:v7' };
  h.detail.decision = { state: 'release_ready', reasons: [] };
  renderPanel();
  // A ready result: the release request is the one primary on Results.
  expect(containedNames()).toEqual(['Request release for v7']);
  // On Run a test the setup's run button is the one primary.
  showRun();
  await screen.findByRole('button', { name: 'Run application test' });
  expect(containedNames()).toEqual(['Run application test']);
  showTab('Results');
  expect(containedNames()).toEqual(['Request release for v7']);
});
it('keeps one primary with no runs at all', async () => {
  h.runs = []; h.detail = undefined;
  renderPanel();
  expect(containedNames()).toEqual(['Run a test']);
  showRun();
  await screen.findByRole('button', { name: 'Run application test' });
  expect(containedNames()).toEqual(['Run application test']);
});
it('drops the visible Latest result heading but keeps the region name', () => {
  renderPanel();
  expect(screen.getByRole('region', { name: 'Latest result' })).toBeInTheDocument();
  expect(screen.queryByText('Latest result')).not.toBeInTheDocument();
  expect(screen.getByRole('heading', { name: 'Application test · v6 vs v4 (was failing)', level: 2 })).toBeVisible();
  expect(screen.queryByRole('heading', { name: /Verify on the application/ })).not.toBeInTheDocument();
});
it('keeps Results, Run a test and Cases in a fixed order in every state, with Results by default', () => {
  const states = [
    () => renderPanel(),
    () => { h.detail.decision = { state: 'release_ready', reasons: [] }; return renderPanel(); },
    () => { h.runs = []; h.detail = undefined; return renderPanel(); },
    () => renderPanel({ state: 'empty', cases: [] }),
    () => renderPanel({ state: 'loading' }),
    () => renderPanel({ state: 'error' }),
    () => renderPanel({ casesState: 'loading', cases: [] }),
  ];
  for (const mount of states) {
    mount();
    expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual(['Results', 'Run a test', 'Cases']);
    expect(screen.getByRole('tab', { name: 'Results' })).toHaveAttribute('aria-selected', 'true');
    cleanup();
  }
});
it('puts the result and History on Results, the setup on Run a test and the cases on Cases', () => {
  h.detail.arms[0] = { variant: 'candidate', version: 7 };
  h.detail.decision = { state: 'release_ready', reasons: [] };
  h.detail.cells = [{ caseId: 'refund', variant: 'candidate', repeatIndex: 0, status: 'completed', attemptId: 'a', answer: 'pass', selection: 'pass', delivery: 'pass', tools: 'pass' }];
  renderPanel({ reviewedCases: <p>Cases table</p> });
  const results = visiblePanel();
  const order = [within(results).getByRole('region', { name: 'Latest result' }), within(results).getByRole('table', { name: 'Verification cells' }), within(results).getByRole('table', { name: 'Test history' })];
  for (let i = 1; i < order.length; i += 1) expect(order[i - 1].compareDocumentPosition(order[i]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(screen.queryByRole('radio', { name: /^Test on the application/ })).not.toBeInTheDocument();
  expect(screen.queryByText('Cases table')).not.toBeVisible();
  showRun();
  expect(within(visiblePanel()).getByRole('radio', { name: /^Test on the application/ })).toBeInTheDocument();
  showTab('Cases');
  expect(within(visiblePanel()).getByText('Cases table')).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Show setup' })).not.toBeInTheDocument();
});
it('describes each History row by comparison, coverage and a separate outcome, and opens it from the row', () => {
  h.detail.decision = { state: 'release_ready', reasons: [] };
  const renderAnswer = vi.fn((id) => <p>Answer result {id}</p>);
  renderPanel({ answerHistory: { data: { results: [{ id: 'answer', status: 'completed', created_at: '2026-11-10T12:00:00Z', candidate: { version: 7 }, reference: { version: 6 }, selected_case_ids: ['one'], decision: { state: 'needs_changes' } }] } }, renderAnswer });
  const history = screen.getByRole('table', { name: 'Test history' });
  const application = within(history).getByRole('row', { name: 'Inspect Application test · v6 vs v4 (was failing) · 2 cases' });
  expect(within(application).getByText('Passed')).toBeVisible();
  // No outlined Inspect button; the row's ⋯ holds it.
  expect(within(application).queryByRole('button', { name: /^Inspect/ })).not.toBeInTheDocument();
  expect(within(application).getByRole('button', { name: 'Actions for Application test · v6 vs v4 (was failing) · 2 cases' })).toBeVisible();
  expect(within(history).queryByText(/Verify on the application|recorded v4/)).not.toBeInTheDocument();
  const answer = within(history).getByRole('row', { name: 'Inspect Answer check · v7 vs v6 · 1 case' });
  expect(within(answer).getByText('Needs changes')).toBeVisible();
  // The precise time is the shared When tooltip on the relative time.
  expect(answer.querySelector('time')).toHaveAttribute('datetime', '2026-11-10T12:00:00.000Z');
  fireEvent.click(within(answer).getByText('Needs changes'));
  expect(renderAnswer).toHaveBeenLastCalledWith('answer', expect.any(Object));
});
it('never shows a verdict for an application row before its detail loads', () => {
  h.detail = { ...h.detail, decision: undefined };
  renderPanel();
  const row = within(screen.getByRole('table', { name: 'Test history' })).getByText(/^Application test ·/).closest('tr');
  expect(within(row).getByText('Loading…')).toBeVisible();
  expect(within(row).queryByText('Passed')).not.toBeInTheDocument();
});
it('states the allowance rule once, under each run choice', () => {
  h.runs = []; h.detail = undefined;
  renderPanel();
  showRun();
  expect(screen.getByText('Compare answers using your instructions and linked skills. No workspace allowance needed.')).toBeVisible();
  expect(screen.getByText('Run approved test cases on your app. Uses your workspace allowance.')).toBeVisible();
  expect(screen.queryByText('Answer checks don’t need a workspace allowance; application verification spends from it.')).not.toBeInTheDocument();
  expect(screen.queryByRole('radio', { name: /^Verify on the application/ })).not.toBeInTheDocument();
});
it('moves only the main scroller and focuses without scrolling when a run starts', async () => {
  const scrollIntoView = vi.fn();
  Element.prototype.scrollIntoView = scrollIntoView;
  const focused = [];
  const descriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'focus');
  const originalFocus = HTMLElement.prototype.focus;
  Object.defineProperty(HTMLElement.prototype, 'focus', { configurable: true, writable: true, value(options) { focused.push([this, options]); return originalFocus.call(this, options); } });
  const scrollTo = vi.fn();
  h.detail.arms[0] = { variant: 'candidate', version: 7 };
  render(<MemoryRouter><main ref={(node) => { if (node) node.scrollTo = scrollTo; }}><ContextNativePanel contextId="context" versions={versions} viewed={{ number: 7 }} cases={cases} canWrite productionVersion={6} /></main></MemoryRouter>);
  showRun();
  const run = await screen.findByRole('button', { name: 'Run application test' });
  fireEvent.click(screen.getByRole('checkbox', { name: /I accept up to/ }));
  await waitFor(() => expect(run).toBeEnabled());
  focused.length = 0;
  fireEvent.click(run);
  await waitFor(() => expect(h.create).toHaveBeenCalledOnce());
  // The started run is the subject: Results opens and its region takes focus.
  await waitFor(() => expect(screen.getByRole('tab', { name: 'Results' })).toHaveAttribute('aria-selected', 'true'));
  const region = screen.getByRole('region', { name: 'Latest result' });
  await waitFor(() => expect(focused.map(([element]) => element)).toContain(region));
  for (const [element, options] of focused) if (element === region) expect(options).toEqual({ preventScroll: true });
  expect(scrollTo).toHaveBeenCalledWith(expect.objectContaining({ behavior: 'smooth' }));
  expect(scrollIntoView).not.toHaveBeenCalled();
  Object.defineProperty(HTMLElement.prototype, 'focus', descriptor);
  delete Element.prototype.scrollIntoView;
});
it('keeps the screen-reader-only plan header 1px wide inside its cell, so it cannot widen the page', async () => {
  h.runs = []; h.detail = undefined;
  renderPanel();
  showRun();
  const label = (await screen.findAllByText('Was failing', { exact: true, selector: 'span' }))[0];
  expect(getComputedStyle(label).width).toBe('1px');
  expect(getComputedStyle(label.closest('th')).position).toBe('relative');
});
it('claims no regression when no attempt finished', () => {
  h.detail = { ...h.detail, status: 'failed', jobState: 'failed', failure: 'runtime_dispatch_failed', received: 0, result: { reproduction: 'not_requested', regressed_cases: 0 }, decision: { state: 'hold', reasons: [] },
    cells: [{ caseId: 'refund', variant: 'candidate', repeatIndex: 0, status: 'not_started', answer: 'unverifiable', selection: 'unverifiable', delivery: 'unverifiable', tools: 'unverifiable' }] };
  renderPanel();
  expect(screen.getByText('v6 passed 0 of 2 cases.')).toBeVisible();
  expect(within(visiblePanel()).queryByText(/regression/i)).not.toBeInTheDocument();
});
it('reads an application row whose detail fails as not reported, never loading forever', () => {
  h.detailError = true;
  try {
    renderPanel({ answerHistory: { data: { results: [] } }, renderAnswer: () => null });
    const row = within(screen.getByRole('table', { name: 'Test history' })).getByText(/^Application test ·/).closest('tr');
    const outcome = row.querySelectorAll('td')[1];
    expect(outcome).toHaveTextContent('Not reported');
    expect(within(row).queryByText('Loading…')).not.toBeInTheDocument();
  } finally { h.detailError = false; }
});

it('confirms a passing result for the version production already serves, with no release request', () => {
  h.detail.decision = { state: 'release_ready', reasons: ['All selected cases passed.'] };
  h.detail.configurationDigest = `sha256:${'c'.repeat(64)}`;
  h.detail.arms[0] = { variant: 'candidate', version: 6, versionId: 'v6-id', digest: 'sha256:v6' };
  renderPanel({ productionVersion: 6 });
  expect(screen.getByText('Already in production')).toBeVisible();
  expect(screen.getByText('v6 is already in production. This result confirms the release.')).toBeVisible();
  expect(screen.queryByText('Ready for a release request')).not.toBeInTheDocument();
  expect(screen.queryByRole('link', { name: /^Request release/ })).not.toBeInTheDocument();
  expect(document.querySelector('a[href*="/review"]')).toBeNull();
  // v7 is newer than the tested v6, so the next step is testing it.
  expect(screen.getByRole('link', { name: 'Test a newer draft' })).toHaveAttribute('href', '/dashboard/skills/contexts/context/edit');
  // No release packet is prepared for a version production already serves.
  expect(screen.queryByRole('group', { name: 'Attach your CI run (optional)', hidden: true })).not.toBeInTheDocument();
});
it('keeps the configuration identity in "How it was decided", and the pinned state on Run a test', async () => {
  h.detail.decision = { state: 'release_ready', reasons: ['All selected cases passed.'] };
  h.detail.configurationDigest = `sha256:${'c'.repeat(64)}`;
  h.detail.arms[0] = { variant: 'candidate', version: 7, versionId: 'v7-id', digest: 'sha256:v7' };
  renderPanel();
  const region = screen.getByRole('region', { name: 'Latest result' });
  const disclosure = within(region).getByRole('button', { name: 'How it was decided' });
  expect(disclosure).toHaveAttribute('aria-expanded', 'false');
  // The configuration identity is not in the status row; it opens with the decision.
  expect(within(region).queryByText(/c{12}/)).not.toBeInTheDocument();
  fireEvent.click(disclosure);
  expect(await within(region).findByText('All selected cases passed.')).toBeVisible();
  expect(within(region).getByText(/c{12}/)).toBeVisible();
  showRun();
  expect(screen.getAllByText('App configuration pinned')).toHaveLength(1);
  expect(screen.getByRole('button', { name: 'Show app configuration' })).toBeVisible();
  expect(screen.queryByText('Review runtime configuration')).not.toBeInTheDocument();
});

// UX iteration 4 (TST1, TST3, TST5, TST6).
it('names the plan columns and cells by what they are, never "Candidate" or "Recorded"', () => {
  h.detail.cells = ['candidate', 'reference'].map((variant) => ({ caseId: 'refund', variant, repeatIndex: 0, status: 'completed', attemptId: variant, answer: 'pass', selection: 'pass', delivery: 'pass', tools: variant === 'reference' ? 'fail' : 'pass' }));
  h.detail.cases[0].title = 'Refund';
  renderPanel();
  const table = screen.getByRole('table', { name: 'Verification cells' });
  expect(within(table).getByRole('row', { name: 'Inspect Refund Was failing on repeat 1' })).toHaveTextContent('Was failing on v4');
  expect(within(table).queryByText(/Candidate|Recorded/)).not.toBeInTheDocument();
  expect(within(table).getByRole('row', { name: 'Inspect Refund Version tested repeat 1' })).toBeVisible();
  // The column is "Human check", never "Human review" (UXF-14).
  const headers = within(table).getAllByRole('columnheader', { hidden: true }).map((cell) => cell.textContent);
  expect(headers).toContain('Human check');
  expect(headers).not.toContain('Human review');
});
it('shows a pass as quiet text and keeps the coloured badge for failures', () => {
  h.detail.cells = [{ caseId: 'refund', variant: 'candidate', repeatIndex: 0, status: 'completed', attemptId: 'a', answer: 'pass', selection: 'pass', delivery: 'pass', tools: 'fail' }];
  renderPanel();
  const table = screen.getByRole('table', { name: 'Verification cells' });
  const passes = within(table).getAllByText('Passed');
  expect(passes).toHaveLength(3);
  // A pass is plain secondary text; a failure keeps the tinted status badge.
  for (const pass of passes) expect(pass).toHaveClass('MuiTypography-s1');
  expect(within(table).getByText('Failed')).not.toHaveClass('MuiTypography-s1');
});
it('links Set allowance back to the blocked test', async () => {
  h.spend = { data: { enabled: false, periodLimitUsd: null } };
  render(<MemoryRouter initialEntries={['/dashboard/skills/contexts/context?tab=tests&version=7']}><ContextNativePanel contextId="context" versions={versions} viewed={{ number: 7 }} cases={cases} canWrite productionVersion={6} /></MemoryRouter>);
  showRun();
  const link = await screen.findByRole('link', { name: 'Set allowance' });
  const url = new URL(link.href);
  expect(url.pathname).toBe('/dashboard/skills/allowance');
  expect(url.searchParams.get('returnTo')).toBe('/dashboard/skills/contexts/context?tab=tests&version=7');
});
describe('exactly one contained button on Results', () => {
  const ready = () => { h.detail.arms[0] = { variant: 'candidate', version: 7, versionId: 'v7-id', digest: 'sha256:v7' }; h.detail.decision = { state: 'release_ready', reasons: [] }; };
  it('no result: Run a test', () => {
    h.runs = []; h.detail = undefined;
    renderPanel();
    expect(containedNames()).toEqual(['Run a test']);
  });
  it('ready result, even after the plan was engaged on Run a test: the release request', async () => {
    ready();
    renderPanel();
    showRun();
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Working chargeback case' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Working chargeback case' }));
    expect(containedNames()).toEqual(['Run application test']);
    showTab('Results');
    expect(containedNames()).toEqual(['Request release for v7']);
  });
  it('earlier partial result: the rerun leads, the banner says what differs and costs, and the release request says what was not tested', () => {
    ready();
    h.detail.cases = [{ id: 'refund', title: 'Original refund failure', historical: true }];
    renderPanel();
    expect(screen.getByText('Earlier result')).toBeVisible();
    expect(screen.getByText('This result is for v7 with 1 case. Your selection adds Working chargeback case (1 more case) · up to $1.5.')).toBeVisible();
    expect(containedNames()).toEqual(['Run on current selection']);
    expect(screen.getByRole('link', { name: 'Request release for v7' })).toHaveClass('MuiButton-outlined');
    expect(screen.getByText('2 of 3 cases not tested')).toBeVisible();
    expect(screen.queryByRole('link', { name: 'Continue to release review' })).not.toBeInTheDocument();
  });
  it('reproduction handoff whose result is ready: the release request, not a paid rerun', () => {
    h.runs = [{ id: 'repro', arms: [{ variant: 'candidate', version: 7 }, { variant: 'reference', version: 4 }] }];
    h.detail = { id: 'repro', status: 'completed', received: 2, planned: 2, arms: [{ variant: 'candidate', version: 7, versionId: 'v7-id', digest: 'sha256:v7' }, { variant: 'reference', version: 4 }],
      cases: [{ id: 'refund', title: 'Original refund failure', historical: true }], decision: { state: 'release_ready', reasons: [] } };
    renderPanel({ seed: { caseId: 'refund', reference: 4 } });
    showTab('Results');
    expect(screen.queryByText('Earlier result')).not.toBeInTheDocument();
    expect(containedNames()).toEqual(['Request release for v7']);
  });
  it('earlier result that failed: the rerun leads', () => {
    h.detail.arms[0] = { variant: 'candidate', version: 7 };
    h.detail.decision = { state: 'hold', reasons: [] };
    h.detail.cases = [{ id: 'refund', title: 'Original refund failure', historical: true }];
    renderPanel();
    expect(containedNames()).toEqual(['Run on current selection']);
  });
  it('earlier result for the version production already serves: no rerun is offered', () => {
    ready();
    h.detail.cases = [{ id: 'refund', title: 'Original refund failure', historical: true }];
    renderPanel({ productionVersion: 7, viewed: { number: 7 } });
    expect(screen.getByText('Earlier result')).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Run on current selection' })).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'View release history' })).toBeVisible();
    expect(containedNames()).toEqual([]);
  });
});

// UX iteration 5 (TST1, TST2, TST5).
describe('iteration 5: one release label, verdicts and plain words', () => {
  const pass = { status: 'completed', answer: 'pass', selection: 'pass', delivery: 'pass', tools: 'pass' };
  const twoArms = () => {
    h.detail.result = { repair_verified: true, reproduction: 'reproduced', regressed_cases: 0 };
    h.detail.decision = { state: 'release_ready', reasons: [] };
    h.detail.cases[0].title = 'Refund';
    h.detail.cells = [{ caseId: 'refund', variant: 'candidate', repeatIndex: 0, attemptId: 'tested', ...pass }, { caseId: 'refund', variant: 'reference', repeatIndex: 0, attemptId: 'baseline', ...pass, tools: 'fail' }];
  };
  const disagree = (attemptId) => ({ id: `annotation-${attemptId}`, attemptId, actorId: 'reviewer', verdict: 'disagree', comment: 'Check the ledger', createdAt: '2026-11-12T12:00:00Z' });
  it('heads the version column "Version", with the cells unchanged', () => {
    twoArms();
    renderPanel();
    const table = screen.getByRole('table', { name: 'Verification cells' });
    // The header row is hidden below sm (each cell carries its own label there), so read it with hidden: true.
    const headers = within(table).getAllByRole('columnheader', { hidden: true }).map((cell) => cell.textContent);
    expect(headers.slice(0, 3)).toEqual(['Test case', 'Version', 'Run']);
    expect(headers).not.toContain('Context');
    expect(within(table).getByRole('row', { name: 'Inspect Refund Was failing on repeat 1' })).toHaveTextContent('Was failing on v4');
  });
  it('says a disagreement on the baseline does not block the tested version', () => {
    twoArms();
    h.annotations = [disagree('baseline')];
    renderPanel();
    expect(screen.getByRole('button', { name: '1 disagreement to inspect' })).toBeVisible();
    expect(screen.getByText("On the v4 baseline; it doesn't block v6.")).toBeVisible();
    expect(screen.queryByText(/before requesting release/)).not.toBeInTheDocument();
  });
  it('asks for a look at a disagreement on the tested version before the release request', () => {
    twoArms();
    h.annotations = [disagree('baseline'), disagree('tested')];
    renderPanel();
    expect(screen.getByRole('button', { name: '2 disagreements to inspect' })).toBeVisible();
    expect(screen.getByText('On v6; check them before requesting release.')).toBeVisible();
    expect(screen.queryByText(/doesn't block/)).not.toBeInTheDocument();
  });
  it('names the release request once, the same as everywhere else', () => {
    twoArms();
    h.detail.arms[0] = { variant: 'candidate', version: 7, versionId: 'v7-id', digest: 'sha256:v7' };
    renderPanel();
    expect(screen.getByRole('link', { name: 'Request release for v7' })).toHaveClass('MuiButton-contained');
    expect(screen.queryByRole('link', { name: 'Request release review' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Continue to release review' })).not.toBeInTheDocument();
    expect(screen.queryByText(/cases? not tested$/)).not.toBeInTheDocument();
  });
  it('records an assessment for one run, and repairs from the failing version', async () => {
    twoArms();
    renderPanel();
    expect(screen.getByRole('button', { name: 'Repair from the failing v4' })).toBeVisible();
    expect(screen.queryByRole('button', { name: /Repair from recorded/ })).not.toBeInTheDocument();
    fireEvent.click(within(cellRow('Inspect Refund Version tested repeat 1')).getAllByRole('cell')[0]);
    expect(screen.getByText('No assessments for this run.')).toBeVisible();
    expect(screen.queryByText(/No human reviews for this attempt/)).not.toBeInTheDocument();
    const record = screen.getByRole('button', { name: 'Record assessment' });
    expect(record).toBeDisabled();
    expect(record).toHaveAccessibleDescription('Waiting for the current evidence revision.');
    expect(screen.queryByRole('button', { name: 'Record review' })).not.toBeInTheDocument();
  });
});

it('says "No tests yet" only when the context has neither test cases nor runs', () => {
  const view = renderPanel({ state: 'empty', cases: [] });
  expect(screen.getByText('No tests yet')).toBeVisible();
  expect(screen.queryByText('No test runs yet')).not.toBeInTheDocument();
  view.unmount();
  renderPanel({ state: 'empty' });
  expect(within(screen.getByRole('region', { name: 'Latest result' })).getByRole('status')).toHaveTextContent('No test runs yetYour 3 saved test cases are in Cases. Run a test to check answers or test the application. Neither changes production.Run a test');
  expect(screen.queryByText('No tests yet')).not.toBeInTheDocument();
});

// UXF-13: the pin dialog speaks about the app, not its build.
describe('pin the application configuration', () => {
  const measured = { code_commit: '8254acd488c074ec30f660c68619e4039f52bb90', entrypoint: 'ctxhub_support.entrypoint:main', runtime_version: 'python3.11', packages: [{}, {}, {}], model: { model_id: 'gpt-4o-mini' }, side_effect_policy: 'fixture_only' };
  it('leads with the plain sentence and a 7-character code version, with the rest under Technical details', () => {
    h.configuration = { data: measured };
    h.targets = [{ bindingId: 'binding', name: 'support-app', revision: 2, qualified: false }];
    renderPanel();
    showRun();
    // An unpinned target shows its pin review in place: no button only opens it.
    expect(screen.queryByRole('button', { name: 'Show app configuration' })).not.toBeInTheDocument();
    const dialog = screen.getByRole('group', { name: 'Pin the application configuration' });
    const sentence = within(dialog).getByTestId('pin-sentence');
    expect(sentence).toHaveTextContent("We'll test your app exactly as it is running now (code version 8254acd, Python 3.11, model gpt-4o-mini). Tests refuse to run if the app changes.");
    // The sentence comes before anything technical, and nothing technical shows until asked.
    expect(within(dialog).queryByText('8254acd488c074ec30f660c68619e4039f52bb90')).not.toBeInTheDocument();
    expect(within(dialog).queryByText('ctxhub_support.entrypoint:main')).not.toBeInTheDocument();
    expect(within(dialog).queryByText(/Source commit/)).not.toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Technical details' }));
    expect(within(dialog).getByText('8254acd488c074ec30f660c68619e4039f52bb90')).toBeVisible();
    expect(within(dialog).getByText('ctxhub_support.entrypoint:main')).toBeVisible();
    expect(within(dialog).getByText('3 pinned')).toBeVisible();
    expect(within(dialog).getByText("Uses test data only; can't change real systems")).toBeVisible();
    expect(within(dialog).queryByText('fixture_only')).not.toBeInTheDocument();
  });
  it('pins exactly the measured configuration for the reviewed target revision', async () => {
    h.configuration = { data: measured };
    h.qualify = vi.fn().mockResolvedValue({});
    h.targets = [{ bindingId: 'binding', name: 'support-app', revision: 2, qualified: false }];
    renderPanel();
    showRun();
    fireEvent.click(screen.getByRole('button', { name: 'Pin this configuration' }));
    await waitFor(() => expect(h.qualify).toHaveBeenCalledOnce());
    expect(h.qualify).toHaveBeenCalledWith({ bindingId: 'binding', revision: 2, configuration: measured });
  });
  it('builds the sentence from camel-case fields too', () => {
    expect(pinSentence({ codeCommit: 'abcdef0123', runtimeVersion: 'node 22', model: { modelId: 'gpt-4o' } })).toBe("We'll test your app exactly as it is running now (code version abcdef0, node 22, model gpt-4o). Tests refuse to run if the app changes.");
  });
});

// A just-published repair is verified next: its hand-off opens Run a test, like a reproduction seed.
it('opens Run a test for a published repair hand-off, and Results otherwise', () => {
  const view = renderPanel({ runHandoff: true });
  expect(screen.getByRole('tab', { name: 'Run a test' })).toHaveAttribute('aria-selected', 'true');
  view.unmount();
  renderPanel();
  expect(screen.getByRole('tab', { name: 'Results' })).toHaveAttribute('aria-selected', 'true');
});

// Recording an assessment is the drawer's task: it closes; a failed record keeps it open with the reason.
it('closes the run drawer after recording an assessment and keeps it open when recording fails', async () => {
  h.detail.result = { repair_verified: true, reproduction: 'reproduced', regressed_cases: 0 };
  h.detail.decision = { state: 'release_ready', reasons: [] };
  h.detail.cases[0].title = 'Refund';
  h.detail.cells = [{ caseId: 'refund', variant: 'candidate', repeatIndex: 0, attemptId: 'tested', status: 'completed', answer: 'pass', selection: 'pass', delivery: 'pass', tools: 'pass' }];
  h.reference = { data: { annotationRevision: 1 } };
  h.append = vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce({});
  renderPanel();
  fireEvent.click(within(cellRow('Inspect Refund Version tested repeat 1')).getAllByRole('cell')[0]);
  fireEvent.click(screen.getByRole('radio', { name: 'Disagree with the automatic outcome' }));
  fireEvent.change(screen.getByLabelText('Why'), { target: { value: 'Declined in the ledger.' } });
  fireEvent.click(screen.getByRole('button', { name: 'Record assessment' }));
  expect(await screen.findByText(/Could not record this assessment/)).toBeVisible();
  expect(screen.getByRole('button', { name: 'Record assessment' })).toBeVisible();
  fireEvent.click(screen.getByRole('radio', { name: 'Disagree with the automatic outcome' }));
  fireEvent.click(screen.getByRole('button', { name: 'Record assessment' }));
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Record assessment' })).not.toBeInTheDocument());
  expect(h.append).toHaveBeenCalledTimes(2);
});
