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
