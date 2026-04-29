import { describe, it, expect, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import ObserveHeaderProvider from "../ObserveHeaderContextProvider";
import { useObserveHeader } from "../ObserveHeaderContext";

const wrapper = ({ children }) => (
  <ObserveHeaderProvider>{children}</ObserveHeaderProvider>
);

describe("ObserveHeaderContext — getViewConfig register/read", () => {
  it("getViewConfig returns null when nothing is registered", () => {
    const { result } = renderHook(() => useObserveHeader(), { wrapper });
    expect(result.current.getViewConfig()).toBeNull();
  });

  it("getViewConfig returns the registered callback's result", () => {
    const { result } = renderHook(() => useObserveHeader(), { wrapper });
    const fake = vi.fn(() => ({ filters: [{ columnId: "status" }] }));
    act(() => result.current.registerGetViewConfig(fake));
    expect(result.current.getViewConfig()).toEqual({
      filters: [{ columnId: "status" }],
    });
    expect(fake).toHaveBeenCalledTimes(1);
  });

  it("getViewConfig returns null after unregister", () => {
    const { result } = renderHook(() => useObserveHeader(), { wrapper });
    act(() => result.current.registerGetViewConfig(() => ({ a: 1 })));
    act(() => result.current.registerGetViewConfig(null));
    expect(result.current.getViewConfig()).toBeNull();
  });

  it("latest registration wins when re-registered", () => {
    const { result } = renderHook(() => useObserveHeader(), { wrapper });
    const first = vi.fn(() => ({ v: 1 }));
    const second = vi.fn(() => ({ v: 2 }));
    act(() => result.current.registerGetViewConfig(first));
    act(() => result.current.registerGetViewConfig(second));
    expect(result.current.getViewConfig()).toEqual({ v: 2 });
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledTimes(1);
  });
});

describe("ObserveHeaderContext — getTabType register/read", () => {
  it("getTabType returns 'traces' default when nothing is registered", () => {
    const { result } = renderHook(() => useObserveHeader(), { wrapper });
    expect(result.current.getTabType()).toBe("traces");
  });

  it("getTabType returns the registered callback's result", () => {
    const { result } = renderHook(() => useObserveHeader(), { wrapper });
    const fake = vi.fn(() => "spans");
    act(() => result.current.registerGetTabType(fake));
    expect(result.current.getTabType()).toBe("spans");
    expect(fake).toHaveBeenCalledTimes(1);
  });

  it("getTabType reverts to default after unregister", () => {
    const { result } = renderHook(() => useObserveHeader(), { wrapper });
    act(() => result.current.registerGetTabType(() => "spans"));
    act(() => result.current.registerGetTabType(null));
    expect(result.current.getTabType()).toBe("traces");
  });

  it("latest registration wins when re-registered", () => {
    const { result } = renderHook(() => useObserveHeader(), { wrapper });
    const first = vi.fn(() => "traces");
    const second = vi.fn(() => "spans");
    act(() => result.current.registerGetTabType(first));
    act(() => result.current.registerGetTabType(second));
    expect(result.current.getTabType()).toBe("spans");
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledTimes(1);
  });
});
