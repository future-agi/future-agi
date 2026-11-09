import { describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import { useGlobalCommands, viewLink } from "../providers";

const ctx = (extra = {}) => ({
  isDark: false,
  hasTips: false,
  showTips: vi.fn(),
  openSheet: vi.fn(),
  copy: vi.fn(),
  askFalcon: vi.fn(),
  toggleDark: vi.fn(),
  ...extra,
});

describe("global palette commands (one view, G-04)", () => {
  it("has no Simple / Advanced switch", () => {
    const { result } = renderHook(() => useGlobalCommands(ctx()));
    const ids = result.current.map((c) => c.id);
    expect(ids.some((id) => id.startsWith("ui."))).toBe(false);
    expect(
      result.current.some((c) => /Switch to (Simple|Advanced)/.test(c.label)),
    ).toBe(false);
    expect(ids).toContain("view.copyLink");
  });

  it("offers `Show tips on this page` only when the page has tips", () => {
    const none = renderHook(() => useGlobalCommands(ctx()));
    expect(none.result.current.some((c) => c.id === "help.tips")).toBe(false);
    const c = ctx({ hasTips: true });
    const some = renderHook(() => useGlobalCommands(c));
    const tips = some.result.current.find((x) => x.id === "help.tips");
    expect(tips.label).toBe("Show tips on this page");
    tips.run();
    expect(c.showTips).toHaveBeenCalled();
  });

  it("Copy link to this view drops the legacy `ui` param", () => {
    window.history.replaceState(
      {},
      "",
      "/dashboard/develop?ui=simple&tab=data",
    );
    expect(viewLink()).toBe(
      `${window.location.origin}/dashboard/develop?tab=data`,
    );
  });
});
