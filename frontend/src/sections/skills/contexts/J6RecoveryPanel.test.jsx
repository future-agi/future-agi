/* eslint-disable react/prop-types -- Test doubles mirror the real component props. */
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { enqueueSnackbar } from "notistack";
import { MemoryRouter } from "react-router-dom";
import { setSkillTracesOverride } from "../runtime/skillTracesFlag";
import J6RecoveryPanel from "./J6RecoveryPanel";
import ContextCampaignList from "./ContextCampaignList";
import ContextConsumersView from "./ContextConsumersView";

// Keep the real module (the snackbar provider styles import from it) and spy only on enqueueSnackbar.
vi.mock("notistack", async (importOriginal) => ({ ...(await importOriginal()), enqueueSnackbar: vi.fn() }));

// The authorized project picker (loaded lazily, and only with the skillTraces flag on) is replaced by a plain select.
vi.mock("../governance/AdoptionPanel", () => ({
  AuthorizedProjectPicker: ({ value, onChange, label }) => (
    <select aria-label={label} value={value} onChange={(event) => onChange(event.target.value)}>
      <option value="">Choose a project</option>
      <option value="proj-1">Support production</option>
    </select>
  ),
}));

const refresh = vi.fn();
const previewMutate = vi.fn();
const submitMutate = vi.fn();
let previewState = {};
let submitState = {};
let reviewers = [];
let evidenceOptions = { data: { evidence: [] } };
// Reviewers are the server's eligible recipients for the request (readable by any author).
vi.mock("src/api/skills/review-authoring", () => ({ useReviewOptions: () => ({ ...evidenceOptions, data: evidenceOptions.data && { ...evidenceOptions.data, recipients: reviewers } }) }));
const requestRefetch = vi.fn();
const executeMutate = vi.fn();
let requestRead = {};
let executeState = {};
// The server's read of the request this session sent, and the one execute call the tracker may make.
vi.mock("src/api/skills/review-production", () => ({
  useReleaseRequest: () => ({ refetch: requestRefetch, ...requestRead }),
  useExecuteReleaseRequest: () => ({ mutate: executeMutate, ...executeState }),
}));
let closureData = { rows: [], changes: [] };
let recovery = { candidates: [], cohort: [], releaseRevision: 1 };
vi.mock("src/api/skills/j6-recovery", () => ({
  useContextRecovery: () => ({ isLoading: false, error: null, data: recovery }),
  useRefreshContextFacts: () => refresh,
  useRecoveryClosure: () => ({ data: closureData }),
  DESCRIPTOR_PATH: ".futureagi/context.json",
  usePreviewRecoveryRelease: () => ({ mutate: previewMutate, ...previewState }),
  useSubmitRecoveryRequest: () => ({ mutate: submitMutate, ...submitState }),
  useIssueRecoveryCredential: () => ({}),
}));

beforeEach(() => {
  vi.useFakeTimers();
  refresh.mockClear();
  enqueueSnackbar.mockClear();
  previewMutate.mockClear();
  submitMutate.mockClear();
  requestRefetch.mockClear();
  executeMutate.mockClear();
  requestRead = {};
  executeState = {};
  previewState = {};
  submitState = {};
  reviewers = [];
  evidenceOptions = { data: { evidence: [] } };
  recovery = { candidates: [], cohort: [], releaseRevision: 1 };
  closureData = { rows: [], changes: [] };
});
afterEach(() => {
  vi.useRealTimers();
  setSkillTracesOverride(null);
});

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

it("names the release a rollback review is made against and keeps the revision in technical details", () => {
  recovery = {
    cohort: [],
    releaseRevision: 7,
    canPromote: true,
    candidates: [
      {
        id: "v2-id",
        number: 2,
        digest: "sha256:v2",
        eligible: false,
        selectable: false,
        isProduction: true,
        reasons: ["current_production"],
        children: [],
        unavailableChildren: [],
      },
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
  // The sentence names the production version in words; the bound revision is an attribute and sits in technical details.
  const sentence = screen.getByText(/Reviewed against the release that made v2 production\./);
  expect(sentence).toBeVisible();
  expect(sentence).toHaveAttribute("data-release-revision", "7");
  expect(sentence.textContent).not.toMatch(/revision/);
  expect(screen.getByText("Release revision 7")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Request details" })).toBeVisible();
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
  fireEvent.click(screen.getByRole("checkbox", { name: /I've checked the changes/ }));
  expect(previewMutate).toHaveBeenCalledTimes(1);
  expect(previewMutate).toHaveBeenCalledWith({ contextId: "ctx", candidateVersionId: "v1-id", evidence: [], external_pipeline: null });
});

it("submits to the only reviewer and offers the exact request link afterwards", () => {
  recovery = { cohort: [], releaseRevision: 7, canPromote: true, candidates: [rollbackCandidate] };
  reviewers = [{ id: "u2", name: "Releaser" }];
  previewState = { data: { fingerprint: "fp", packet: { p: 1 } } };
  submitState = { data: { id: "req-1", revision: 0 } };
  render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  fireEvent.click(screen.getByRole("button", { name: "Choose v1" }));
  // The form is gone: the same panel is now the status tracker.
  expect(screen.queryByRole("button", { name: "Request rollback to v1" })).toBeNull();
  expect(screen.queryByRole("combobox", { name: "Reviewer" })).toBeNull();
  expect(screen.queryByRole("checkbox", { name: /I've checked the changes/ })).toBeNull();
  expect(screen.getByRole("link", { name: "Open request" }).href).toBe(
    `${window.location.origin}/dashboard/skills/contexts/ctx/review?request=req-1`,
  );
  expect(screen.getByRole("button", { name: "Copy request link" })).toBeEnabled();
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
  expect(screen.getByRole("heading", { name: "Roll back Support policy to v1" })).toBeVisible();
  expect(screen.getByRole("button", { name: "Request rollback to v1" })).toBeVisible();
  expect(previewMutate).not.toHaveBeenCalled();
  expect(screen.getByRole("checkbox", { name: /I've checked the changes/ })).not.toBeChecked();
  // The quarantined alternative is still refused, and another release can still be chosen,
  // all without opening anything: the alternatives sit in the review the acknowledgment covers.
  expect(screen.getByRole("table", { name: "Recovery releases" })).toBeVisible();
  expect(screen.getByRole("button", { name: "Choose v2" })).toBeDisabled();
  expect(screen.queryByRole("button", { name: "Change", exact: true })).toBeNull();
});

it("preselects whoever last moved production as the reviewer", () => {
  recovery = { cohort: failedCohort, releaseRevision: 7, canPromote: true, lastReleaserId: "u3", candidates: suggestedCandidates };
  reviewers = [{ id: "u2", name: "Alice" }, { id: "u3", name: "Releaser" }];
  previewState = { data: { fingerprint: "fp", packet: { p: 1 } } };
  render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  fireEvent.click(screen.getByRole("checkbox", { name: /I've checked the changes/ }));
  expect(screen.getByRole("combobox", { name: "Reviewer" })).toHaveTextContent("Releaser");
  fireEvent.click(screen.getByRole("button", { name: "Request rollback to v1" }));
  expect(submitMutate).toHaveBeenCalledWith(expect.objectContaining({ reviewerId: "u3" }));
});

it("attaches only explicitly selected workspace evidence for the exact recovery candidate", () => {
  recovery = { cohort: failedCohort, releaseRevision: 7, canPromote: true, candidates: suggestedCandidates };
  const evidence = { source: 'skills_text', sourceId: 'run-1', subjectId: 'ctx', candidateDigest: rollbackCandidate.digest, visibility: 'workspace', status: 'passed', annotationRevision: 4 };
  evidenceOptions = { data: { evidence: [evidence, { ...evidence, sourceId: 'other', candidateDigest: 'different' }, { ...evidence, sourceId: 'private', visibility: 'private' }] } };
  render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  expect(screen.getAllByRole('checkbox', { name: 'Attach Answer check' })).toHaveLength(1);
  fireEvent.click(screen.getByRole('checkbox', { name: 'Attach Answer check' }));
  fireEvent.click(screen.getByRole('checkbox', { name: /I've checked the changes/ }));
  expect(previewMutate).toHaveBeenCalledWith({ contextId: 'ctx', candidateVersionId: 'v1-id', evidence: [evidence], external_pipeline: null });
  expect(screen.getByRole('checkbox', { name: 'Attach Answer check' })).toBeDisabled();
});

it("keeps evidence errors distinct from an empty test history and blocks incomplete review", () => {
  recovery = { cohort: failedCohort, releaseRevision: 7, canPromote: true, candidates: suggestedCandidates };
  evidenceOptions = { isError: true, refetch: vi.fn() };
  render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  expect(screen.getByText("Couldn't load test evidence")).toBeVisible();
  expect(screen.getByRole('checkbox', { name: /I've checked the changes/ })).toBeDisabled();
  expect(previewMutate).not.toHaveBeenCalled();
});

it('reconciles an uncertain submission with its original reviewer, packet and idempotency key', () => {
  recovery = { cohort: failedCohort, releaseRevision: 7, canPromote: true, lastReleaserId: 'u2', candidates: suggestedCandidates };
  reviewers = [{ id: 'u2', name: 'First reviewer' }, { id: 'u3', name: 'Second reviewer' }];
  previewState = { data: { fingerprint: 'fp', packet: { candidate: 'v1-id' } } };
  const view = render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  fireEvent.click(screen.getByRole('button', { name: 'Request rollback to v1' }));
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
  fireEvent.click(screen.getByRole('checkbox', { name: 'Attach Answer check' }));
  fireEvent.click(screen.getByRole('checkbox', { name: /I've checked the changes/ }));
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
  expect(screen.getByRole('checkbox', { name: /I've checked the changes/ })).not.toBeChecked();
});

it('reports a production move without treating missing reports as confirmation', () => {
  recovery = { cohort: failedCohort, releaseRevision: 7, candidates: suggestedCandidates };
  const view = render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  recovery = { ...recovery, releaseRevision: 8, candidates: suggestedCandidates.map((row) => ({ ...row, isProduction: row.id === 'v1-id' })) };
  view.rerender(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  expect(screen.getByText('Production moved from v3 to v1 · 0 of 1 app confirmed')).toBeVisible();
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
  expect(screen.getByText('Production moved from v2 to v1 · 0 of 1 app confirmed')).toBeVisible();
});

it('reopens verification after a reload without inventing the previous production release', () => {
  recovery = { cohort: [], releaseRevision: 8, candidates: suggestedCandidates.map((row) => ({ ...row, isProduction: row.id === 'v1-id' })) };
  render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  expect(screen.getByText('Production is v1 · 0 of 0 apps confirmed')).toBeVisible();
  expect(screen.getByText('Replace the application key')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Choose another recovery release' }));
  expect(screen.getByRole('button', { name: 'Choose v3' })).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Choose v1' })).toBeNull();
});


it('uses one exact-identity disclosure and a pressed release choice', () => {
  recovery = { cohort: failedCohort, releaseRevision: 7, canPromote: true, candidates: suggestedCandidates };
  render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  expect(screen.getByRole('button', { name: 'Choose v1' })).toHaveAttribute('aria-pressed', 'true');
  expect(screen.getAllByRole('button', { name: 'Technical details', exact: true })).toHaveLength(1);
  expect(screen.queryByText(/0 removed|0 sources retired/)).toBeNull();
  expect(screen.getByRole('checkbox', { name: "I've checked the changes" })).not.toBeChecked();
});

it('shows a disabled submit and a checking status while preparing the rollback request', () => {
  recovery = { cohort: failedCohort, releaseRevision: 7, canPromote: true, candidates: suggestedCandidates };
  previewState = { isPending: true };
  render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  expect(screen.getByText('Preparing the rollback request…')).toBeVisible();
  expect(screen.getByText('Preparing the rollback request…').closest('[role="status"]')).not.toBeNull();
  expect(screen.getByRole('button', { name: 'Request rollback to v1' })).toBeDisabled();
});

it('offers CLI restore, not retry or approval, for an over-limit context', () => {
  window.history.replaceState({}, '', '?state=inventory-limit');
  try {
    render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
    expect(screen.getByRole('link', { name: 'Open CLI restore instructions' })).toHaveAttribute('href', '?tab=use');
    expect(screen.queryByRole('button', { name: 'Retry' })).toBeNull();
    expect(screen.queryByRole('checkbox')).toBeNull();
  } finally { window.history.replaceState({}, '', '/'); }
});

it("lets a workspace member pick a reviewer from the request's eligible recipients", () => {
  // Members cannot read the organization member list; the recovery picker never depends on it.
  recovery = { cohort: [], releaseRevision: 7, canPromote: true, lastReleaserId: "maya-id", candidates: [rollbackCandidate] };
  reviewers = [{ id: "maya-id", name: "Maya Chen" }, { id: "lee-id", name: "Lee Park" }];
  previewState = { data: { fingerprint: "fp", packet: { p: 1 } } };
  render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  fireEvent.click(screen.getByRole("button", { name: "Choose v1" }));
  fireEvent.click(screen.getByRole("checkbox", { name: /I've checked the changes/ }));
  const picker = screen.getByRole("combobox", { name: "Reviewer" });
  expect(picker).toHaveTextContent("Maya Chen");
  expect(screen.queryByText("Couldn't load reviewers.")).toBeNull();
  expect(screen.getByRole("button", { name: "Request rollback to v1" })).toBeEnabled();
  // The fingerprint is a detail, not part of the decision.
  expect(screen.getByText("Packet fingerprint")).not.toBeVisible();
});

it("leads the rollback review with its consequence and keeps the descriptor bytes in details", () => {
  recovery = { cohort: [], releaseRevision: 7, canPromote: true, candidates: [{ ...rollbackCandidate, lowerThanProduction: true }] };
  const json = '{"links":[{"mount":"skills/refund-policy/","version":1}]}';
  closureData = {
    rows: [{ path: ".futureagi/context.json", status: "changed", sizeBytes: 10, sha256: "a" }, { path: "skills/refund-policy/policy.md", status: "changed", sizeBytes: 10, sha256: "b" }, { path: "AGENTS.md", status: "unchanged", sizeBytes: 10, sha256: "c" }],
    changes: [
      { path: ".futureagi/context.json", status: "changed", links: [{ name: "refund-policy", from: 2, to: 1 }], lines: [{ kind: "removed", text: json.replace("1}", "2}") }, { kind: "added", text: json }] },
      { path: "skills/refund-policy/policy.md", status: "changed", lines: [{ kind: "removed", text: "45 days" }, { kind: "added", text: "30 days" }] },
    ],
  };
  render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  fireEvent.click(screen.getByRole("button", { name: "Choose v1" }));
  expect(screen.getByText("refund-policy v2 → v1 · 2 files differ")).toBeVisible();
  // The generated settings file is tagged so it is not read as the change being rolled back.
  const files = screen.getByRole("table", { name: "Recovery closure files" });
  expect(within(files).getByText("Context settings (generated)")).toBeVisible();
  expect(within(files).getAllByText("Context settings (generated)")).toHaveLength(1);
  expect(within(files).getByRole("cell", { name: ".futureagi/context.json", exact: true })).toBeVisible();
  // One short line under the table says what differs, instead of a paragraph before it.
  expect(screen.getByText(/Only skills\/refund-policy\/policy\.md differs\. The generated settings file changes with it\./)).toBeVisible();
  expect(screen.queryByText(/is the context settings file/)).toBeNull();
  expect(screen.getAllByText("Earlier version")[0]).toBeVisible();
  expect(screen.queryByText("Lower-number rollback")).toBeNull();
  const readable = screen.getByLabelText("skills/refund-policy/policy.md change from production");
  const descriptor = screen.getByLabelText(".futureagi/context.json change from production");
  // People read the file diff first; the descriptor's raw JSON is collapsed but complete.
  expect(readable.compareDocumentPosition(descriptor) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(descriptor).toHaveTextContent("refund-policy v2 → v1");
  expect(descriptor.textContent).toContain(json);
  expect(screen.getByRole("button", { name: "Raw .futureagi/context.json change" })).toHaveAttribute("aria-expanded", "false");
});

it('explains a single release without offering a rollback or replacement credential', () => {
  recovery = { cohort: [], releaseRevision: 1, candidates: [{ ...rollbackCandidate, isProduction: true, eligible: false }] };
  render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  expect(screen.getByText("v1 is the only released version, so there's nothing to roll back to yet.")).toBeVisible();
  expect(screen.queryByRole('list', { name: 'Rollback progress' })).toBeNull();
  expect(screen.queryByRole('table')).toBeNull();
  // UXF-15: the empty state points at "connected apps", never "consumers".
  expect(screen.getByRole('link', { name: 'Open connected apps' })).toHaveAttribute('href', '?tab=consumers');
  expect(document.body.textContent).not.toMatch(/consumer/i);
  expect(screen.queryByRole('button', { name: 'Issue replacement credential' })).toBeNull();
});

it('opens an eligible rollback without needing a retained app failure', () => {
  recovery = { cohort: [], releaseRevision: 2, canPromote: true, candidates: [rollbackCandidate, { ...rollbackCandidate, id: 'v2', number: 2, isProduction: true, eligible: false }] };
  render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  expect(screen.getByRole('heading', { name: 'Roll back Support policy to v1' })).toBeVisible();
  expect(screen.getByText('Production is v2.')).toBeVisible();
  expect(screen.queryByText(/Roll back to v1\?/)).toBeNull();
  expect(screen.getByRole('button', { name: 'Choose v1' })).toHaveAttribute('aria-pressed', 'true');
  expect(screen.getByRole('button', { name: 'Request rollback to v1' })).toBeDisabled();
  expect(screen.getByRole('combobox', { name: 'Reviewer' })).toBeVisible();
  expect(screen.getByRole('checkbox', { name: /I've checked the changes/ })).not.toBeChecked();
  expect(previewMutate).not.toHaveBeenCalled();
});

it('gives denied rollback access a calm next step', () => {
  window.history.replaceState({}, '', '?state=denied');
  try {
    render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
    expect(screen.getByText('Only admins and reviewers can start a rollback.')).toBeVisible();
    expect(screen.queryByText(/Ask a workspace admin/)).toBeNull();
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.getByRole('button', { name: 'Copy request for admin' })).toBeEnabled();
  } finally { window.history.replaceState({}, '', '/'); }
});

it('copies a ready-to-send request for an admin from the denied notice', async () => {
  window.history.replaceState({}, '', '?state=denied');
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
  try {
    render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Copy request for admin' })); });
    expect(writeText).toHaveBeenCalledWith(`Please start a rollback of Support policy, or give me reviewer access: ${window.location.href}`);
    expect(enqueueSnackbar).toHaveBeenCalledWith('Copied the request for an admin', { variant: 'success' });
  } finally { window.history.replaceState({}, '', '/'); }
});

it('names the chosen version in the request when a member cannot start the rollback', async () => {
  recovery = { cohort: failedCohort, releaseRevision: 7, canPromote: false, candidates: suggestedCandidates };
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
  render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  expect(screen.getByText('Only admins and reviewers can start a rollback.')).toBeVisible();
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Copy request for admin' })); });
  expect(writeText).toHaveBeenCalledWith(expect.stringMatching(/^Please start a rollback of Support policy to v1, or give me reviewer access: http/));
});

it('reports a copy failure instead of a false success', async () => {
  window.history.replaceState({}, '', '?state=denied');
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: undefined });
  try {
    render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Copy request for admin' })); });
    expect(enqueueSnackbar).toHaveBeenCalledWith("Couldn't copy the request. Ask an admin to start the rollback.", { variant: 'error' });
    expect(enqueueSnackbar).not.toHaveBeenCalledWith('Copied the request for an admin', expect.anything());
  } finally { window.history.replaceState({}, '', '/'); }
});

it('says what failed to load and that nothing changed', () => {
  window.history.replaceState({}, '', '?state=error');
  try {
    render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
    expect(screen.getByText("Couldn't load recovery details.")).toBeVisible();
    expect(screen.getByText('Nothing was changed.')).toBeVisible();
    expect(screen.queryByText(/consumer facts/)).toBeNull();
  } finally { window.history.replaceState({}, '', '/'); }
});

it('keeps incident facts visible but defers the warning and local actions until verification', () => {
  recovery = { cohort: failedCohort, releaseRevision: 7, candidates: suggestedCandidates };
  const view = render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  expect(screen.getByRole('table', { name: 'Recovery cohort' })).toBeVisible();
  expect(screen.queryByText(/still needs? delivery verification/)).toBeNull();
  expect(screen.queryByRole('button', { name: 'Issue replacement credential' })).toBeNull();
  // No tracker before a request is sent.
  expect(screen.queryByRole('list', { name: 'Rollback progress' })).toBeNull();
  recovery = { ...recovery, releaseRevision: 8, candidates: suggestedCandidates.map((row) => ({ ...row, isProduction: row.id === 'v1-id' })) };
  view.rerender(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  expect(screen.getByText(/still needs? delivery verification/)).toBeVisible();
  expect(screen.getByRole('button', { name: 'Issue replacement credential' })).toBeVisible();
});

it('offers the canonical release review for a newer publication without calling it a rollback', () => {
  recovery = { cohort: [], releaseRevision: 1, candidates: [{ ...rollbackCandidate, isProduction: true, eligible: false }, { ...rollbackCandidate, id: 'v2-id', number: 2 }] };
  render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  expect(screen.getByText("v1 is the only released version, so there's nothing to roll back to yet.")).toBeVisible();
  // The page header carries "Request release for v2"; the body keeps the sentence but not a second button.
  expect(screen.getByText('To change production, send a release request for v2.')).toBeVisible();
  expect(screen.queryByRole('link', { name: /Request release/ })).toBeNull();
  expect(screen.queryByRole('button', { name: /Request release/ })).toBeNull();
  expect(screen.queryByRole('button', { name: 'Request rollback to v1' })).toBeNull();
});

it('keeps excluded alternatives and their restriction visible', () => {
  recovery = { cohort: [], releaseRevision: 1, candidates: suggestedCandidates.map((row) => ({ ...row, eligible: false, selectable: false })) };
  render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  fireEvent.click(screen.getByRole('button', { name: 'Choose another recovery release' }));
  expect(screen.getByText('Quarantined', { exact: true })).toBeVisible();
  expect(screen.getByRole('button', { name: 'Choose v2' })).toBeDisabled();
});

it('keeps restore copy beside the commands and disabled until a full commit is entered', async () => {
  recovery = { cohort: failedCohort, releaseRevision: 7, candidates: suggestedCandidates };
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
  render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  fireEvent.click(screen.getByRole('button', { name: 'Prepare recovery on your machine' }));
  expect(screen.queryByLabelText('Last reviewed commit')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Advanced recovery' }));
  const copy = screen.getByRole('button', { name: 'Copy restore commands' });
  expect(copy).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Last reviewed commit'), { target: { value: 'a'.repeat(40) } });
  await act(async () => fireEvent.click(copy));
  expect(writeText).toHaveBeenCalledWith(expect.stringContaining('a'.repeat(40)));
  expect(screen.getByText('Copied restore commands.')).toBeVisible();
});

it('keeps verification reachable after reloading a completed rollback with newer eligible releases', () => {
  recovery = { cohort: failedCohort, releaseRevision: 8, candidates: [
    { ...rollbackCandidate, isProduction: true, eligible: false },
    { ...rollbackCandidate, id: 'v3-id', number: 3 },
  ] };
  render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  expect(screen.getByRole('table', { name: 'Recovery cohort' })).toBeVisible();
  expect(screen.getByRole('button', { name: 'Issue replacement credential' })).toBeVisible();
  expect(screen.queryByText(/only released version/)).toBeNull();
  expect(screen.getByRole('list', { name: 'Rollback progress' }).lastChild).toHaveAttribute('aria-current', 'step');
});

it('names the version to roll back to in plain words and keeps the review-release sentence', () => {
  recovery = { cohort: failedCohort, releaseRevision: 7, canPromote: true, candidates: suggestedCandidates };
  render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  expect(screen.getByRole('heading', { name: 'Roll back Support policy to v1' })).toBeVisible();
  expect(screen.getByText('Version to roll back to')).toBeVisible();
  expect(screen.queryByText(/Recovery release/)).toBeNull();
  expect(screen.getByText(/Reviewed against the release that made v\d+ production\./)).toBeVisible();
  expect(screen.getByRole('table', { name: 'Recovery releases' })).toBeVisible();
});

it('uses plain words for the application steps, the CLI and the reviewer check', () => {
  recovery = { cohort: failedCohort, releaseRevision: 7, canPromote: true, lastReleaserId: 'u3', candidates: suggestedCandidates };
  reviewers = [{ id: 'u3', name: 'Releaser' }];
  previewState = { data: { fingerprint: 'fp', packet: { p: 1 } } };
  render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  fireEvent.click(screen.getByRole('checkbox', { name: /I've checked the changes/ }));
  expect(screen.getByText(/The reviewer's access is checked when you submit\./)).toBeVisible();
  expect(screen.queryByText(/review authority/)).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Prepare recovery on your machine' }));
  // The credential stays in the main flow; the commit field and thread steps sit behind Advanced recovery.
  expect(screen.getByRole('button', { name: 'Issue replacement credential' })).toBeVisible();
  expect(screen.queryByText('Check apps that are mid-conversation')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Advanced recovery' }));
  expect(screen.getByText('Check apps that are mid-conversation')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Mid-conversation checks' })).toHaveAttribute('aria-expanded', 'false');
  expect(screen.queryByText('Review application threads')).toBeNull();
  expect(screen.getByText(/One command reads the lock from the commit you reviewed/)).toBeVisible();
});

it('says "no report from this app yet" without the warning tone', () => {
  recovery = { cohort: failedCohort, releaseRevision: 7, canPromote: true, candidates: suggestedCandidates };
  render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  expect(screen.getByText('No report from this app yet')).toBeVisible();
  expect(screen.queryByText('Runtime loading not observed')).toBeNull();
});

it('builds the empty-state sentence from the production version the header shows', () => {
  window.history.replaceState({}, '', '?state=empty');
  try {
    recovery = { cohort: [], releaseRevision: 2, candidates: [{ ...rollbackCandidate, id: 'v2-id', number: 2, isProduction: true, eligible: false }] };
    render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
    expect(screen.getByText("v2 is the only released version, so there's nothing to roll back to yet.")).toBeVisible();
    expect(screen.queryByText(/^v1 is the only released version/)).toBeNull();
  } finally { window.history.replaceState({}, '', '/'); }
});

it('explains an over-limit context in plain words and still points at the CLI restore', () => {
  window.history.replaceState({}, '', '?state=inventory-limit');
  try {
    render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
    expect(screen.getByRole('heading', { name: 'This context is too large to review here. Use the CLI restore.' })).toBeVisible();
    expect(screen.queryByText('Recovery needs a complete inventory')).toBeNull();
    expect(screen.getByRole('link', { name: 'Open CLI restore instructions' })).toHaveAttribute('href', '?tab=use');
  } finally { window.history.replaceState({}, '', '/'); }
});

it('keeps the four release columns the phone card is built from', () => {
  recovery = { cohort: failedCohort, releaseRevision: 7, canPromote: true, candidates: suggestedCandidates };
  render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  const table = screen.getByRole('table', { name: 'Recovery releases' });
  const cells = within(table).getAllByRole('row')[2].querySelectorAll('td');
  expect(cells).toHaveLength(4);
  expect(within(table).getAllByRole('columnheader').map((cell) => cell.textContent)).toEqual(['Release', 'Eligibility', 'Linked skills', 'Choose']);
});

const sentRequest = () => {
  recovery = { cohort: failedCohort, releaseRevision: 7, canPromote: true, candidates: suggestedCandidates };
  reviewers = [{ id: 'u2', name: 'Priya Shah' }];
  previewState = { data: { fingerprint: 'fp', packet: { p: 1 } } };
  submitState = { data: { id: 'req-1', revision: 0 } };
};
const approvedRead = (change = {}) => ({ data: { request: { id: 'req-1', status: 'approved', revision: 0 }, preview: { current: true, fingerprint: 'fp' }, canExecute: true, ...change } });

it('shows the status tracker once the request is sent, with the current step marked and no form', () => {
  sentRequest();
  render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  const steps = within(screen.getByRole('list', { name: 'Rollback progress' })).getAllByRole('listitem');
  expect(steps.map((item) => item.textContent)).toEqual(['1Waiting for Priya Shah', '2Approved: move production', '3Check apps updated']);
  expect(steps.map((item) => item.getAttribute('aria-current'))).toEqual(['step', null, null]);
  expect(screen.getByText('Rollback to v1 requested from Priya Shah. Production has not moved.')).toBeVisible();
  expect(screen.queryByRole('button', { name: /Move production/ })).toBeNull();
  expect(screen.queryByRole('button', { name: 'Request rollback to v1' })).toBeNull();
});

it('collapses the tracker to one "Step N of 3" line for the phone card', () => {
  sentRequest();
  render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  expect(screen.getByText('Step 1 of 3 · Waiting for Priya Shah')).toBeInTheDocument();
});

it('moves to "Approved: move production" with one button when the server says the request is approved, current and executable', () => {
  sentRequest();
  requestRead = approvedRead();
  render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  const steps = within(screen.getByRole('list', { name: 'Rollback progress' })).getAllByRole('listitem');
  // A finished step shows a check mark instead of its number.
  expect(steps.map((item) => item.textContent)).toEqual(['Approved by Priya Shah', '2Approved: move production', '3Check apps updated']);
  expect(steps.map((item) => item.getAttribute('aria-current'))).toEqual([null, 'step', null]);
  fireEvent.click(screen.getByRole('button', { name: 'Move production to v1' }));
  // The submitted request, packet fingerprint and revision are sent; nothing from the re-read replaces them.
  expect(executeMutate).toHaveBeenCalledTimes(1);
  expect(executeMutate).toHaveBeenCalledWith({ requestId: 'req-1', revision: 0, expectedFingerprint: 'fp', idempotencyKey: expect.stringMatching(/^[0-9a-f-]{36}$/) });
});

it.each([
  ['it is not current', { preview: { current: false, fingerprint: 'fp' } }],
  ['the packet changed since it was checked', { preview: { current: true, fingerprint: 'other' } }],
  ['moving production needs someone else', { canExecute: false }],
  ['the request is another one', { request: { id: 'req-9', status: 'approved', revision: 0 } }],
])('offers no move button, only the request page, when approved but %s', (_name, change) => {
  sentRequest();
  requestRead = approvedRead(change);
  render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  expect(screen.queryByRole('button', { name: /Move production/ })).toBeNull();
  expect(screen.getByRole('link', { name: 'Open request' })).toBeVisible();
  expect(executeMutate).not.toHaveBeenCalled();
});

it('names a closed request and keeps production unmoved', () => {
  sentRequest();
  requestRead = { data: { request: { id: 'req-1', status: 'changes_requested', revision: 0 } } };
  render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  expect(screen.getByText('The reviewer asked for changes. Production has not moved.')).toBeVisible();
  expect(screen.queryByRole('button', { name: /Move production/ })).toBeNull();
});

it('says when the request status could not be read and offers to check again', () => {
  sentRequest();
  requestRead = { isError: true };
  render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  expect(screen.getByText(/Couldn't check the request status/)).toBeVisible();
  fireEvent.click(screen.getAllByRole('button', { name: 'Check again' })[0]);
  expect(requestRefetch).toHaveBeenCalled();
});

it('re-reads the request slowly while it can still change, and stops once it is closed', () => {
  // The first test in this file leaves the tab "hidden"; polling runs only while it is visible.
  vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible');
  sentRequest();
  const view = render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  act(() => vi.advanceTimersByTime(14000));
  expect(requestRefetch).not.toHaveBeenCalled();
  act(() => vi.advanceTimersByTime(1000));
  expect(requestRefetch).toHaveBeenCalledTimes(1);
  requestRead = { data: { request: { id: 'req-1', status: 'superseded', revision: 0 } } };
  view.rerender(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  act(() => vi.advanceTimersByTime(60000));
  expect(requestRefetch).toHaveBeenCalledTimes(1);
});

it('reads "Production moved" and opens the app check once production is the chosen release', () => {
  recovery = { cohort: failedCohort, releaseRevision: 8, canPromote: true, candidates: suggestedCandidates.map((row) => ({ ...row, isProduction: row.id === 'v1-id' })) };
  render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  const steps = within(screen.getByRole('list', { name: 'Rollback progress' })).getAllByRole('listitem');
  expect(steps.map((item) => item.textContent)).toEqual(['Approved by your reviewer', 'Production moved', '3Check apps updated']);
  expect(steps[2]).toHaveAttribute('aria-current', 'step');
});

it('reads the eligibility cells as "In production" and "Can roll back to"', () => {
  recovery = { cohort: failedCohort, releaseRevision: 7, canPromote: true, candidates: suggestedCandidates };
  render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  const table = screen.getByRole('table', { name: 'Recovery releases' });
  expect(within(table).getByText('In production')).toBeVisible();
  expect(within(table).getByText('Can roll back to')).toBeVisible();
  expect(within(table).queryByText('Eligible')).toBeNull();
  expect(within(table).queryByText('Production')).toBeNull();
});

it('reads a lifted quarantine as "Approval complete", keeps the server note in technical details, and shows a live quarantine reason as written', () => {
  recovery = { cohort: [], releaseRevision: 7, canPromote: true, candidates: [
    { ...rollbackCandidate, id: 'v3-id', number: 3, isProduction: true, eligible: false, selectable: false, reasons: ['current_production'], quarantineReason: 'Policy review complete' },
    { ...rollbackCandidate, id: 'v2-id', number: 2, eligible: false, selectable: false, reasons: ['quarantined'], quarantineReason: 'Leaked a customer key' },
    rollbackCandidate,
  ] };
  render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  const table = screen.getByRole('table', { name: 'Recovery releases' });
  expect(within(table).getByText('Approval for v3 is complete.')).toBeVisible();
  expect(within(table).getByText(/· Approval complete/)).toBeInTheDocument();
  // The release is still not a rollback target: the note changes the wording, never the eligibility.
  expect(within(table).queryByText('Policy review complete')).toBeNull();
  // A quarantined release keeps its reason, and Choose stays refused.
  expect(within(table).getAllByText(/Leaked a customer key/).length).toBeGreaterThan(0);
  expect(screen.getByRole('button', { name: 'Choose v2' })).toBeDisabled();
  fireEvent.click(screen.getAllByRole('button', { name: 'Technical details' })[0]);
  expect(screen.getByText('Safety review note for v3: Policy review complete')).toBeInTheDocument();
});

it('keeps the reviewer select full width before and after the request is prepared', () => {
  recovery = { cohort: [], releaseRevision: 7, canPromote: true, lastReleaserId: 'u3', candidates: [rollbackCandidate] };
  reviewers = [{ id: 'u3', name: 'Releaser' }];
  const view = render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  fireEvent.click(screen.getByRole('button', { name: 'Choose v1' }));
  const picker = () => screen.getByRole('combobox', { name: 'Reviewer' }).closest('.MuiFormControl-root');
  expect(picker()).toHaveClass('MuiFormControl-fullWidth');
  previewState = { data: { fingerprint: 'fp', packet: { p: 1 } } };
  view.rerender(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  fireEvent.click(screen.getByRole('checkbox', { name: /I've checked the changes/ }));
  expect(picker()).toHaveClass('MuiFormControl-fullWidth');
});

it('lists the changed files first and collapses the unchanged ones behind one button (UXF-05)', () => {
  recovery = { cohort: [], releaseRevision: 7, canPromote: true, candidates: [rollbackCandidate] };
  const unchanged = Array.from({ length: 8 }, (_, index) => ({ path: `skills/a-${index}/SKILL.md`, status: 'unchanged', sizeBytes: 10, sha256: `u${index}` }));
  closureData = {
    rows: [...unchanged, { path: 'skills/refund-policy/SKILL.md', status: 'changed', sizeBytes: 10, sha256: 'c' }],
    changes: [{ path: 'skills/refund-policy/SKILL.md', status: 'changed', lines: [{ kind: 'removed', text: '45 days' }, { kind: 'added', text: '30 days' }] }],
  };
  render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  fireEvent.click(screen.getByRole('button', { name: 'Choose v1' }));
  const files = screen.getByRole('table', { name: 'Recovery closure files' });
  // Header and the one changed file; the 8 unchanged files are not listed until asked for.
  expect(within(files).getAllByRole('row')).toHaveLength(2);
  expect(within(files).getByRole('cell', { name: 'skills/refund-policy/SKILL.md', exact: true })).toBeVisible();
  expect(within(files).queryByRole('cell', { name: 'skills/a-7/SKILL.md', exact: true })).toBeNull();
  // The changed file's diff is open: nothing has to be expanded before the tick can be given.
  expect(screen.getByLabelText('skills/refund-policy/SKILL.md change from production')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Show 8 unchanged files' }));
  expect(within(files).getAllByRole('row')).toHaveLength(10);
  expect(within(files).getByRole('cell', { name: 'skills/a-7/SKILL.md', exact: true })).toBeVisible();
  expect(screen.getByRole('button', { name: 'Hide unchanged files' })).toHaveAttribute('aria-expanded', 'true');
  expect(screen.getByText('Only skills/refund-policy/SKILL.md differs.')).toBeVisible();
  // The tick names what is open above it.
  expect(screen.getByRole('checkbox', { name: "I've checked the 1 changed file" })).toBeEnabled();
});

const manyChanged = (count) => {
  const changed = Array.from({ length: count }, (_, index) => ({ path: `skills/c-${index}/SKILL.md`, status: 'changed', sizeBytes: 10, sha256: `c${index}` }));
  const same = Array.from({ length: 3 }, (_, index) => ({ path: `skills/s-${index}/SKILL.md`, status: 'unchanged', sizeBytes: 10, sha256: `s${index}` }));
  closureData = { rows: [...same, ...changed], changes: changed.map((row) => ({ path: row.path, status: 'changed', lines: [{ kind: 'removed', text: 'a' }, { kind: 'added', text: 'b' }] })) };
  return changed;
};

it('opens all of up to three changed files, with no "show more" button', () => {
  recovery = { cohort: [], releaseRevision: 7, canPromote: true, candidates: [rollbackCandidate] };
  const changed = manyChanged(3);
  render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  fireEvent.click(screen.getByRole('button', { name: 'Choose v1' }));
  changed.forEach((row) => expect(screen.getByLabelText(`${row.path} change from production`)).toBeVisible());
  expect(screen.queryByRole('button', { name: /more changed file/ })).toBeNull();
  expect(screen.getByRole('checkbox', { name: "I've checked the 3 changed files" })).toBeEnabled();
});

it('opens the first of more than three changed files and lists every changed file, never collapsing one from the list', () => {
  recovery = { cohort: [], releaseRevision: 7, canPromote: true, candidates: [rollbackCandidate] };
  const changed = manyChanged(8);
  render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  fireEvent.click(screen.getByRole('button', { name: 'Choose v1' }));
  const files = screen.getByRole('table', { name: 'Recovery closure files' });
  // Header plus all 8 changed files; the 3 unchanged ones stay behind their button.
  expect(within(files).getAllByRole('row')).toHaveLength(9);
  changed.forEach((row) => expect(within(files).getByRole('cell', { name: row.path, exact: true })).toBeVisible());
  expect(within(files).queryByRole('cell', { name: 'skills/s-0/SKILL.md', exact: true })).toBeNull();
  expect(screen.getByLabelText('skills/c-0/SKILL.md change from production')).toBeVisible();
  expect(screen.queryByLabelText('skills/c-1/SKILL.md change from production')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Show 7 more changed files' }));
  changed.forEach((row) => expect(screen.getByLabelText(`${row.path} change from production`)).toBeVisible());
  expect(screen.queryByRole('button', { name: /more changed file/ })).toBeNull();
  expect(screen.getByRole('checkbox', { name: "I've checked the 8 changed files" })).toBeEnabled();
  fireEvent.click(screen.getByRole('button', { name: 'Show 3 unchanged files' }));
  expect(within(files).getAllByRole('row')).toHaveLength(12);
});

it('puts the main instructions file first when the changed files are long', () => {
  recovery = { cohort: [], releaseRevision: 7, canPromote: true, candidates: [rollbackCandidate] };
  const paths = ['skills/a/SKILL.md', 'skills/b/SKILL.md', 'skills/c/SKILL.md', 'AGENTS.md'];
  closureData = { rows: paths.map((path) => ({ path, status: 'changed', sizeBytes: 1, sha256: path })), changes: paths.map((path) => ({ path, status: 'changed', lines: [{ kind: 'added', text: 'x' }] })) };
  render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  fireEvent.click(screen.getByRole('button', { name: 'Choose v1' }));
  expect(screen.getByLabelText('AGENTS.md change from production')).toBeVisible();
  expect(screen.queryByLabelText('skills/a/SKILL.md change from production')).toBeNull();
});

it('names linked skill version changes in the tick, and counts only content files', () => {
  recovery = { cohort: [], releaseRevision: 7, canPromote: true, candidates: [rollbackCandidate] };
  closureData = {
    rows: [{ path: '.futureagi/context.json', status: 'changed', sizeBytes: 1, sha256: 'a' }, { path: 'AGENTS.md', status: 'changed', sizeBytes: 1, sha256: 'b' }],
    changes: [
      { path: '.futureagi/context.json', status: 'changed', links: [{ name: 'refund-policy', from: 2, to: 1 }], lines: [{ kind: 'added', text: '{}' }] },
      { path: 'AGENTS.md', status: 'changed', lines: [{ kind: 'added', text: 'x' }] },
    ],
  };
  render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  fireEvent.click(screen.getByRole('button', { name: 'Choose v1' }));
  expect(screen.getByRole('checkbox', { name: "I've checked the 1 changed file and the linked skill versions" })).toBeEnabled();
});

it('is one page: one tick, one button, a reviewer chosen once, and the same request payload as before', () => {
  recovery = { cohort: failedCohort, releaseRevision: 7, canPromote: true, lastReleaserId: 'u3', candidates: suggestedCandidates };
  reviewers = [{ id: 'u2', name: 'Alice' }, { id: 'u3', name: 'Releaser' }];
  const view = render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  expect(screen.getAllByRole('checkbox')).toHaveLength(1);
  expect(screen.getAllByRole('combobox', { name: 'Reviewer' })).toHaveLength(1);
  expect(screen.getByRole('combobox', { name: 'Reviewer' })).toHaveTextContent('Releaser');
  expect(screen.getAllByRole('button', { name: /Request rollback to v1/ })).toHaveLength(1);
  expect(screen.getByRole('button', { name: 'Request rollback to v1' })).toBeDisabled();
  // The tick prepares the packet from the checked changes (the same preview request as before) ...
  fireEvent.click(screen.getByRole('checkbox', { name: /I've checked the changes/ }));
  expect(previewMutate).toHaveBeenCalledWith({ contextId: 'ctx', candidateVersionId: 'v1-id', evidence: [], external_pipeline: null });
  expect(screen.getByRole('button', { name: 'Request rollback to v1' })).toBeDisabled();
  previewState = { data: { fingerprint: 'fp', packet: { p: 1 } } };
  view.rerender(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  expect(screen.getByRole('checkbox', { name: /I've checked the changes/ })).toBeChecked();
  // ... and the one button sends the same packet, fingerprint, reviewer and operation key.
  fireEvent.click(screen.getByRole('button', { name: 'Request rollback to v1' }));
  expect(submitMutate).toHaveBeenCalledTimes(1);
  expect(submitMutate).toHaveBeenCalledWith({ preview: { fingerprint: 'fp', packet: { p: 1 } }, reviewerId: 'u3', idempotencyKey: expect.stringMatching(/^[0-9a-f-]{36}$/) });
});

it('keeps the tick at full contrast once it is given and the form is locked', () => {
  recovery = { cohort: failedCohort, releaseRevision: 7, canPromote: true, lastReleaserId: 'u3', candidates: suggestedCandidates };
  reviewers = [{ id: 'u3', name: 'Releaser' }];
  const view = render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  fireEvent.click(screen.getByRole('checkbox', { name: /I've checked the changes/ }));
  previewState = { data: { fingerprint: 'fp', packet: { p: 1 } } };
  view.rerender(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  expect(screen.getByRole('checkbox', { name: /I've checked the changes/ })).toBeDisabled();
  expect(screen.getByText("I've checked the changes")).toHaveAttribute('data-acknowledged', 'true');
});

it('starts the review again when the tick is withdrawn after a refused packet', () => {
  recovery = { cohort: failedCohort, releaseRevision: 7, canPromote: true, candidates: suggestedCandidates };
  const reset = vi.fn();
  const view = render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  fireEvent.click(screen.getByRole('checkbox', { name: /I've checked the changes/ }));
  expect(previewMutate).toHaveBeenCalledTimes(1);
  previewState = { error: { requestSent: false }, reset };
  view.rerender(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  fireEvent.click(screen.getByRole('checkbox', { name: /I've checked the changes/ }));
  expect(reset).toHaveBeenCalled();
  fireEvent.click(screen.getByRole('checkbox', { name: /I've checked the changes/ }));
  expect(previewMutate).toHaveBeenCalledTimes(2);
});

it('names evidence in plain words and keeps the revision the review is bound to in view', () => {
  recovery = { cohort: failedCohort, releaseRevision: 7, canPromote: true, candidates: suggestedCandidates };
  const base = { subjectId: 'ctx', candidateDigest: rollbackCandidate.digest, visibility: 'workspace', status: 'passed', annotationRevision: 4 };
  evidenceOptions = { data: { evidence: [{ ...base, source: 'skills_text', sourceId: 'run-1' }, { ...base, source: 'simulation_native', sourceId: 'run-2' }] } };
  render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  expect(screen.getByRole('checkbox', { name: 'Attach Answer check' })).toBeInTheDocument();
  expect(screen.getByRole('checkbox', { name: 'Attach Application test' })).toBeInTheDocument();
  expect(screen.queryByText(/Text comparison|Application verification/)).toBeNull();
  expect(screen.getByText(/^Reviewed against the release that made v\d+ production\. If production changes, review and acknowledge the rollback again\.$/)).toBeVisible();
});

it('never collapses a changed file, even when more than six files differ', () => {
  recovery = { cohort: [], releaseRevision: 7, canPromote: true, candidates: [rollbackCandidate] };
  const changed = Array.from({ length: 8 }, (_, index) => ({ path: `skills/c-${index}/SKILL.md`, status: 'changed', sizeBytes: 10, sha256: `c${index}` }));
  const same = Array.from({ length: 3 }, (_, index) => ({ path: `skills/s-${index}/SKILL.md`, status: 'unchanged', sizeBytes: 10, sha256: `s${index}` }));
  closureData = { rows: [...same, ...changed], changes: changed.map((row) => ({ path: row.path, status: 'changed', lines: [{ kind: 'removed', text: 'a' }, { kind: 'added', text: 'b' }] })) };
  render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  fireEvent.click(screen.getByRole('button', { name: 'Choose v1' }));
  const files = screen.getByRole('table', { name: 'Recovery closure files' });
  // The initial view already contains every changed file.
  expect(within(files).getAllByRole('row')).toHaveLength(9);
  fireEvent.click(screen.getByRole('button', { name: 'Show 3 unchanged files' }));
  expect(within(files).getAllByRole('row')).toHaveLength(12);
  fireEvent.click(screen.getByRole('button', { name: 'Hide unchanged files' }));
  // 8 changed rows plus the header; only unchanged files may be collapsed.
  expect(within(files).getAllByRole('row')).toHaveLength(9);
  changed.forEach((row) => expect(within(files).getByRole('cell', { name: row.path, exact: true })).toBeVisible());
  expect(within(files).queryByRole('cell', { name: 'skills/s-0/SKILL.md', exact: true })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Show 3 unchanged files' }));
  expect(within(files).getAllByRole('row')).toHaveLength(12);
});

// ---- Connected apps and Rollouts (UXF-12, UXF-15) ----

const appRow = (id, name, extra = {}) => ({
  binding: { id, name, kind: 'runtime', selector: { label: 'production' } },
  desired: { versionId: 'v2' }, loaded: null, observationAgeSeconds: null,
  states: ['unknown'], observationsComplete: true, nextActionCode: 'observe_runtime', ...extra,
});
const appsData = (results, extra = {}) => ({ results, count: results.length, page: 1, pageSize: 25, asOf: '2026-11-13T03:00:00Z', ...extra });
const renderApps = (data, props = {}) => render(<ContextConsumersView scopeKey="workspace" state="ready" data={data} versionNumbers={{ v2: 2 }} onWindowChange={vi.fn()} onPageChange={vi.fn()} {...props} />);

it('leads the Connected apps list with "apps" and says "No report yet" once, not on every row', () => {
  renderApps(appsData([appRow('a1', 'Support app'), appRow('a2', 'Billing app'), appRow('a3', 'Docs app'), appRow('a4', 'Sales app')]));
  expect(screen.getByText(/^4 apps follow this context\. None has reported which version it received yet\./)).toBeVisible();
  expect(screen.getAllByText('No report yet')).toHaveLength(1);
  const table = screen.getByRole('table', { name: 'Context delivery observations' });
  expect(within(table).queryByText('No report yet')).toBeNull();
  expect(within(table).queryByText('No report')).toBeNull();
  expect(within(table).queryByText('No report from this app yet')).toBeNull();
  expect(document.body.textContent).not.toMatch(/consumer/i);
});

it('renames the Connected apps columns and filters', () => {
  renderApps(appsData([appRow('a1', 'Support app')], { results: [appRow('a1', 'Support app', { loaded: { versionId: 'v2', digest: 'sha256:x' }, observationAgeSeconds: 60, states: ['matches_target'] })] }));
  const table = screen.getByRole('table', { name: 'Context delivery observations' });
  const names = within(table).getAllByRole('columnheader').map((cell) => cell.textContent);
  expect(names).toEqual(['App or repository', 'Kind', 'Should receive', 'Last delivered', 'Delivery', 'Last report', 'Actions']);
  expect(screen.getByLabelText('Search apps')).toBeVisible();
  expect(screen.getByLabelText('Delivery')).toBeVisible();
  expect(screen.getByLabelText('Reports from')).toBeVisible();
  expect(screen.queryByLabelText(/consumer|Observation window|Verification/i)).toBeNull();
});

it('keeps the per-row tag in a mixed list, where it tells rows apart', () => {
  renderApps(appsData([appRow('a1', 'Support app'), appRow('a2', 'Live app', { states: ['matches_target'], loaded: { versionId: 'v2', digest: 'sha256:x' }, observationAgeSeconds: 60 })]));
  expect(screen.queryByText('No report yet')).toBeNull();
  expect(screen.getByText(/^2 apps follow this context\. 1 of 2 still needs a delivery report/)).toBeVisible();
  expect(screen.getByText('No report from this app yet')).toBeVisible();
});

it('opens Inspect from the whole row, and offers Inspect in the trailing ⋯ rather than as an outlined button', () => {
  renderApps(appsData([appRow('a1', 'Support app')]));
  const row = screen.getByText('Support app').closest('tr');
  expect(row).toHaveAttribute('data-row-link', 'true');
  expect(screen.queryByRole('button', { name: 'Inspect Support app' })).toBeNull();
  expect(screen.getByRole('button', { name: 'Actions for Support app' })).toBeVisible();
  fireEvent.click(within(row).getAllByRole('cell')[1]);
  expect(screen.getByRole('dialog')).toBeVisible();
  expect(screen.getByText('Delivery receipts')).toBeVisible();
});

it('words the Connected apps states without "consumer"', () => {
  const { unmount } = renderApps(undefined, { state: 'denied' });
  expect(screen.getByText("You can't view these apps")).toBeVisible();
  unmount();
  const loadFailed = renderApps(undefined, { state: 'error', onRetry: vi.fn() });
  expect(screen.getByText("Couldn't load apps")).toBeVisible();
  loadFailed.unmount();
  const off = renderApps(undefined, { state: 'off' });
  expect(screen.getByText('App reports are off')).toBeVisible();
  off.unmount();
  renderApps(appsData([]), { state: 'empty', registrationAction: <button type="button">Connect an app</button> });
  expect(screen.getByText('No apps connected yet')).toBeVisible();
  expect(document.body.textContent).not.toMatch(/consumer/i);
});

const campaigns = (items) => ({ data: { results: items, count: items.length }, isFetching: false });
const rollout = { id: 'c1', name: 'Refund rollout', targetCount: 3, status: 'draft', revision: 2 };

it('opens a rollout from the whole row, with Open in the trailing ⋯ and no "Review" column or outlined button', () => {
  const onOpen = vi.fn();
  render(<ContextCampaignList query={campaigns([rollout])} enabled onOpen={onOpen} page={1} onPageChange={vi.fn()} onCreate={vi.fn()} />);
  const table = screen.getByRole('table', { name: 'Repository rollouts' });
  expect(within(table).getAllByRole('columnheader').map((cell) => cell.textContent)).toEqual(['Rollout', 'Targets', 'State', 'Actions']);
  expect(within(table).queryByRole('button', { name: /^Review / })).toBeNull();
  expect(within(table).queryByRole('button', { name: 'Open' })).toBeNull();
  expect(within(table).getByRole('button', { name: 'Actions for Refund rollout' })).toBeVisible();
  const row = within(table).getByText('Refund rollout').closest('tr');
  expect(row).toHaveAttribute('data-row-link', 'true');
  fireEvent.click(within(row).getAllByRole('cell')[1]);
  expect(onOpen).toHaveBeenCalledWith('c1');
});

// ---- Connected apps: "Runtime traces" (§7.4, flag `skillTraces`; commit 2) ----

const CONTEXT_ID = '3f1c2a7e-4b5d-4c6e-8f90-a1b2c3d4e5f6';
const BINDING_ID = '9d8c7b6a-5e4f-4a3b-8c2d-1e0f9a8b7c6d';
const tracedApp = () => appRow(BINDING_ID, 'Support app', { binding: { id: BINDING_ID, contextId: CONTEXT_ID, name: 'Support app', kind: 'runtime', selector: { label: 'production' } } });
const renderTraced = () => render(<MemoryRouter><ContextConsumersView scopeKey="workspace" state="ready" data={appsData([tracedApp()])} versionNumbers={{ v2: 2 }} onWindowChange={vi.fn()} onPageChange={vi.fn()} /></MemoryRouter>);

it('shows nothing from the runtime work with the flag off: no project picker and no Runtime traces item', () => {
  setSkillTracesOverride(false);
  renderTraced();
  expect(screen.queryByLabelText('Traces project')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Actions for Support app' }));
  expect(screen.getByRole('menuitem', { name: 'Inspect' })).toBeVisible();
  expect(screen.queryByRole('menuitem', { name: /Runtime traces/ })).toBeNull();
});

it('with the flag on, offers Runtime traces in the row menu, disabled until a traces project is chosen', async () => {
  // The picker loads lazily, so these tests wait on real timers.
  vi.useRealTimers();
  setSkillTracesOverride(true);
  renderTraced();
  expect(await screen.findByLabelText('Traces project')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Actions for Support app' }));
  const item = screen.getByRole('menuitem', { name: /Runtime traces/ });
  expect(item).toHaveAttribute('aria-disabled', 'true');
  expect(screen.getByText('Choose a traces project first')).toBeVisible();
});

it('opens Observe in the same tab, filtered by this context and binding, once a project is chosen', async () => {
  // The picker loads lazily, so these tests wait on real timers.
  vi.useRealTimers();
  setSkillTracesOverride(true);
  renderTraced();
  fireEvent.change(await screen.findByLabelText('Traces project'), { target: { value: 'proj-1' } });
  fireEvent.click(screen.getByRole('button', { name: 'Actions for Support app' }));
  const item = screen.getByRole('menuitem', { name: /Runtime traces/ });
  expect(item).not.toHaveAttribute('aria-disabled', 'true');
  const url = new URL(item.getAttribute('href'), 'http://localhost');
  expect(url.pathname).toBe('/dashboard/observe/proj-1/llm-tracing');
  const filters = JSON.parse(url.searchParams.get('observeLinkFilter'));
  expect(filters.map((entry) => [entry.column_id, entry.filter_config.col_type, entry.filter_config.filter_value])).toEqual([
    ['skill.context_id', 'SKILL', CONTEXT_ID],
    ['skill.binding_id', 'SKILL', BINDING_ID],
  ]);
  expect(item).not.toHaveAttribute('target');
});

it('repeats the traces link in the Inspect drawer, which is the way to it on a phone card', async () => {
  // The picker loads lazily, so these tests wait on real timers.
  vi.useRealTimers();
  setSkillTracesOverride(true);
  renderTraced();
  fireEvent.change(await screen.findByLabelText('Traces project'), { target: { value: 'proj-1' } });
  fireEvent.click(screen.getByText('Support app'));
  const dialog = screen.getByRole('dialog');
  expect(within(dialog).getByRole('link', { name: 'Runtime traces' }).getAttribute('href')).toMatch(/^\/dashboard\/observe\/proj-1\/llm-tracing\?/);
});

it('says the link could not be built when the binding has no valid context id, instead of linking to a wrong list', async () => {
  // The picker loads lazily, so these tests wait on real timers.
  vi.useRealTimers();
  setSkillTracesOverride(true);
  render(<MemoryRouter><ContextConsumersView scopeKey="workspace" state="ready" data={appsData([appRow('a1', 'Support app')])} onWindowChange={vi.fn()} onPageChange={vi.fn()} /></MemoryRouter>);
  fireEvent.change(await screen.findByLabelText('Traces project'), { target: { value: 'proj-1' } });
  fireEvent.click(screen.getByRole('button', { name: 'Actions for Support app' }));
  expect(screen.getByRole('menuitem', { name: /Runtime traces/ })).toHaveAttribute('aria-disabled', 'true');
  expect(screen.getByText("Couldn't build the link")).toBeVisible();
});
