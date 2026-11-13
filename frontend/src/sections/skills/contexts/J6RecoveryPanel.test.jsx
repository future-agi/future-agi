import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import J6RecoveryPanel from "./J6RecoveryPanel";

const refresh = vi.fn();
const previewMutate = vi.fn();
const submitMutate = vi.fn();
let previewState = {};
let submitState = {};
let reviewers = [];
let evidenceOptions = { data: { evidence: [] } };
vi.mock("src/api/skills/review-authoring", () => ({ useReviewOptions: () => evidenceOptions }));
let recovery = { candidates: [], cohort: [], releaseRevision: 1 };
vi.mock("src/api/skills/j6-recovery", () => ({
  useContextRecovery: () => ({ isLoading: false, error: null, data: recovery }),
  useRefreshContextFacts: () => refresh,
  useRecoveryClosure: () => ({ data: { rows: [], changes: [] } }),
  useRecoveryReviewers: () => ({ data: reviewers }),
  usePreviewRecoveryRelease: () => ({ mutate: previewMutate, ...previewState }),
  useSubmitRecoveryRequest: () => ({ mutate: submitMutate, ...submitState }),
  useIssueRecoveryCredential: () => ({}),
}));

beforeEach(() => {
  vi.useFakeTimers();
  refresh.mockClear();
  previewMutate.mockClear();
  submitMutate.mockClear();
  previewState = {};
  submitState = {};
  reviewers = [];
  evidenceOptions = { data: { evidence: [] } };
  recovery = { candidates: [], cohort: [], releaseRevision: 1 };
});
afterEach(() => vi.useRealTimers());

it("keeps the recovery facts current while the tab is visible and stops on unmount", () => {
  const { unmount } = render(
    <J6RecoveryPanel contextId="ctx" contextName="Support policy" />,
  );
  expect(refresh).not.toHaveBeenCalled();
  act(() => vi.advanceTimersByTime(5000));
  expect(refresh).toHaveBeenCalledTimes(1);
  // A slow load is never cancelled and restarted by the next tick.
  expect(refresh).toHaveBeenCalledWith({ cancelRefetch: false });
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
  act(() => vi.advanceTimersByTime(10000));
  expect(refresh).toHaveBeenCalledTimes(1);
  unmount();
  act(() => vi.advanceTimersByTime(10000));
  expect(refresh).toHaveBeenCalledTimes(1);
});

it("names the release revision a rollback review is made against", () => {
  recovery = {
    cohort: [],
    releaseRevision: 7,
    canPromote: true,
    candidates: [
      {
        id: "v1-id",
        number: 1,
        digest: "sha256:v1",
        eligible: true,
        selectable: true,
        isProduction: false,
        reasons: [],
        children: [],
        unavailableChildren: [],
      },
    ],
  };
  render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  fireEvent.click(screen.getByRole("button", { name: "Choose v1" }));
  expect(
    screen.getByText(/Reviewed against release revision 7\./),
  ).toBeVisible();
});

const rollbackCandidate = {
  id: "v1-id",
  number: 1,
  digest: "sha256:v1",
  eligible: true,
  selectable: true,
  isProduction: false,
  reasons: [],
  children: [],
  unavailableChildren: [],
};

it("prepares the rollback packet when the review is acknowledged, with no separate preview step", () => {
  recovery = { cohort: [], releaseRevision: 7, canPromote: true, candidates: [rollbackCandidate] };
  render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  fireEvent.click(screen.getByRole("button", { name: "Choose v1" }));
  expect(screen.queryByRole("button", { name: "Preview rollback packet" })).toBeNull();
  expect(previewMutate).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("checkbox", { name: /I reviewed the complete v1 closure/ }));
  expect(previewMutate).toHaveBeenCalledTimes(1);
  expect(previewMutate).toHaveBeenCalledWith({ contextId: "ctx", candidateVersionId: "v1-id", evidence: [], external_pipeline: null });
});

it("submits to the only reviewer and offers the exact request link afterwards", () => {
  recovery = { cohort: [], releaseRevision: 7, canPromote: true, candidates: [rollbackCandidate] };
  reviewers = [{ id: "u2", name: "Releaser" }];
  previewState = { data: { fingerprint: "fp", packet: { p: 1 } } };
  submitState = { data: { id: "req-1" } };
  render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  fireEvent.click(screen.getByRole("button", { name: "Choose v1" }));
  fireEvent.click(screen.getByRole("checkbox", { name: /I reviewed the complete v1 closure/ }));
  expect(screen.queryByRole("button", { name: "Submit rollback for review" })).toBeNull();
  expect(screen.queryByRole("combobox", { name: "Reviewer" })).toBeNull();
  expect(screen.getByRole("checkbox", { name: /I reviewed the complete v1 closure/ })).toBeChecked();
  expect(screen.getByRole("link", { name: "Review link" }).href).toBe(
    `${window.location.origin}/dashboard/skills/contexts/ctx/review?request=req-1`,
  );
});

const failedCohort = [
  {
    binding: { id: "b1", name: "App", kind: "runtime", selector: { label: "production" } },
    loaded: null,
    latestFailure: { versionId: "v3-id", diagnosticCodes: ["refund_window_regression"], traceRef: null },
    states: ["unknown"],
  },
];
const suggestedCandidates = [
  { ...rollbackCandidate, id: "v3-id", number: 3, isProduction: true, eligible: false, reasons: ["current_production"] },
  { ...rollbackCandidate, id: "v2-id", number: 2, eligible: false, selectable: false, reasons: ["quarantined"] },
  rollbackCandidate,
];

it("preselects the newest eligible release before the failing one and shows its closure for review without a choice click", () => {
  recovery = { cohort: failedCohort, releaseRevision: 7, canPromote: true, candidates: suggestedCandidates };
  render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  expect(screen.getByText(/v1 is the newest eligible release before v3/)).toBeVisible();
  // The review and its acknowledgement are still required before anything is prepared.
  expect(screen.getByText("Request rollback to v1")).toBeVisible();
  expect(previewMutate).not.toHaveBeenCalled();
  expect(screen.getByRole("checkbox", { name: /I reviewed the complete v1 closure/ })).not.toBeChecked();
  // The quarantined alternative is still refused, and another release can still be chosen.
  fireEvent.click(screen.getByRole("button", { name: "Change", exact: true }));
  expect(screen.getByRole("button", { name: "Choose v2" })).toBeDisabled();
});

it("preselects whoever last moved production as the reviewer", () => {
  recovery = { cohort: failedCohort, releaseRevision: 7, canPromote: true, lastReleaserId: "u3", candidates: suggestedCandidates };
  reviewers = [{ id: "u2", name: "Alice" }, { id: "u3", name: "Releaser" }];
  previewState = { data: { fingerprint: "fp", packet: { p: 1 } } };
  render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  fireEvent.click(screen.getByRole("checkbox", { name: /I reviewed the complete v1 closure/ }));
  expect(screen.getByRole("combobox", { name: "Reviewer" })).toHaveTextContent("Releaser");
  fireEvent.click(screen.getByRole("button", { name: "Submit rollback for review" }));
  expect(submitMutate).toHaveBeenCalledWith(expect.objectContaining({ reviewerId: "u3" }));
});

it("attaches only explicitly selected workspace evidence for the exact recovery candidate", () => {
  recovery = { cohort: failedCohort, releaseRevision: 7, canPromote: true, candidates: suggestedCandidates };
  const evidence = { source: 'skills_text', sourceId: 'run-1', subjectId: 'ctx', candidateDigest: rollbackCandidate.digest, visibility: 'workspace', status: 'passed', annotationRevision: 4 };
  evidenceOptions = { data: { evidence: [evidence, { ...evidence, sourceId: 'other', candidateDigest: 'different' }, { ...evidence, sourceId: 'private', visibility: 'private' }] } };
  render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  expect(screen.getAllByRole('checkbox', { name: 'Attach Text comparison' })).toHaveLength(1);
  fireEvent.click(screen.getByRole('checkbox', { name: 'Attach Text comparison' }));
  fireEvent.click(screen.getByRole('checkbox', { name: /I reviewed the complete v1 closure/ }));
  expect(previewMutate).toHaveBeenCalledWith({ contextId: 'ctx', candidateVersionId: 'v1-id', evidence: [evidence], external_pipeline: null });
  expect(screen.getByRole('checkbox', { name: 'Attach Text comparison' })).toBeDisabled();
});

it("keeps evidence errors distinct from an empty test history and blocks incomplete review", () => {
  recovery = { cohort: failedCohort, releaseRevision: 7, canPromote: true, candidates: suggestedCandidates };
  evidenceOptions = { isError: true, refetch: vi.fn() };
  render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  expect(screen.getByText("Couldn't load test evidence")).toBeVisible();
  expect(screen.getByRole('checkbox', { name: /I reviewed the complete v1 closure/ })).toBeDisabled();
  expect(previewMutate).not.toHaveBeenCalled();
});

it('reconciles an uncertain submission with its original reviewer, packet and idempotency key', () => {
  recovery = { cohort: failedCohort, releaseRevision: 7, canPromote: true, lastReleaserId: 'u2', candidates: suggestedCandidates };
  reviewers = [{ id: 'u2', name: 'First reviewer' }, { id: 'u3', name: 'Second reviewer' }];
  previewState = { data: { fingerprint: 'fp', packet: { candidate: 'v1-id' } } };
  const view = render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  fireEvent.click(screen.getByRole('button', { name: 'Submit rollback for review' }));
  const original = submitMutate.mock.calls[0][0];
  submitState = { error: { code: 'uncertain_outcome' } };
  recovery = { ...recovery, lastReleaserId: 'u3' };
  view.rerender(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  expect(screen.getByRole('combobox', { name: 'Reviewer' })).toHaveAttribute('aria-disabled', 'true');
  fireEvent.click(screen.getByRole('button', { name: 'Check status' }));
  expect(submitMutate.mock.calls[1][0]).toEqual(original);
  expect(original.reviewerId).toBe('u2');
});

it('retries the preview with the acknowledged annotation revision, not refreshed evidence', () => {
  recovery = { cohort: failedCohort, releaseRevision: 7, canPromote: true, candidates: suggestedCandidates };
  const evidence = { source: 'skills_text', sourceId: 'run-1', subjectId: 'ctx', candidateDigest: rollbackCandidate.digest, visibility: 'workspace', status: 'passed', annotationRevision: 4 };
  evidenceOptions = { data: { evidence: [evidence] } };
  const view = render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  fireEvent.click(screen.getByRole('checkbox', { name: 'Attach Text comparison' }));
  fireEvent.click(screen.getByRole('checkbox', { name: /I reviewed the complete v1 closure/ }));
  const original = previewMutate.mock.calls[0][0];
  previewState = { error: { requestSent: false } };
  evidenceOptions = { data: { evidence: [{ ...evidence, annotationRevision: 5 }] } };
  view.rerender(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  fireEvent.click(screen.getByRole('button', { name: 'Retry', exact: true }));
  expect(previewMutate.mock.calls[1][0]).toEqual(original);
  expect(previewMutate.mock.calls[1][0].evidence[0].annotationRevision).toBe(4);
});

it('keeps the submitted request revision-bound when production changes', () => {
  recovery = { cohort: failedCohort, releaseRevision: 7, canPromote: true, candidates: suggestedCandidates };
  reviewers = [{ id: 'u2', name: 'Releaser' }];
  previewState = { data: { fingerprint: 'fp' } };
  submitState = { data: { id: 'req-1' } };
  const view = render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  expect(screen.getByText(/Rollback to v1 requested from Releaser/)).toBeVisible();
  submitState = {};
  previewState = {};
  recovery = { ...recovery, releaseRevision: 8 };
  view.rerender(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  expect(screen.queryByText(/Rollback to v1 requested from Releaser/)).toBeNull();
  expect(screen.getByRole('checkbox', { name: /I reviewed the complete v1 closure/ })).not.toBeChecked();
});

it('reports a production move without treating missing reports as confirmation', () => {
  recovery = { cohort: failedCohort, releaseRevision: 7, candidates: suggestedCandidates };
  const view = render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  recovery = { ...recovery, releaseRevision: 8, candidates: suggestedCandidates.map((row) => ({ ...row, isProduction: row.id === 'v1-id' })) };
  view.rerender(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  expect(screen.getByText('Production moved from v3 to v1 · 0 of 1 consumers confirmed')).toBeVisible();
});

it('uses the observed previous production rather than an obsolete submitted request', () => {
  recovery = { cohort: failedCohort, releaseRevision: 7, candidates: suggestedCandidates };
  reviewers = [{ id: 'u2', name: 'Releaser' }];
  previewState = { data: { fingerprint: 'fp' } };
  submitState = { data: { id: 'req-1' } };
  const view = render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  submitState = {};
  recovery = { ...recovery, releaseRevision: 8, candidates: suggestedCandidates.map((row) => ({ ...row, isProduction: row.id === 'v2-id' })) };
  view.rerender(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  recovery = { ...recovery, releaseRevision: 9, candidates: suggestedCandidates.map((row) => ({ ...row, isProduction: row.id === 'v1-id' })) };
  view.rerender(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  expect(screen.getByText('Production moved from v2 to v1 · 0 of 1 consumers confirmed')).toBeVisible();
});

it('reopens verification after a reload without inventing the previous production release', () => {
  recovery = { cohort: [], releaseRevision: 8, candidates: suggestedCandidates.map((row) => ({ ...row, isProduction: row.id === 'v1-id' })) };
  render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  expect(screen.getByText('Production is v1 · 0 of 0 consumers confirmed')).toBeVisible();
  expect(screen.getByText('Replace the application credential')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Choose another recovery release' }));
  expect(screen.getByRole('button', { name: 'Choose v3' })).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Choose v1' })).toBeNull();
});
