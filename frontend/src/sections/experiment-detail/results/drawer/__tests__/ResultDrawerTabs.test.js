import { describe, expect, it } from "vitest";
import {
  DEFAULT_RESULT_DRAWER_TAB,
  RESULT_DRAWER_TABS,
  RESULT_DRAWER_TAB_COMPONENTS,
  resultDrawerTab,
  visibleResultDrawerTabs,
} from "../ResultDrawerTabs";

describe("ResultDrawerTabs", () => {
  it("lists Compare · Trajectory · Review · Artifacts in that order (R4: Trace merged)", () => {
    expect(RESULT_DRAWER_TABS.map((t) => t.label)).toEqual([
      "Compare",
      "Trajectory",
      "Review",
      "Artifacts",
    ]);
    RESULT_DRAWER_TABS.forEach((t) =>
      expect(RESULT_DRAWER_TAB_COMPONENTS[t.value]).toBe(t.Component),
    );
  });

  it("falls back to Compare for an unknown dtab", () => {
    expect(resultDrawerTab("trace").value).toBe("trajectory");
    expect(resultDrawerTab("nope").value).toBe(DEFAULT_RESULT_DRAWER_TAB);
    expect(resultDrawerTab(undefined).value).toBe("compare");
  });

  it("shows Artifacts only for harness and code runs (R2)", () => {
    const prompt = { run_id: "R1", target_type: "prompt" };
    const harness = { run_id: "R2", config: { kind: "harness" } };
    expect(visibleResultDrawerTabs(prompt).map((t) => t.value)).toEqual([
      "compare",
      "trajectory",
      "review",
    ]);
    expect(visibleResultDrawerTabs(harness).map((t) => t.value)).toContain(
      "artifacts",
    );
    expect(resultDrawerTab("artifacts", prompt).value).toBe("compare");
    expect(resultDrawerTab("artifacts", harness).value).toBe("artifacts");
  });
});
