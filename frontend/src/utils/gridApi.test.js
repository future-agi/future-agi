import { describe, expect, it, vi } from "vitest";
import { isGridApiLive, withLiveGridApi } from "./gridApi";

describe("AG Grid API lifecycle", () => {
  it("accepts a live or legacy API", () => {
    expect(isGridApiLive({})).toBe(true);
    expect(isGridApiLive({ isDestroyed: () => false })).toBe(true);
  });

  it("rejects missing, destroyed, and unreadable APIs", () => {
    expect(isGridApiLive(null)).toBe(false);
    expect(isGridApiLive({ isDestroyed: () => true })).toBe(false);
    expect(
      isGridApiLive({
        isDestroyed: () => {
          throw new Error("disposed");
        },
      }),
    ).toBe(false);
  });

  it("does not run a callback for a destroyed grid", () => {
    const callback = vi.fn();
    const api = { isDestroyed: () => true };

    expect(withLiveGridApi(api, callback)).toBe(false);
    expect(callback).not.toHaveBeenCalled();
  });
});
