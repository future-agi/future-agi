import { describe, expect, it } from "vitest";

import { canonicalObserveViewMode } from "../viewMode";

describe("canonicalObserveViewMode", () => {
  it.each(["graph", "agentGraph", "agentPath"])(
    "preserves supported %s mode",
    (viewMode) => {
      expect(canonicalObserveViewMode({ viewMode, isSimulator: false })).toBe(
        viewMode,
      );
    },
  );

  it.each(["graph", "agentGraph", "agentPath"])(
    "forces simulator %s mode to graph",
    (viewMode) => {
      expect(canonicalObserveViewMode({ viewMode, isSimulator: true })).toBe(
        "graph",
      );
    },
  );

  it("forces cross-project mode to graph until one project is selected", () => {
    expect(
      canonicalObserveViewMode({
        viewMode: "agentGraph",
        isSimulator: false,
        agentGraphEnabled: false,
      }),
    ).toBe("graph");
  });

  it("also gates Agent Path until one project is selected", () => {
    expect(
      canonicalObserveViewMode({
        viewMode: "agentPath",
        isSimulator: false,
        agentGraphEnabled: false,
      }),
    ).toBe("graph");
  });
});
