import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ContextNativePanel from './ContextNativePanel';
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
  useCreateNativeTarget: () => ({ mutate: () => {}, isPending: false }), useQualifyNativeTarget: () => ({ mutateAsync: async () => {} }),
  useRuntimeConfiguration: () => ({ data: undefined }), usePrepareReleasePacket: () => ({ data: h.prepared, mutate: () => {}, isPending: false }),
}));
vi.mock('src/api/skills/evidence-annotations', () => ({ useEvidenceReference: () => ({}), useAllEvidenceAnnotations: (_, id) => { h.annotationIds.push(id ?? null); if (!id) return { isLoading: false, data: undefined }; return h.annotationError ? { isError: true, error: h.annotationError, refetch: () => {} } : { data: { results: h.annotations } }; }, useAppendEvidenceAnnotation: () => ({ mutateAsync: async () => {} }) }));
vi.mock('src/api/skills/context-authoring', () => ({ useContextDetail: () => ({ data: { name: 'Support context' } }) }));
vi.mock('src/api/skills/j6-recovery', () => ({ useRecoveryReviewers: () => ({ data: [{ id: 'reviewer', name: 'Case reviewer' }] }) }));
vi.mock('src/auth/hooks', () => ({ useAuthContext: () => ({ user: { id: 'user' } }) }));
const versions = [7, 6, 4].map((number) => ({ number, id: `v${number}` }));
const cases = [{ id: 'refund', title: 'Original refund failure', reviewed: true }, { id: 'chargeback', title: 'Working chargeback case', reviewed: true }, { id: 'draft', title: 'Unreviewed', reviewed: false }];
const renderPanel = (props = {}) => render(<MemoryRouter><ContextNativePanel contextId="context" versions={versions} viewed={{ number: 7 }} cases={cases} canWrite productionVersion={6} {...props} /></MemoryRouter>);
beforeEach(() => {
  h.annotations = []; h.annotationIds = []; h.annotationError = undefined; h.prepared = undefined; h.spend = {}; h.access = { canAdmin: true }; h.targets = undefined; h.targetsLoading = false;
  h.preview = { data: undefined, plans: [] }; h.create = vi.fn().mockResolvedValue('evidence');
  h.runs = [{ id: 'previous', arms: [{ variant: 'candidate', version: 6 }, { variant: 'reference', version: 4 }] }];
  h.detail = { id: 'previous', status: 'completed', executionFingerprint: 'sha256:previous', received: 2, planned: 2, arms: h.runs[0].arms, cases: [{ id: 'refund', historical: true }, { id: 'chargeback', historical: false }] };
});
afterEach(cleanup);

it('plans the matched run from stored data and starts it only after the reviewer accepts the paid ceiling', async () => {
  renderPanel();
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
  await screen.findByRole('button', { name: 'Run application test' });
  expect(h.preview.plans.at(-1)).toMatchObject({ candidateVersion: 6, baselines: { reference: 4 }, caseIds: ['refund'], historicalCaseIds: ['refund'] });
  expect(h.preview.plans.at(-1).baselines.production).toBeUndefined();
  expect(screen.getByRole('checkbox', { name: 'Working chargeback case' })).not.toBeChecked();
});
it('re-plans after the reviewer deselects an example and refuses a stale plan', async () => {
  renderPanel();
  await screen.findByRole('button', { name: 'Run application test' });
  fireEvent.click(screen.getByRole('checkbox', { name: 'Working chargeback case' }));
  await waitFor(() => expect(h.preview.plans.at(-1).caseIds).toEqual(['refund']));
});
it('plans nothing without a recorded reference or production baseline', async () => {
  h.runs = [];
  renderPanel({ productionVersion: undefined });
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
it('opens an attempt with the keyboard, shows disagreement scope and returns focus on Close', async () => {
  const user = userEvent.setup();
  h.detail.cells = [{ caseId: 'refund', variant: 'candidate', repeatIndex: 0, attemptId: 'attempt', answer: 'pass', selection: 'pass', delivery: 'pass', tools: 'pass' }];
  h.detail.cases[0].title = 'Refund';
  h.annotations = [{ id: 'annotation', attemptId: 'attempt', actorId: 'reviewer', verdict: 'disagree', comment: 'Wrong business action', createdAt: '2026-11-12T12:00:00Z' }];
  renderPanel();
  const inspect = screen.getByRole('button', { name: 'Inspect Refund Version tested repeat 1' });
  inspect.focus();
  await user.keyboard('{Enter}');
  expect(screen.getByRole('heading', { name: 'Refund', level: 2 })).toBeVisible();
  // A review is recorded against this one attempt, and says so.
  expect(screen.getByText('Applies to this attempt: version tested v6 · Refund · run 1')).toBeVisible();
  expect(screen.queryByText(/whole run/)).not.toBeInTheDocument();
  expect(screen.getByText('Version tested v6 · Run 1 of 1')).toBeVisible();
  expect(screen.getByText("Your review is added next to the automatic result; it doesn't change it.")).toBeVisible();
  expect(screen.getByText('Disagrees')).toBeVisible();
  expect(screen.getByText('Wrong business action')).toBeVisible();
  expect(screen.getByText('Case reviewer')).toBeVisible();
  expect(screen.getByText('1 disagreement below')).toBeVisible();
  await user.click(screen.getByRole('button', { name: 'Close', exact: true }));
  await waitFor(() => expect(inspect).toHaveFocus());
});
it('continues to the composer with the complete candidate, target and shared run identity', () => {
  h.detail.decision = { state: 'release_ready', reasons: [] };
  h.detail.arms[0] = { variant: 'candidate', version: 7, versionId: 'candidate-id', digest: 'sha256:full-digest' };
  h.prepared = { fingerprint: 'sha256:packet', targetDigest: 'sha256:target' };
  renderPanel();
  const url = new URL(screen.getByRole('link', { name: 'Continue to release review' }).href);
  expect(Object.fromEntries(url.searchParams)).toMatchObject({ kind: 'agent_context', subject: 'context', name: 'Support context', candidate: 'candidate-id', number: '7', digest: 'sha256:full-digest', evidence: 'simulation_native:previous', target: 'binding', label: 'production' });
  expect(url.searchParams.has('annotation_revision')).toBe(false);
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
  expect(screen.getByRole('button', { name: 'Attach your CI run (optional)' })).toHaveAttribute('aria-expanded', 'true');
  expect(screen.getByText('Fill in the three CI fields to attach your CI run.')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Share run and attach CI result' })).toBeDisabled();
  expect(screen.getByText('This applies to the earlier run, not your current selection.')).toBeVisible();
  // The earlier result passed for a releasable version, so its release review stays the primary and the
  // paid rerun steps back.
  expect(screen.getByRole('link', { name: 'Continue to release review' })).toHaveClass('MuiButton-contained');
  expect(screen.getByRole('button', { name: 'Run on current selection' })).toHaveClass('MuiButton-outlined');
  expect(screen.getByRole('button', { name: 'Run application test' })).toHaveClass('MuiButton-outlined');
});
it('reconciles preparing plus returned work without announcing the result grid', () => {
  h.detail.jobState = 'provisioning';
  h.detail.received = 1;
  h.detail.decision = { state: 'release_ready', reasons: [] };
  h.detail.cells = [{ caseId: 'refund', variant: 'candidate', repeatIndex: 0, status: 'running' }];
  renderPanel();
  expect(screen.getByRole('status')).toHaveTextContent('1 of 2 runs finished');
  expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '50');
  expect(screen.getByRole('region', { name: 'Latest result' })).not.toHaveAttribute('aria-live');
  expect(screen.queryByText('Ready for release review')).not.toBeInTheDocument();
  expect(screen.getAllByText('Pending')).toHaveLength(4);
});

it('continues to release review without CI and keeps the CI run an optional, foldable attachment', () => {
  h.detail.arms[0] = { variant: 'candidate', version: 7, versionId: 'v7-id', digest: 'sha256:v7' };
  h.detail.decision = { state: 'release_ready', reasons: [] };
  renderPanel();
  const proceed = screen.getByRole('link', { name: 'Continue to release review' });
  expect(proceed).toHaveClass('MuiButton-contained');
  expect(new URL(proceed.href).searchParams.get('evidence')).toBe('simulation_native:previous');
  expect(screen.getByText('Ready for release review')).toBeVisible();
  // Open beside the result so attaching CI costs no extra step; the author can fold it away.
  const disclosure = screen.getByRole('button', { name: 'Attach your CI run (optional)' });
  expect(disclosure).toHaveAttribute('aria-expanded', 'true');
  for (const label of ['CI run link', 'CI pipeline name', 'CI result (pass or hold)']) expect(screen.getByRole('textbox', { name: label })).toBeVisible();
  expect(screen.queryByRole('textbox', { name: 'CI configuration ID' })).not.toBeInTheDocument();
  fireEvent.click(disclosure);
  expect(disclosure).toHaveAttribute('aria-expanded', 'false');
  expect(screen.getByRole('textbox', { name: 'CI run link', hidden: true })).not.toBeVisible();
  fireEvent.click(disclosure);
  expect(screen.getByRole('button', { name: 'Share run and attach CI result' })).toHaveClass('MuiButton-outlined');
  // Opening the release review never shares or prepares anything by itself.
  expect(screen.queryByText(/Packet ready/)).not.toBeInTheDocument();
});

it('explains an automatically omitted production comparison', () => {
  renderPanel({ viewed: { number: 6 } });
  // The current result folds the setup; the reviewer opens it to see the plan.
  fireEvent.click(screen.getByRole('button', { name: 'Show setup' }));
  expect(screen.getByRole('combobox', { name: /^Compare with/ })).toHaveTextContent('No comparison');
  expect(screen.getByText('v6 is production, so it runs once as the version tested.')).toBeVisible();
});

it('keeps the latest retained result visible when application options fail', () => {
  h.detail.arms[0] = { variant: 'candidate', version: 7 };
  renderPanel({ state: 'error' });
  expect(screen.getByRole('region', { name: 'Latest result' })).toHaveTextContent('2 of 2 runs finished');
  fireEvent.click(screen.getByRole('button', { name: 'Show setup' }));
  expect(screen.getByText('You can still open past results and reviewed cases.')).toBeVisible();
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
  fireEvent.click(screen.getByRole('button', { name: '1 disagreement to inspect' }));
  expect(screen.getByRole('heading', { name: 'Refund' })).toBeVisible();
  expect(screen.getByText('Inspect receipt')).toBeVisible();
});

it('does not infer a verified repair or release readiness from a passing answer', () => {
  h.detail.result = { repair_verified: false, reproduction: 'not_reproduced', regressed_cases: 1 };
  h.detail.decision = { state: 'hold', reasons: ['Business action failed'] };
  h.detail.cells = [{ caseId: 'refund', variant: 'candidate', repeatIndex: 0, status: 'completed', answer: 'pass', tools: 'fail', selection: 'pass', delivery: 'pass' }];
  renderPanel();
  expect(screen.getByText('Repair not verified: v6 passed 0 of 2 cases; the recorded failure did not reproduce. 1 case regressed; 1 other case was not tested.')).toBeVisible();
  expect(screen.queryByText('Ready for release review')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Share run and attach CI result' })).not.toBeInTheDocument();
});

it('makes the open setup the one primary in the empty preview, with no empty-state button', async () => {
  renderPanel({ state: 'empty' });
  expect(screen.queryByRole('button', { name: 'Run your first test' })).not.toBeInTheDocument();
  expect(screen.getByText('No tests yet')).toBeVisible();
  const run = await screen.findByRole('button', { name: 'Run application test' });
  expect(run).toHaveClass('MuiButton-contained');
  expect(run).not.toHaveClass('MuiButton-sizeSmall');
});

it('hands the primary to the chosen answer form when the packet belongs to an earlier selection', () => {
  h.detail.decision = { state: 'release_ready', reasons: [] };
  h.prepared = { fingerprint: 'sha256:packet', targetDigest: 'sha256:target' };
  const renderForm = vi.fn(() => null);
  // The earlier result tested v6 while production is v4, so it still offers the release review.
  renderPanel({ children: renderForm, productionVersion: 4 });
  fireEvent.click(screen.getByRole('radio', { name: /^Check answers with this context/ }));
  expect(renderForm).toHaveBeenLastCalledWith(true, expect.any(Function));
  expect(screen.getByRole('button', { name: 'Run on current selection' })).toHaveClass('MuiButton-outlined');
  expect(screen.getByRole('link', { name: 'Continue to release review' })).toHaveClass('MuiButton-outlined');
});

it('reads human reviews only once the run has finished, so the first finished render has no error', () => {
  h.detail = { ...h.detail, status: 'running', jobState: 'running', received: 1 };
  const { rerender } = renderPanel();
  expect(h.annotationIds).not.toContain('previous');
  expect(screen.queryByText("Couldn't load human reviews")).not.toBeInTheDocument();
  h.detail = { ...h.detail, status: 'completed', jobState: 'completed', received: 2, result: { repair_verified: true, reproduction: 'reproduced' }, cells: [] };
  rerender(<MemoryRouter><ContextNativePanel contextId="context" versions={versions} viewed={{ number: 7 }} cases={cases} canWrite productionVersion={6} /></MemoryRouter>);
  expect(h.annotationIds).toContain('previous');
  expect(screen.queryByText("Couldn't load human reviews")).not.toBeInTheDocument();
  expect(screen.getByText('No human reviews yet')).toBeVisible();
});
it('treats a missing review record as no human reviews and keeps real failures visible', () => {
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
  const run = await screen.findByRole('button', { name: 'Run application test' });
  expect(screen.getByText('Paid runs need a workspace allowance')).toBeVisible();
  expect(screen.getByRole('link', { name: 'Set allowance' })).toHaveAttribute('href', expect.stringContaining('/skills/allowance'));
  fireEvent.click(screen.getByRole('checkbox', { name: /I accept up to/ }));
  expect(run).toBeDisabled();
  expect(run).toHaveAccessibleDescription('Paid runs are off for this workspace.');
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});
it('tells a member who can set the allowance instead of sending them to a page they cannot change', async () => {
  h.spend = { data: { enabled: false, periodLimitUsd: null } };
  h.access = { canAdmin: false };
  renderPanel();
  await screen.findByRole('button', { name: 'Run application test' });
  expect(screen.getByText('Ask a workspace admin to set one.')).toBeVisible();
  expect(screen.queryByRole('link', { name: 'Set allowance' })).not.toBeInTheDocument();
});
it('defaults to the answer check when no application is registered', () => {
  h.runs = []; h.targets = [];
  renderPanel();
  expect(screen.getByRole('radio', { name: /^Check answers with this context/ })).toBeChecked();
  expect(screen.getByRole('radio', { name: /^Test on the application/ })).not.toBeChecked();
});
it('folds the run setup under a current result and opens it on request', () => {
  h.detail.arms[0] = { variant: 'candidate', version: 7 };
  renderPanel();
  const toggle = screen.getByRole('button', { name: 'Show setup' });
  expect(toggle).toHaveAttribute('aria-expanded', 'false');
  fireEvent.click(toggle);
  expect(screen.getByRole('button', { name: 'Hide setup' })).toHaveAttribute('aria-expanded', 'true');
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

// UX iteration 2 (TST1-TST3, TST5).
const containedNames = () => [...document.querySelectorAll('.MuiButton-contained')].filter((el) => !el.closest('[hidden]') && !el.closest('.MuiCollapse-hidden')).map((el) => el.textContent);
it('keeps exactly one contained primary as the reviewer moves between result and setup', async () => {
  h.detail.arms[0] = { variant: 'candidate', version: 7, versionId: 'v7-id', digest: 'sha256:v7' };
  h.detail.decision = { state: 'release_ready', reasons: [] };
  renderPanel();
  // A ready result with the setup folded: the release review is the one primary.
  expect(containedNames()).toEqual(['Continue to release review']);
  // Opening the setup alone does not move the primary: the ready result keeps it.
  fireEvent.click(screen.getByRole('button', { name: 'Show setup' }));
  const run = await screen.findByRole('button', { name: 'Run application test' });
  expect(containedNames()).toEqual(['Continue to release review']);
  // Accepting the spend is working in the plan: its run button leads and the release review steps back.
  fireEvent.click(screen.getByRole('checkbox', { name: /I accept up to/ }));
  expect(run).toHaveClass('MuiButton-contained');
  expect(screen.getByRole('link', { name: 'Continue to release review' })).toHaveClass('MuiButton-outlined');
  expect(containedNames()).toEqual(['Run application test']);
});
it('keeps one primary with no runs at all', async () => {
  h.runs = []; h.detail = undefined;
  renderPanel();
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
it('puts the per-case table before the CI attachment, and both before History and the setup', () => {
  h.detail.arms[0] = { variant: 'candidate', version: 7 };
  h.detail.decision = { state: 'release_ready', reasons: [] };
  h.detail.cells = [{ caseId: 'refund', variant: 'candidate', repeatIndex: 0, status: 'completed', attemptId: 'a', answer: 'pass', selection: 'pass', delivery: 'pass', tools: 'pass' }];
  renderPanel();
  const order = [screen.getByRole('table', { name: 'Verification cells' }), screen.getByRole('button', { name: 'Attach your CI run (optional)' }), screen.getByRole('table', { name: 'Test history' }), screen.getByRole('heading', { name: 'Run a test' })];
  for (let i = 1; i < order.length; i += 1) expect(order[i - 1].compareDocumentPosition(order[i]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Attach your CI run (optional)' })).toHaveAttribute('aria-expanded', 'true');
});
it('describes each History row by comparison, coverage and a separate outcome', () => {
  h.detail.decision = { state: 'release_ready', reasons: [] };
  renderPanel({ answerHistory: { data: { results: [{ id: 'answer', status: 'completed', created_at: '2026-11-10T12:00:00Z', candidate: { version: 7 }, reference: { version: 6 }, selected_case_ids: ['one'], decision: { state: 'needs_changes' } }] } }, renderAnswer: (id) => <p>Answer result {id}</p> });
  const history = screen.getByRole('table', { name: 'Test history' });
  const application = within(history).getByText('Application test · v6 vs v4 (was failing) · 2 cases').closest('tr');
  expect(within(application).getByText('Passed')).toBeVisible();
  expect(within(application).getByRole('button', { name: 'Inspect Application test · v6 vs v4 (was failing) · 2 cases' })).toBeVisible();
  expect(within(history).queryByText(/Verify on the application|recorded v4/)).not.toBeInTheDocument();
  const answer = within(history).getByText('Answer check · v7 vs v6 · 1 case').closest('tr');
  expect(within(answer).getByText('Needs changes')).toBeVisible();
  // The precise time is the shared When tooltip on the relative time.
  expect(answer.querySelector('time')).toHaveAttribute('datetime', '2026-11-10T12:00:00.000Z');
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
  expect(screen.getByText('Compare answers using your instructions and linked skills. No workspace allowance needed.')).toBeVisible();
  expect(screen.getByText('Run reviewed cases on your app. Uses your workspace allowance.')).toBeVisible();
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
  fireEvent.click(screen.getByRole('button', { name: 'Show setup' }));
  const run = await screen.findByRole('button', { name: 'Run application test' });
  fireEvent.click(screen.getByRole('checkbox', { name: /I accept up to/ }));
  await waitFor(() => expect(run).toBeEnabled());
  focused.length = 0;
  fireEvent.click(run);
  await waitFor(() => expect(h.create).toHaveBeenCalledOnce());
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
  const label = (await screen.findAllByText('Was failing', { exact: true, selector: 'span' }))[0];
  expect(getComputedStyle(label).width).toBe('1px');
  expect(getComputedStyle(label.closest('th')).position).toBe('relative');
});
it('claims no regression when no attempt finished', () => {
  h.detail = { ...h.detail, status: 'failed', jobState: 'failed', failure: 'runtime_dispatch_failed', received: 0, result: { reproduction: 'not_requested', regressed_cases: 0 }, decision: { state: 'hold', reasons: [] },
    cells: [{ caseId: 'refund', variant: 'candidate', repeatIndex: 0, status: 'not_started', answer: 'unverifiable', selection: 'unverifiable', delivery: 'unverifiable', tools: 'unverifiable' }] };
  renderPanel();
  expect(screen.getByText('v6 passed 0 of 2 cases.')).toBeVisible();
  expect(screen.queryByText(/regression/i)).not.toBeInTheDocument();
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
  expect(screen.queryByText('Ready for release review')).not.toBeInTheDocument();
  expect(screen.queryByRole('link', { name: 'Continue to release review' })).not.toBeInTheDocument();
  expect(document.querySelector('a[href*="/review"]')).toBeNull();
  // v7 is newer than the tested v6, so the next step is testing it.
  expect(screen.getByRole('link', { name: 'Test a newer draft' })).toHaveAttribute('href', '/dashboard/skills/contexts/context/edit');
  // No release packet is prepared for a version production already serves.
  expect(screen.queryByRole('button', { name: 'Attach your CI run (optional)' })).not.toBeInTheDocument();
});
it('keeps the configuration identity in "How it was decided", and the pinned state visible', async () => {
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
  expect(screen.getAllByText('App configuration pinned')).toHaveLength(1);
  // The folded setup's target row: an accessible name is not computed inside a hidden subtree, so match the text.
  expect(screen.getByText('Show app configuration').closest('button')).not.toBeNull();
  expect(screen.queryByText('Review runtime configuration')).not.toBeInTheDocument();
});

// UX iteration 4 (TST1, TST3, TST5, TST6).
it('names the plan columns and cells by what they are, never "Candidate" or "Recorded"', () => {
  h.detail.cells = ['candidate', 'reference'].map((variant) => ({ caseId: 'refund', variant, repeatIndex: 0, status: 'completed', attemptId: variant, answer: 'pass', selection: 'pass', delivery: 'pass', tools: variant === 'reference' ? 'fail' : 'pass' }));
  h.detail.cases[0].title = 'Refund';
  renderPanel();
  const table = screen.getByRole('table', { name: 'Verification cells' });
  expect(within(table).getByRole('row', { name: /Refund.*Was failing on v4/ })).toBeVisible();
  expect(within(table).queryByText(/Candidate|Recorded/)).not.toBeInTheDocument();
  expect(within(table).getByRole('button', { name: 'Inspect Refund Version tested repeat 1' })).toBeVisible();
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
  // v7 differs from the earlier v6 result, so the setup is already open.
  const link = await screen.findByRole('link', { name: 'Set allowance' });
  const url = new URL(link.href);
  expect(url.pathname).toBe('/dashboard/skills/allowance');
  expect(url.searchParams.get('returnTo')).toBe('/dashboard/skills/contexts/context?tab=tests&version=7');
});
describe('exactly one contained button', () => {
  const ready = () => { h.detail.arms[0] = { variant: 'candidate', version: 7, versionId: 'v7-id', digest: 'sha256:v7' }; h.detail.decision = { state: 'release_ready', reasons: [] }; };
  it('plan open and untouched, with no result: the run button', async () => {
    h.runs = []; h.detail = undefined;
    renderPanel();
    await screen.findByRole('button', { name: 'Run application test' });
    expect(containedNames()).toEqual(['Run application test']);
  });
  it('plan engaged after a ready result: the run button', async () => {
    ready();
    renderPanel();
    fireEvent.click(screen.getByRole('button', { name: 'Show setup' }));
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Working chargeback case' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Working chargeback case' }));
    expect(containedNames()).toEqual(['Run application test']);
  });
  it('ready result, setup open but untouched: Continue to release review', async () => {
    ready();
    renderPanel();
    fireEvent.click(screen.getByRole('button', { name: 'Show setup' }));
    await screen.findByRole('button', { name: 'Run application test' });
    expect(containedNames()).toEqual(['Continue to release review']);
  });
  it('earlier result with a different selection: the result leads and the banner says what differs and costs', () => {
    ready();
    h.detail.cases = [{ id: 'refund', title: 'Original refund failure', historical: true }];
    renderPanel();
    expect(screen.getByText('Earlier result')).toBeVisible();
    expect(screen.getByText('This result is for v7 with 1 case. Your selection adds Working chargeback case (1 more case) · up to $1.5.')).toBeVisible();
    expect(containedNames()).toEqual(['Continue to release review']);
    expect(screen.getByRole('button', { name: 'Run on current selection' })).toHaveClass('MuiButton-outlined');
  });
  it('reproduction handoff whose result is ready: Continue to release review, not a paid rerun', () => {
    h.runs = [{ id: 'repro', arms: [{ variant: 'candidate', version: 7 }, { variant: 'reference', version: 4 }] }];
    h.detail = { id: 'repro', status: 'completed', received: 2, planned: 2, arms: [{ variant: 'candidate', version: 7, versionId: 'v7-id', digest: 'sha256:v7' }, { variant: 'reference', version: 4 }],
      cases: [{ id: 'refund', title: 'Original refund failure', historical: true }], decision: { state: 'release_ready', reasons: [] } };
    renderPanel({ seed: { caseId: 'refund', reference: 4 } });
    expect(screen.queryByText('Earlier result')).not.toBeInTheDocument();
    expect(containedNames()).toEqual(['Continue to release review']);
  });
  it('reproduction handoff over an earlier, different result: the run button', async () => {
    ready();
    renderPanel({ seed: { caseId: 'refund', reference: 4 } });
    await screen.findByRole('button', { name: 'Run application test' });
    expect(screen.getByText('Earlier result')).toBeVisible();
    expect(containedNames()).toEqual(['Run application test']);
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
    expect(containedNames()).toEqual(['Run application test']);
  });
});
