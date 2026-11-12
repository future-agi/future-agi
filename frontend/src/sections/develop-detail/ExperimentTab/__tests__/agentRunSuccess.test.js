// An agent (graph) run whose 3 evals passed showed Success (pass@1) "-" on
// the Experiments tab. The table reads the matrix (shape=matrix); the server
// sent each try as null (pass unknown) because the run's judge is a choice
// eval with no explicit direction (server fix: results_root_causes). Here:
// once the matrix carries the passes, the agent run's success shows, and
// "-" (null) stays only when no try has a known pass or fail.
import { describe, expect, it } from "vitest";
import { leaderboardNumbers, successFrom } from "../leaderboardVersions";

// The agent run as the R1 run list sends it (no success_rate field).
const AGENT = {
  run_id: "run-agent",
  label: "#1",
  task_kind: "graph",
  kind: "execution",
  source: "native",
  status: "completed",
  trials: 1,
  progress: { done: 3, total: 3 },
  config: { model: { name: "gpt-4o-mini" } },
};

const matrixOf = (trials) => ({
  rows: trials.map((t, i) => ({
    row_id: `row-${i}`,
    label: String(i + 1),
    cells: { [AGENT.run_id]: { trials: [t] } },
  })),
});

describe("Success (pass@1) for an agent run", () => {
  it("shows 100% when its 3 evaluated tries passed", () => {
    const numbers = leaderboardNumbers([AGENT], matrixOf([true, true, true]));
    expect(successFrom(numbers, AGENT)).toMatchObject({
      passed: 3,
      total: 3,
      rate: 1,
    });
  });

  it("counts a failed try as a fail, not as unknown", () => {
    const numbers = leaderboardNumbers([AGENT], matrixOf([true, false, true]));
    expect(successFrom(numbers, AGENT)).toMatchObject({
      passed: 2,
      total: 3,
    });
  });

  it("is unknown only when no try has a known pass or fail", () => {
    const numbers = leaderboardNumbers([AGENT], matrixOf([null, null, null]));
    expect(successFrom(numbers, AGENT)).toBe(null);
    expect(successFrom(leaderboardNumbers([AGENT], null), AGENT)).toBe(null);
  });
});
