import { describe, it, expect, vi } from "vitest";

vi.mock("../scenarios", () => ({ listScenarios: vi.fn() }));

const { listScenarios } = await import("../scenarios");
const { listAllScenarioKeys, runnableScenarioKey } = await import(
  "../scenarioSelection"
);

describe("listAllScenarioKeys", () => {
  it("leaves out rows only the unsaved changes add", async () => {
    listScenarios.mockResolvedValue({
      total_pages: 1,
      results: [
        { id: "r1", scenario_key: "a", draft_change: "unchanged" },
        { id: "r2", scenario_key: "b", draft_change: "changed" },
        { id: "r3", scenario_key: "c", draft_change: "added" },
        { id: "r4", scenario_key: "d" },
      ],
    });
    await expect(listAllScenarioKeys("env-1")).resolves.toEqual([
      "a",
      "b",
      "d",
    ]);
  });
});

describe("runnableScenarioKey", () => {
  it("has no key for an added row", () => {
    expect(
      runnableScenarioKey({ scenario_key: "c", draft_change: "added" }),
    ).toBeNull();
    expect(runnableScenarioKey({ scenario_key: "a" })).toBe("a");
  });
});
