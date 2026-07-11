import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import useFalconStore from "../store/useFalconStore";
import usePendingNavigation from "../hooks/usePendingNavigation";

/**
 * Phase 4C: the `navigate` WS event chain — pendingNavigation must be pushed
 * to the router and consumed EXACTLY once (DashboardLayout mounts this hook).
 */
describe("usePendingNavigation", () => {
  let router;

  beforeEach(() => {
    useFalconStore.getState().resetAll();
    router = { push: vi.fn() };
  });

  it("pushes a pending navigation and clears it (consumed once)", () => {
    renderHook(() => usePendingNavigation(router));

    act(() => {
      useFalconStore.getState().setPendingNavigation("/dashboard/alerts");
    });

    expect(router.push).toHaveBeenCalledTimes(1);
    expect(router.push).toHaveBeenCalledWith("/dashboard/alerts");
    expect(useFalconStore.getState().pendingNavigation).toBeNull();
  });

  it("does not push again on unrelated re-renders", () => {
    const { rerender } = renderHook(() => usePendingNavigation(router));

    act(() => {
      useFalconStore.getState().setPendingNavigation("/dashboard/alerts");
    });
    rerender();
    rerender();

    expect(router.push).toHaveBeenCalledTimes(1);
  });

  it("does nothing while there is no pending navigation", () => {
    renderHook(() => usePendingNavigation(router));
    expect(router.push).not.toHaveBeenCalled();
  });

  it("handles consecutive navigations, one push each", () => {
    renderHook(() => usePendingNavigation(router));

    act(() => {
      useFalconStore.getState().setPendingNavigation("/dashboard/alerts");
    });
    act(() => {
      useFalconStore.getState().setPendingNavigation("/dashboard/develop");
    });

    expect(router.push).toHaveBeenCalledTimes(2);
    expect(router.push).toHaveBeenNthCalledWith(1, "/dashboard/alerts");
    expect(router.push).toHaveBeenNthCalledWith(2, "/dashboard/develop");
    expect(useFalconStore.getState().pendingNavigation).toBeNull();
  });
});
