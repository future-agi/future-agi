import { describe, expect, it } from "vitest";

import { canonicalObserveViewMode } from "../viewMode";

describe("canonicalObserveViewMode", () => {
  it.each(["graph", "agentGraph"])(
    "preserves supported %s mode",
    (viewMode) => {
      expect(canonicalObserveViewMode({ viewMode, isSimulator: false })).toBe(
        viewMode,
      );
    },
  );

  it("redirects the removed legacy agentPath mode to Agent Graph", () => {
    expect(
      canonicalObserveViewMode({
        viewMode: "agentPath",
        isSimulator: false,
      }),
    ).toBe("agentGraph");
  });

  it.each(["graph", "agentGraph", "agentPath"])(
    "forces simulator %s mode to graph",
    (viewMode) => {
      expect(canonicalObserveViewMode({ viewMode, isSimulator: true })).toBe(
        "graph",
      );
    },
  );
});
