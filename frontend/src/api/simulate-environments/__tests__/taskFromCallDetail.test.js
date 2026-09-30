import { describe, it, expect } from "vitest";

import { taskFromCallDetail } from "../runCalls";

// A trimmed `GET /simulate/v3/call-executions/{id}/` payload: the detail
// serializer's fields plus the `build_call_rows` fields the view merges in.
const CHAT_DETAIL = {
  id: "call-chat-1",
  status: "completed",
  scenario: "Refund request",
  source_scenario_key: "refund-late",
  trial_index: 2,
  harness_outcome_status: "passed",
  goal: "Get a refund",
  sub_goals: ["Verify order"],
  persona: "Impatient Ian",
  persona_details: { name: "Impatient Ian", voice: null, age: 40, traits: [] },
  outcome: "failed",
  simulation_call_type: "text",
  provider: "hosted",
  duration_seconds: 42,
  turn_count: 6,
  total_tokens: 1200,
  avg_agent_latency: null,
  avg_latency_ms: 850,
  overall_score: 3.46,
  evaluations: [
    { id: "ev-1", name: "Politeness", score: 0.9, passed: true, reason: "ok" },
  ],
  eval_metrics: {
    "ev-2": { name: "Accuracy", value: "Failed", type: "pass_fail" },
  },
};

describe("taskFromCallDetail", () => {
  it("returns null without a payload", () => {
    expect(taskFromCallDetail(undefined)).toBeNull();
  });

  it("maps a chat call's detail to the drawer task the table would hand it", () => {
    const task = taskFromCallDetail(CHAT_DETAIL);

    expect(task).toMatchObject({
      id: "call-chat-1",
      simulationCallType: "text",
      status: "failed",
      executionStatus: "completed",
      scenario: "refund-late · Trial 2",
      persona: "Impatient Ian",
      goal: "Get a refund",
      subGoals: ["Verify order"],
      provider: "hosted",
      durationMs: 42000,
      turns: 6,
      tokens: 1200,
      latencyMs: 850,
      csat: 3.5,
    });
  });

  it("keeps both live and stored eval verdicts", () => {
    const task = taskFromCallDetail(CHAT_DETAIL);

    expect(task.evalResults.map((result) => result.id)).toEqual([
      "ev-1",
      "ev-2",
    ]);
    expect(task.evalResults[0]).toMatchObject({
      name: "Politeness",
      passed: true,
    });
  });

  it("routes a voice call as voice", () => {
    const task = taskFromCallDetail({
      id: "call-voice-1",
      simulation_call_type: "voice",
      outcome: "passed",
      scenario: "Billing",
    });

    expect(task).toMatchObject({
      id: "call-voice-1",
      simulationCallType: "voice",
      status: "passed",
      scenario: "Billing",
    });
  });
});
