import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import ContextNativePanel from './ContextNativePanel';
import { noReviewsYet } from '../utils/conclusions';

const h = vi.hoisted(() => ({ preview: { data: undefined, plans: [] }, create: vi.fn(), runs: [], detail: undefined, annotations: [], annotationIds: [], annotationError: undefined, prepared: undefined, spend: {}, access: { canAdmin: true }, targets: undefined }));
vi.mock('src/api/skills/r3-spend', () => ({ useSkillsSpendPolicy: () => h.spend }));
vi.mock('../tests/sharedSeams', async (original) => ({ ...(await original()), useSkillsAccess: () => h.access }));
vi.mock('src/api/skills/context-testing', () => ({
  useNativeTargets: () => ({ data: { runtime: { available: true, runtime: 'container', evidenceClass: 'synthetic fixture' }, targets: h.targets ?? [{ bindingId: 'binding', name: 'support-app', revision: 2, qualified: true, configurationDigest: 'sha256:abc' }] } }),
  useNativeRuns: () => ({ data: h.runs }),
  useNativeRun: (_, id) => ({ data: id ? h.detail : undefined, isLoading: false }),
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
  h.annotations = []; h.annotationIds = []; h.annotationError = undefined; h.prepared = undefined; h.spend = {}; h.access = { canAdmin: true }; h.targets = undefined;
  h.preview = { data: undefined, plans: [] }; h.create = vi.fn().mockResolvedValue('evidence');
  h.runs = [{ id: 'previous', arms: [{ variant: 'candidate', version: 6 }, { variant: 'reference', version: 4 }] }];
  h.detail = { id: 'previous', status: 'completed', executionFingerprint: 'sha256:previous', received: 2, planned: 2, arms: h.runs[0].arms, cases: [{ id: 'refund', historical: true }, { id: 'chargeback', historical: false }] };
});
afterEach(cleanup);

it('plans the matched run from stored data and starts it only after the reviewer accepts the paid ceiling', async () => {
  renderPanel();
  const button = await screen.findByRole('button', { name: 'Run on the application' });
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
  await screen.findByRole('button', { name: 'Run on the application' });
  expect(h.preview.plans.at(-1)).toMatchObject({ candidateVersion: 6, baselines: { reference: 4 }, caseIds: ['refund'], historicalCaseIds: ['refund'] });
  expect(h.preview.plans.at(-1).baselines.production).toBeUndefined();
  expect(screen.getByRole('checkbox', { name: 'Working chargeback case' })).not.toBeChecked();
});
it('re-plans after the reviewer deselects an example and refuses a stale plan', async () => {
  renderPanel();
  await screen.findByRole('button', { name: 'Run on the application' });
  fireEvent.click(screen.getByRole('checkbox', { name: 'Working chargeback case' }));
  await waitFor(() => expect(h.preview.plans.at(-1).caseIds).toEqual(['refund']));
});
it('plans nothing without a recorded reference or production baseline', async () => {
  h.runs = [];
  renderPanel({ productionVersion: undefined });
  expect((await screen.findAllByText('Was failing on')).length).toBeGreaterThan(0);
  expect(h.preview.plans).toHaveLength(0);
  expect(screen.queryByRole('button', { name: 'Run on the application' })).not.toBeInTheDocument();
});

it('uses one baseline when recorded reference is also production and retains the fingerprint gate', async () => {
  h.runs = [];
  renderPanel({ seed: { caseId: 'refund', reference: 6 } });
  const button = await screen.findByRole('button', { name: 'Run on the application' });
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
  const inspect = screen.getByRole('button', { name: 'Inspect Refund Candidate repeat 1' });
  inspect.focus();
  await user.keyboard('{Enter}');
  expect(screen.getByRole('heading', { name: 'Refund', level: 2 })).toBeVisible();
  expect(screen.getByText(/Applies to this whole run/)).toBeVisible();
  expect(screen.getByText('Candidate v6 · Run 1 of 1')).toBeVisible();
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
  h.detail.arms[0] = { variant: 'candidate', version: 6, versionId: 'candidate-id', digest: 'sha256:full-digest' };
  h.prepared = { fingerprint: 'sha256:packet', targetDigest: 'sha256:target' };
  renderPanel();
  const url = new URL(screen.getByRole('link', { name: 'Continue to release review' }).href);
  expect(Object.fromEntries(url.searchParams)).toMatchObject({ kind: 'agent_context', subject: 'context', name: 'Support context', candidate: 'candidate-id', number: '6', digest: 'sha256:full-digest', evidence: 'simulation_native:previous', target: 'binding', label: 'production' });
  expect(url.searchParams.has('annotation_revision')).toBe(false);
  expect(screen.getByText(/Packet ready/)).toBeVisible();
});

it('chooses the newest result across both run types and keeps a combined history', () => {
  h.runs[0].createdAt = '2026-11-11T12:00:00Z';
  renderPanel({ answerHistory: { data: { results: [{ id: 'answer', status: 'completed', created_at: '2026-11-12T12:00:00Z', candidate: { version: 7 } }] } }, renderAnswer: (id) => <p>Answer result {id}</p> });
  expect(screen.getByText('Answer result answer')).toBeVisible();
  expect(screen.getByRole('table', { name: 'Test history' })).toHaveTextContent('Answer check');
  expect(screen.getByRole('table', { name: 'Test history' })).toHaveTextContent('Application verification');
});
it('explains disabled packet prerequisites and marks the earlier-run scope', () => {
  h.detail.decision = { state: 'release_ready', reasons: [] };
  renderPanel();
  fireEvent.click(screen.getByRole('button', { name: 'Attach your CI run (optional)' }));
  expect(screen.getByText('Fill in the three CI fields to attach your CI run.')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Share evidence and prepare packet' })).toBeDisabled();
  expect(screen.getByText('This applies to the earlier run, not your current selection.')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Run on current selection' })).toHaveClass('MuiButton-contained');
  expect(screen.getByRole('button', { name: 'Run on the application' })).toHaveClass('MuiButton-outlined');
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

it('continues to release review without CI and keeps the CI run an optional, collapsed attachment', () => {
  h.detail.arms[0] = { variant: 'candidate', version: 7, versionId: 'v7-id', digest: 'sha256:v7' };
  h.detail.decision = { state: 'release_ready', reasons: [] };
  renderPanel();
  const proceed = screen.getByRole('link', { name: 'Continue to release review' });
  expect(proceed).toHaveClass('MuiButton-contained');
  expect(new URL(proceed.href).searchParams.get('evidence')).toBe('simulation_native:previous');
  expect(screen.getByText('Ready for release review')).toBeVisible();
  expect(screen.queryByRole('textbox', { name: 'CI run link' })).not.toBeInTheDocument();
  const disclosure = screen.getByRole('button', { name: 'Attach your CI run (optional)' });
  expect(disclosure).toHaveAttribute('aria-expanded', 'false');
  fireEvent.click(disclosure);
  for (const label of ['CI run link', 'CI configuration ID', 'CI result (pass or hold)']) expect(screen.getByRole('textbox', { name: label })).toBeVisible();
  expect(screen.getByRole('button', { name: 'Share evidence and prepare packet' })).toHaveClass('MuiButton-outlined');
  // Opening the release review never shares or prepares anything by itself.
  expect(screen.queryByText(/Packet ready/)).not.toBeInTheDocument();
});

it('explains an automatically omitted production comparison', () => {
  renderPanel({ viewed: { number: 6 } });
  // The current result folds the setup; the reviewer opens it to see the plan.
  fireEvent.click(screen.getByRole('button', { name: 'Show setup' }));
  expect(screen.getByRole('combobox', { name: /^Compare with/ })).toHaveTextContent('No comparison');
  expect(screen.getByText('v6 is production, so it runs once as the candidate.')).toBeVisible();
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
  expect(screen.getByText('Repair verified: v6 passed all 2 cases; the recorded failure reproduced on v4. No regressions.')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: '1 disagreement to inspect' }));
  expect(screen.getByRole('heading', { name: 'Refund' })).toBeVisible();
  expect(screen.getByText('Inspect receipt')).toBeVisible();
});

it('does not infer a verified repair or release readiness from a passing answer', () => {
  h.detail.result = { repair_verified: false, reproduction: 'not_reproduced', regressed_cases: 1 };
  h.detail.decision = { state: 'hold', reasons: ['Business action failed'] };
  h.detail.cells = [{ caseId: 'refund', variant: 'candidate', repeatIndex: 0, status: 'completed', answer: 'pass', tools: 'fail', selection: 'pass', delivery: 'pass' }];
  renderPanel();
  expect(screen.getByText('Repair not verified: v6 passed 0 of 2 cases; the recorded failure did not reproduce. 1 case regressed.')).toBeVisible();
  expect(screen.queryByText('Ready for release review')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Share evidence and prepare packet' })).not.toBeInTheDocument();
});

it('offers the first-run primary in the real-route empty preview even with retained history', () => {
  renderPanel({ state: 'empty' });
  expect(screen.getByRole('button', { name: 'Run your first test' })).toHaveClass('MuiButton-contained');
});

it('hands the primary to the chosen answer form when the packet belongs to an earlier selection', () => {
  h.detail.decision = { state: 'release_ready', reasons: [] };
  h.prepared = { fingerprint: 'sha256:packet', targetDigest: 'sha256:target' };
  const renderForm = vi.fn(() => null);
  renderPanel({ children: renderForm });
  fireEvent.click(screen.getByRole('radio', { name: /^Check answers with this context/ }));
  expect(renderForm).toHaveBeenLastCalledWith(true);
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
  await screen.findByRole('button', { name: 'Run on the application' });
  expect(screen.queryByRole('button', { name: 'Run your first test' })).not.toBeInTheDocument();
});

it('names the case a version fixes against production and counts untested cases plainly', () => {
  h.runs = [{ id: 'matched', arms: [{ variant: 'candidate', version: 7 }, { variant: 'production', version: 6 }] }];
  const pass = { status: 'completed', answer: 'pass', selection: 'pass', delivery: 'pass', tools: 'pass' };
  h.detail = { id: 'matched', status: 'completed', received: 2, planned: 2, arms: h.runs[0].arms, cases: [{ id: 'refund', title: 'Damaged mug refund' }], result: { reproduction: 'not_requested', regressed_cases: 0 }, decision: { state: 'release_ready', reasons: [] },
    cells: [{ caseId: 'refund', variant: 'candidate', repeatIndex: 0, attemptId: 'a1', ...pass }, { caseId: 'refund', variant: 'production', repeatIndex: 0, attemptId: 'a2', ...pass, tools: 'fail' }] };
  renderPanel();
  expect(screen.getByText('v7 passed 1 of 1 case and fixes Damaged mug refund, which fails on production v6. No regressions.')).toBeVisible();
  expect(screen.getByText(/1 case tested \(2 others not selected\)/)).toBeVisible();
});
it('shows a missing allowance as a calm prerequisite before consent, with the admin action', async () => {
  h.spend = { data: { enabled: false, periodLimitUsd: null } };
  renderPanel();
  const run = await screen.findByRole('button', { name: 'Run on the application' });
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
  await screen.findByRole('button', { name: 'Run on the application' });
  expect(screen.getByText('Ask a workspace admin to set one.')).toBeVisible();
  expect(screen.queryByRole('link', { name: 'Set allowance' })).not.toBeInTheDocument();
});
it('defaults to the answer check when no application is registered', () => {
  h.runs = []; h.targets = [];
  renderPanel();
  expect(screen.getByRole('radio', { name: /^Check answers with this context/ })).toBeChecked();
  expect(screen.getByRole('radio', { name: /^Verify on the application/ })).not.toBeChecked();
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
