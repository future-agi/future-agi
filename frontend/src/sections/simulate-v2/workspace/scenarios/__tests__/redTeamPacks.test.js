import { describe, expect, it } from "vitest";
import {
  RED_TEAM_PACKS,
  packCount,
  packOf,
  packScenarioId,
  redTeamLabels,
} from "../redTeamPacks";

describe("red-team packs", () => {
  it("every pack but baseline brings its own cases", () => {
    RED_TEAM_PACKS.filter((p) => p.id !== "baseline").forEach((p) => {
      expect(p.cases).toHaveLength(packCount(p));
    });
  });

  it("finds the pack from a task id", () => {
    const id = packScenarioId("abc", "injection", 3);
    expect(packOf({ id })?.label).toBe("Prompt injection");
  });

  it("finds the pack from labels when the id has none", () => {
    expect(packOf({ id: "x", labels: redTeamLabels("tools") })?.id).toBe(
      "tools",
    );
  });

  it("is null for an ordinary scenario", () => {
    expect(packOf({ id: "gen-abc-4" })).toBeNull();
  });
});
