import { describe, expect, it } from "vitest";

import { groupRuns, summarizeRun } from "../runModel";

const row = (over) => ({
  id: over.id,
  request_id: `req-${over.id}`,
  model: "gpt-4o-mini",
  provider: "openai",
  status_code: 200,
  is_error: false,
  latency_ms: 10,
  cost: "0.000042",
  total_tokens: 14,
  input_tokens: 7,
  output_tokens: 7,
  agent_id: "support-agent",
  agent_workflow: null,
  agent_env: null,
  agent_parent_run_id: null,
  agent_root_run_id: null,
  ...over,
});

// Newest first, as the list endpoint returns them.
const ROWS = [
  row({
    id: "a5",
    agent_run_id: "run-2",
    agent_step: "final",
    started_at: "2026-10-20T09:32:45.000Z",
    latency_ms: 6,
  }),
  row({
    id: "a4",
    agent_run_id: "run-2",
    agent_step: "plan",
    started_at: "2026-10-20T09:32:44.000Z",
    status_code: 502,
    is_error: true,
    cost: "0",
  }),
  row({ id: "x1", agent_run_id: null, started_at: "2026-10-20T09:32:43.500Z" }),
  row({
    id: "a3",
    agent_run_id: "run-1",
    agent_step: "final",
    started_at: "2026-10-20T09:32:43.000Z",
    model: "gpt-4o",
    latency_ms: 300,
  }),
  row({
    id: "a2",
    agent_run_id: "run-1",
    agent_step: "tool",
    started_at: "2026-10-20T09:32:42.000Z",
  }),
  row({
    id: "a1",
    agent_run_id: "run-1",
    agent_step: "plan",
    started_at: "2026-10-20T09:32:41.000Z",
    agent_workflow: "triage",
    agent_env: "prod",
  }),
];

describe("groupRuns", () => {
  it("groups requests by run id, newest run first, ignoring requests with none", () => {
    const runs = groupRuns(ROWS);
    expect(runs.map((r) => r.runId)).toEqual(["run-2", "run-1"]);
    const [two, one] = runs;
    expect(two).toMatchObject({
      agentId: "support-agent",
      requests: 2,
      errors: 1,
      steps: ["plan", "final"],
      startedAt: "2026-10-20T09:32:44.000Z",
      lastAt: "2026-10-20T09:32:45.000Z",
    });
    expect(two.durationMs).toBe(1006);
    expect(one).toMatchObject({
      requests: 3,
      errors: 0,
      steps: ["plan", "tool", "final"],
      models: ["gpt-4o-mini", "gpt-4o"],
      workflow: "triage",
      env: "prod",
      tokens: 42,
    });
    expect(one.cost).toBeCloseTo(0.000126, 9);
    expect(one.durationMs).toBe(2300);
  });

  it("is empty when no request carries a run id", () => {
    expect(groupRuns([row({ id: "x", agent_run_id: "" })])).toEqual([]);
    expect(groupRuns(undefined)).toEqual([]);
  });
});

describe("summarizeRun", () => {
  it("orders the calls oldest first on the run's axis and breaks them down", () => {
    const calls = ROWS.filter((r) => r.agent_run_id === "run-1");
    const run = summarizeRun(calls);
    expect(run.calls.map((c) => c.id)).toEqual(["a1", "a2", "a3"]);
    expect(run.calls.map((c) => c.offsetMs)).toEqual([0, 1000, 2000]);
    expect(run.totalMs).toBe(2300);
    expect(run.bySteps).toEqual([
      expect.objectContaining({ key: "plan", requests: 1 }),
      expect.objectContaining({ key: "tool", requests: 1 }),
      expect.objectContaining({ key: "final", requests: 1, latencyMs: 300 }),
    ]);
    expect(run.byModels.map((m) => [m.key, m.requests])).toEqual([
      ["gpt-4o-mini", 2],
      ["gpt-4o", 1],
    ]);
    expect(run.parentRunId).toBeNull();
  });

  it("names the parent and root runs the calls carried", () => {
    const run = summarizeRun([
      row({
        id: "c1",
        agent_run_id: "child",
        agent_parent_run_id: "run-1",
        agent_root_run_id: "root-0",
        started_at: "2026-10-20T09:32:41.000Z",
      }),
    ]);
    expect(run.parentRunId).toBe("run-1");
    expect(run.rootRunId).toBe("root-0");
  });

  it("is empty for no calls", () => {
    expect(summarizeRun([])).toMatchObject({
      calls: [],
      requests: 0,
      totalMs: 0,
    });
  });
});

describe("totals over unpriced calls", () => {
  it("counts the calls the gateway found no price for", () => {
    const [run] = groupRuns([
      { agent_run_id: "r", cost: "0.002", started_at: "2026-10-20T00:00:01Z" },
      {
        agent_run_id: "r",
        cost: "0",
        cost_source: "unpriced",
        started_at: "2026-10-20T00:00:02Z",
      },
    ]);
    expect(run).toMatchObject({ requests: 2, unpriced: 1, cost: 0.002 });
    const detail = summarizeRun([
      { agent_step: "a", cost: "0", metadata: { cost_source: "unpriced" } },
      { agent_step: "b", cost: "0.001" },
    ]);
    expect(detail.unpriced).toBe(1);
    expect(detail.bySteps.find((s) => s.key === "a").unpriced).toBe(1);
    expect(detail.bySteps.find((s) => s.key === "b").unpriced).toBe(0);
  });
});
