import { describe, it, expect } from "vitest";
import { render, screen } from "src/utils/test-utils";
import { MOCK_WORLD } from "src/api/simulate-environments/_fixtures/world";
import { generatedPool } from "src/api/simulate-environments/_fixtures/scenarioPool";
import ScenarioTable from "../ScenarioTable";
import { scenarioReadiness } from "../scenarioReadiness";

const list = (keys, truncated = false) => ({ keys, truncated });
const report = (lists) => ({ selection: { lists } });

describe("scenarioReadiness", () => {
  it("marks blocked, not proved here and not checked scenarios, blocked first", () => {
    const marks = scenarioReadiness(
      report({
        eligible: list(["ok"]),
        blocked: list(["a"]),
        stale: list(["b", "a"]),
        untested: list(["c"]),
      }),
    );
    expect(Object.fromEntries(marks)).toEqual({
      a: "blocked",
      b: "stale",
      c: "untested",
    });
  });

  it("reads a truncated list from the paged cases instead", () => {
    const marks = scenarioReadiness(
      report({
        blocked: list(["a"], true),
        stale: list([]),
        untested: list([]),
      }),
      { blocked: ["a", "z"] },
    );
    expect(marks.get("z")).toBe("blocked");
  });
});

describe("ScenarioTable readiness mark", () => {
  it("shows Blocked on a scenario the default versions hold back", () => {
    // Readiness names a scenario by its key, not the row's id.
    const rows = generatedPool(MOCK_WORLD)
      .slice(0, 2)
      .map((row, at) => ({ ...row, scenarioKey: `key-${at}` }));
    render(
      <ScenarioTable
        rows={rows}
        env={MOCK_WORLD}
        readiness={new Map([["key-0", "blocked"]])}
      />,
    );
    expect(screen.getAllByText("Blocked")).toHaveLength(1);
  });
});
