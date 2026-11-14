import { describe, it, expect } from "vitest";
import { act, renderHook } from "@testing-library/react";

import useSelection from "../useSelection";

describe("useSelection — predicate selection that survives paging", () => {
  it("include mode: toggles ids and counts them", () => {
    const { result } = renderHook(() => useSelection(2431));
    act(() => result.current.toggle("a"));
    act(() => result.current.toggle("b"));
    expect(result.current.isSelected("a")).toBe(true);
    expect(result.current.isSelected("c")).toBe(false);
    expect(result.current.count).toBe(2);
    expect(result.current.payload({ search: "x" })).toEqual({ mode: "include", ids: ["a", "b"] });
  });

  it("select-all-matching: count is the whole match, payload is filter + exclusions", () => {
    const { result } = renderHook(() => useSelection(2431));
    act(() => result.current.selectAllMatching());
    expect(result.current.count).toBe(2431);
    // Un-checking a row in all-mode records it as an exception, not a removal.
    act(() => result.current.toggle("a"));
    expect(result.current.isSelected("a")).toBe(false);
    expect(result.current.isSelected("z")).toBe(true); // still selected — never loaded, still matches
    expect(result.current.count).toBe(2430);
    expect(result.current.payload({ search: "ride", filters: { persona: ["Dana"] } })).toEqual({
      mode: "all",
      search: "ride",
      filters: { persona: ["Dana"] },
      excludeIds: ["a"],
    });
  });

  it("header tri-state reflects the current page only", () => {
    const { result } = renderHook(() => useSelection(2431));
    const page = ["p1", "p2", "p3"];
    expect(result.current.pageState(page)).toEqual({ allChecked: false, someChecked: false });
    act(() => result.current.setPage(page, true));
    expect(result.current.pageState(page)).toEqual({ allChecked: true, someChecked: false });
    act(() => result.current.toggle("p2"));
    expect(result.current.pageState(page)).toEqual({ allChecked: false, someChecked: true });
  });

  it("clear resets mode and exceptions", () => {
    const { result } = renderHook(() => useSelection(10));
    act(() => result.current.selectAllMatching());
    act(() => result.current.toggle("a"));
    act(() => result.current.clear());
    expect(result.current.mode).toBe("include");
    expect(result.current.count).toBe(0);
    expect(result.current.isSelected("a")).toBe(false);
  });
});
