import { describe, expect, it } from "vitest";
import { renderHook } from "@testing-library/react";
import { useDrawerCovered } from "src/hooks/use-drawer-stack";

const drawer = (open, options) =>
  renderHook(({ isOpen }) => useDrawerCovered(isOpen, options), {
    initialProps: { isOpen: open },
  });

describe("useDrawerCovered", () => {
  it("covers every drawer but the newest open one", () => {
    const first = drawer(true);
    const second = drawer(true);
    const third = drawer(true);
    expect(first.result.current).toBe(true);
    expect(second.result.current).toBe(true);
    expect(third.result.current).toBe(false);
  });

  it("uncovers the previous drawer when the newest closes", () => {
    const first = drawer(true);
    const second = drawer(true);
    second.rerender({ isOpen: false });
    expect(first.result.current).toBe(false);
    expect(second.result.current).toBe(false);
  });

  it("drops a drawer from the stack when it unmounts while open", () => {
    const first = drawer(true);
    const second = drawer(true);
    second.unmount();
    expect(first.result.current).toBe(false);
  });

  it("keeps an overlay panel out of the stack", () => {
    const parent = drawer(true);
    const overlay = drawer(true, { overlay: true });
    expect(parent.result.current).toBe(false);
    expect(overlay.result.current).toBe(false);
    // A drawer opened after the overlay still covers the parent.
    drawer(true);
    expect(parent.result.current).toBe(true);
    expect(overlay.result.current).toBe(false);
  });
});
