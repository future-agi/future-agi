import { describe, expect, it } from "vitest";
import { runScenarios } from "../comparison";

/* A finished run keeps the results it had: a proof that broke after the run
   started doesn't replay that scenario as broken in the old run. */
describe("runScenarios and later proof checks", () => {
  const run = { id: "r1", startedAt: "2026-11-24T15:29:10.000Z", scenarioIds: ["a", "b"] };
  const state = {
    scenarios: [
      { id: "a", provedBroke: true, brokeAt: "2026-11-24T17:40:00.000Z" },
      { id: "b", provedBroke: true, brokeAt: "2026-11-24T10:00:00.000Z" },
    ],
  };
  const byId = Object.fromEntries(runScenarios(state, run).scenarios.map((s) => [s.id, s]));
  it("ignores a break found after the run started", () => {
    expect(byId.a.provedBroke).toBe(false);
  });
  it("keeps a break that was known before the run", () => {
    expect(byId.b.provedBroke).toBe(true);
  });
});
