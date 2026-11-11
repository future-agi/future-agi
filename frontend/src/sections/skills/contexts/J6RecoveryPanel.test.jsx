import { act, render } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import J6RecoveryPanel from "./J6RecoveryPanel";

const refresh = vi.fn();
vi.mock("src/api/skills/j6-recovery", () => ({
  useContextRecovery: () => ({
    isLoading: false,
    error: null,
    data: { candidates: [], cohort: [], releaseRevision: 1 },
  }),
  useRefreshContextFacts: () => refresh,
  useRecoveryClosure: () => ({ data: undefined }),
  useRecoveryReviewers: () => ({ data: [] }),
  usePreviewRecoveryRelease: () => ({}),
  useSubmitRecoveryRequest: () => ({}),
  useIssueRecoveryCredential: () => ({}),
}));

beforeEach(() => {
  vi.useFakeTimers();
  refresh.mockClear();
});
afterEach(() => vi.useRealTimers());

it("keeps the recovery facts current while the tab is visible and stops on unmount", () => {
  const { unmount } = render(
    <J6RecoveryPanel contextId="ctx" contextName="Support policy" />,
  );
  expect(refresh).not.toHaveBeenCalled();
  act(() => vi.advanceTimersByTime(5000));
  expect(refresh).toHaveBeenCalledTimes(1);
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
  act(() => vi.advanceTimersByTime(10000));
  expect(refresh).toHaveBeenCalledTimes(1);
  unmount();
  act(() => vi.advanceTimersByTime(10000));
  expect(refresh).toHaveBeenCalledTimes(1);
});
