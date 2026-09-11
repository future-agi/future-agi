import React from "react";
import { describe, it, expect, beforeEach, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { BrowserRouter } from "react-router-dom";
import { useUrlState } from "../use-url-state";

const wrapper = ({ children }) => <BrowserRouter>{children}</BrowserRouter>;

describe("useUrlState", () => {
  beforeEach(() => {
    window.history.pushState({}, "", "/test");
  });

  it("initializes from the URL and writes canonical JSON values back to it", async () => {
    window.history.pushState({}, "", "/test?count=2");

    const { result } = renderHook(() => useUrlState("count", 0), { wrapper });

    expect(result.current[0]).toBe(2);

    act(() => {
      result.current[1]((current) => current + 1);
    });

    await waitFor(() => {
      expect(window.location.search).toContain("count=3");
    });
    expect(result.current[0]).toBe(3);
  });

  it("preserves URL params when multiple setters run in the same action", async () => {
    const { result } = renderHook(
      () => {
        const [first, setFirst] = useUrlState("first", "");
        const [second, setSecond] = useUrlState("second", "");
        return { first, setFirst, second, setSecond };
      },
      { wrapper },
    );

    act(() => {
      result.current.setFirst("one");
      result.current.setSecond("two");
    });

    await waitFor(() => {
      expect(window.location.search).toContain("first=one");
      expect(window.location.search).toContain("second=two");
    });
    expect(result.current.first).toBe("one");
    expect(result.current.second).toBe("two");
  });

  it("does not update router state from inside the React state updater", () => {
    const consoleError = vi
      .spyOn(console, "error")
      .mockImplementation(() => undefined);
    const { result } = renderHook(() => useUrlState("selectedTab", "trace"), {
      wrapper,
    });

    act(() => {
      result.current[1](() => "spans");
    });

    const renderPhaseRouterWarning = consoleError.mock.calls.some((args) =>
      String(args[0]).includes(
        "Cannot update a component (`BrowserRouter`) while rendering",
      ),
    );
    expect(renderPhaseRouterWarning).toBe(false);

    consoleError.mockRestore();
  });
});
