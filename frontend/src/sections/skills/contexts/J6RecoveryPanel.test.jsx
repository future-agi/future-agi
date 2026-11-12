import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import J6RecoveryPanel from "./J6RecoveryPanel";

const refresh = vi.fn();
const previewMutate = vi.fn();
const submitMutate = vi.fn();
let previewState = {};
let submitState = {};
let reviewers = [];
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
  expect(previewMutate).toHaveBeenCalledWith({ contextId: "ctx", candidateVersionId: "v1-id" });
});

it("submits to the only reviewer and offers the exact request link afterwards", () => {
  recovery = { cohort: [], releaseRevision: 7, canPromote: true, candidates: [rollbackCandidate] };
  reviewers = [{ id: "u2", name: "Releaser" }];
  previewState = { data: { fingerprint: "fp", packet: { p: 1 } } };
  submitState = { data: { id: "req-1" } };
  render(<J6RecoveryPanel contextId="ctx" contextName="Support policy" />);
  fireEvent.click(screen.getByRole("button", { name: "Choose v1" }));
  fireEvent.click(screen.getByRole("checkbox", { name: /I reviewed the complete v1 closure/ }));
  expect(screen.getByRole("textbox", { name: "Review link" }).value).toBe(
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
  expect(screen.getByText(/Suggested: v1, the newest eligible release older than the failing v3/)).toBeVisible();
  // The review and its acknowledgement are still required before anything is prepared.
  expect(screen.getByText("Request rollback to v1")).toBeVisible();
  expect(previewMutate).not.toHaveBeenCalled();
  expect(screen.getByRole("checkbox", { name: /I reviewed the complete v1 closure/ })).not.toBeChecked();
  // The quarantined alternative is still refused, and another release can still be chosen.
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
