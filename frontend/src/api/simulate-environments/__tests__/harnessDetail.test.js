import { describe, it, expect } from "vitest";
import { harnessDetailToEnvironment } from "../harnessDetail";

// The §6 example body from environments-api-contracts, trimmed to the fields the
// adapter reads. Kept close to verbatim so the mapping is checked against the
// contract, not against our own assumptions.
const DETAIL = {
  id: "0b1c",
  overview: {
    id: "0b1c",
    name: "ride-voice-agent",
    description: "Books rides",
    domain: "Logistics",
    source_kind: "github",
    agent_type: "voice",
    status: "completed",
    stage: "completed",
    scenario_count: 68,
    sub_goals_count: 34,
    tools_count: 8,
    runs_count: 1,
    flows_count: 7,
    guardrails_count: 6,
    personas_count: 17,
    evaluations_count: 1,
    run: {
      run_test_id: "rt-1",
      test_execution_id: "ex-1",
      simulation_url: "/dashboard/simulate/test/rt-1/ex-1/call-details",
    },
  },
  contract: {
    agent: "Customer Support Agent",
    one_liner: "Handles refund conversations",
    modality: "voice",
    call_direction: "inbound",
    system_prompt_excerpt: "You are a support agent…",
    tools: [
      {
        name: "lookup_order",
        args: ["order_id"],
        arg_types: { order_id: "str" },
        description: "Look an order up",
        requires: [],
      },
    ],
    real_use_cases: ["Fetch order by ID", "Refund up to the order total"],
    hard_constraints: ["Refunds above $200 need supervisor approval"],
    amendments: [
      { subject: "tool removed - lookup_order", note: "no runnable seam in the source" },
      { subject: "", note: "a line that carries no reason" },
    ],
    sub_goals: [
      {
        name: "refund_refused_on_expired_card",
        what: "the agent refuses before charging",
        kind: "checkpoint",
        claim: "",
        check: "def check(run):\n    return not run.charges\n",
      },
    ],
    end_conditions: {
      max_turns: 12,
      max_duration_seconds: 3600,
      clock: "real-time",
      ended_reasons: ["assistant-ended-call", "customer-ended-call"],
    },
    chosen_evals: ["cs_policy"],
  },
  world: {
    runtime: { services: ["agent", "tools-api"] },
    personas: [
      { name: "Priya Raman", occupation: "driver", scenario_keys: ["expired-card-refund"] },
    ],
    stores: [
      {
        capability: "orders",
        engine: "postgres",
        strategy: "seeded",
        tables: [
          { name: "orders", rows: 240 },
          { name: "refunds", rows: 31 },
        ],
        total_rows: 271,
      },
    ],
  },
  scenarios: [
    {
      scenario_key: "expired-card-refund",
      scenario_id: "sc-1",
      name: "expired-card-refund",
      instruction: "Call in about an expired card",
      use_case: "Refund up to the order total",
      tests: "the agent notices before charging",
      outcome: "refund refused",
      steps: 2,
      sub_goals: [
        { name: "refund_refused_on_expired_card", what: "refuses before charging", kind: "checkpoint" },
      ],
      persona: { name: "Priya Raman" },
      status: "passed",
      call_execution_id: "call-1",
    },
  ],
  evaluations: {
    selected: [{ id: "eval-1", name: "cs_policy", description: "Policy compliance", runnable: true }],
    results: [],
  },
  settings: {
    agent: { connector: "livekit", mode: null },
  },
};

describe("harnessDetailToEnvironment", () => {
  it("returns null for a missing detail", () => {
    expect(harnessDetailToEnvironment(null)).toBeNull();
  });

  it("maps overview identity, status and run bridge ids", () => {
    const { env } = harnessDetailToEnvironment(DETAIL);
    expect(env.id).toBe("0b1c");
    expect(env.name).toBe("ride-voice-agent");
    expect(env.domain).toBe("Logistics");
    expect(env.agentType).toBe("voice");
    expect(env.surface).toBe("voice");
    expect(env.buildStatus).toBe("ready");
    expect(env.platform).toMatchObject({
      runTestId: "rt-1",
      testExecutionId: "ex-1",
      simulationUrl: "/dashboard/simulate/test/rt-1/ex-1/call-details",
    });
  });

  it("maps the real contract tools, rules and one-liner (all real provenance)", () => {
    const { env, world } = harnessDetailToEnvironment(DETAIL);
    expect(env.tools).toEqual([
      expect.objectContaining({ name: "lookup_order", args: ["order_id"], desc: "Look an order up" }),
    ]);
    expect(env.rules).toEqual(["Refunds above $200 need supervisor approval"]);
    expect(env.description).toBe("Handles refund conversations");
    expect(world.tools).toHaveLength(1);
    expect(env.provenance.tools).toBe("real");
    expect(env.provenance.rules).toBe("real");
  });

  it("attaches the new structured contract fields verbatim", () => {
    const { env } = harnessDetailToEnvironment(DETAIL);
    // amendments keep the {subject, note} shape (breaking change) — not re-joined.
    expect(env.amendments).toEqual([
      { subject: "tool removed - lookup_order", note: "no runnable seam in the source" },
      { subject: "", note: "a line that carries no reason" },
    ]);
    expect(env.endConditions).toMatchObject({ max_turns: 12, clock: "real-time" });
    expect(env.subGoals[0].name).toBe("refund_refused_on_expired_card");
    expect(env.useCases).toContain("Refund up to the order total");
    expect(env.callDirection).toBe("inbound");
    expect(env.systemPromptExcerpt).toBe("You are a support agent…");
    expect(env.evalPreset).toEqual(["cs_policy"]);
  });

  it("flattens world.stores into seed tables and keeps the raw stores", () => {
    const { env } = harnessDetailToEnvironment(DETAIL);
    expect(env.seed.tables).toEqual([
      { name: "orders", rows: 240, note: "orders · postgres" },
      { name: "refunds", rows: 31, note: "orders · postgres" },
    ]);
    expect(env.seed.services).toEqual(["agent", "tools-api"]);
    expect(env.stores).toHaveLength(1);
    expect(env.personas[0].name).toBe("Priya Raman");
  });

  it("maps scenarios to pool rows with real sub-goals and use_case", () => {
    const { envState } = harnessDetailToEnvironment(DETAIL);
    expect(envState.scenarios).toHaveLength(1);
    const row = envState.scenarios[0];
    expect(row.id).toBe("expired-card-refund");
    expect(row.useCase).toBe("Refund up to the order total");
    expect(row.status).toBe("passed");
    expect(row.subTasks).toEqual([
      { id: "refund_refused_on_expired_card", label: "refuses before charging", kind: "checkpoint" },
    ]);
  });

  it("maps evaluations.selected into applied eval rows", () => {
    const { envState } = harnessDetailToEnvironment(DETAIL);
    expect(envState.evals).toEqual([
      { id: "eval-1", name: "cs_policy", blurb: "Policy compliance", runnable: true },
    ]);
  });

  it("exposes overview counts including runs_count 0/1", () => {
    const { env } = harnessDetailToEnvironment(DETAIL);
    expect(env.counts).toMatchObject({
      flows: 7,
      guardrails: 6,
      personas: 17,
      subGoals: 34,
      runs: 1,
    });
  });

  it("is null-tolerant while sections are still building", () => {
    const building = {
      id: "j1",
      overview: { id: "j1", name: "half-built", status: "building" },
      contract: null,
      world: null,
      scenarios: null,
      evaluations: null,
    };
    const { env, world, envState } = harnessDetailToEnvironment(building);
    expect(env.buildStatus).toBe("building");
    expect(env.detailReady).toBe(false);
    expect(env.tools).toBeUndefined();
    expect(env.amendments).toBeNull();
    expect(world).toEqual({});
    expect(envState.evals).toEqual([]);
    expect(envState.scenarios).toBeUndefined();
  });

  it("marks a failed overview as failed, not building", () => {
    const failed = {
      id: "j1",
      overview: { id: "j1", name: "broke", status: "failed" },
      contract: null,
      world: null,
    };
    expect(harnessDetailToEnvironment(failed).env.buildStatus).toBe("failed");
  });
});
