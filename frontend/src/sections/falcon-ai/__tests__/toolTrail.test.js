import { describe, it, expect } from "vitest";
import {
  alignToPlan,
  classifySteps,
  declaredSteps,
  formatElapsed,
  groupBlocks,
  humanize,
  pickTrajectory,
  planFor,
  slugFromMessage,
  trailSummary,
} from "../helpers/toolTrail";

// The real shape of one turn, taken from a run that produced a correct answer
// and rendered seven red cards while doing it.
const BUILD_TURN = [
  { call_id: "1", tool_name: "list_eval_templates", status: "completed" },
  {
    call_id: "2",
    tool_name: "search_docs",
    status: "error",
    result_summary: "Documentation search is currently unavailable.",
  },
  {
    call_id: "5",
    tool_name: "list_eval_tasks",
    status: "error",
    result_summary: "**Error:** Invalid parameters: project_id Field required",
  },
  { call_id: "6", tool_name: "list_eval_tasks", status: "completed" },
  {
    call_id: "7",
    tool_name: "list_eval_configs",
    status: "error",
    result_summary: "Tool 'list_eval_configs' not found. Did you mean: ...",
  },
  {
    call_id: "11",
    tool_name: "create_custom_eval_config",
    status: "error",
    result_summary: "**Error:** Invalid parameters: name Field required",
  },
  { call_id: "12", tool_name: "create_custom_eval_config", status: "completed" },
];

describe("classifySteps", () => {
  it("calls a failure a retry when the same tool succeeds later", () => {
    const byId = Object.fromEntries(
      classifySteps(BUILD_TURN).map((s) => [s.call_id, s.outcome]),
    );
    expect(byId["5"]).toBe("retried");
    expect(byId["11"]).toBe("retried");
  });

  it("calls a name the agent invented a retry, never a failure", () => {
    const byId = Object.fromEntries(
      classifySteps(BUILD_TURN).map((s) => [s.call_id, s.outcome]),
    );
    expect(byId["7"]).toBe("retried");
  });

  it("keeps a real failure a failure", () => {
    const byId = Object.fromEntries(
      classifySteps(BUILD_TURN).map((s) => [s.call_id, s.outcome]),
    );
    expect(byId["2"]).toBe("error");
  });

  it("does not read a later success backwards onto an earlier one", () => {
    const steps = classifySteps([
      { call_id: "a", tool_name: "get_trace", status: "completed" },
      {
        call_id: "b",
        tool_name: "get_trace",
        status: "error",
        result_summary: "not found",
      },
    ]);
    expect(steps[1].outcome).toBe("error");
  });
});

describe("trailSummary", () => {
  it("counts what the header has to say", () => {
    const s = trailSummary(classifySteps(BUILD_TURN));
    expect(s.total).toBe(7);
    expect(s.retried).toBe(3);
    expect(s.failed).toBe(1);
  });

  it("names the step in flight", () => {
    const s = trailSummary(
      classifySteps([
        { call_id: "a", tool_name: "get_project", status: "completed" },
        { call_id: "b", tool_name: "search_traces", status: "running" },
      ]),
    );
    expect(s.current.tool_name).toBe("search_traces");
  });
});

describe("groupBlocks", () => {
  it("collapses a run of tool calls into one trail", () => {
    const grouped = groupBlocks([
      { type: "text", id: "t1", content: "on it" },
      { type: "tool_call", id: "c1", toolCall: { call_id: "c1" } },
      { type: "tool_call", id: "c2", toolCall: { call_id: "c2" } },
      { type: "tool_call", id: "c3", toolCall: { call_id: "c3" } },
      { type: "text", id: "t2", content: "done" },
    ]);
    expect(grouped.map((b) => b.type)).toEqual(["text", "trail", "text"]);
    expect(grouped[1].toolCalls).toHaveLength(3);
  });

  it("starts a new trail after text comes between", () => {
    const grouped = groupBlocks([
      { type: "tool_call", id: "c1", toolCall: { call_id: "c1" } },
      { type: "text", id: "t1", content: "found it" },
      { type: "tool_call", id: "c2", toolCall: { call_id: "c2" } },
    ]);
    expect(grouped.map((b) => b.type)).toEqual(["trail", "text", "trail"]);
  });

  it("leaves a message with no tool calls alone", () => {
    const blocks = [{ type: "text", id: "t1", content: "hey" }];
    expect(groupBlocks(blocks)).toEqual(blocks);
  });
});

describe("formatElapsed", () => {
  it("never says zero", () => {
    expect(formatElapsed(120)).toBe("1s");
  });

  it("reads in minutes past a minute", () => {
    expect(formatElapsed(536000)).toBe("8m 56s");
    expect(formatElapsed(120000)).toBe("2m");
  });
});

describe("humanize", () => {
  it("reads as an activity, not an identifier", () => {
    expect(humanize("search_trace_spans")).toBe("Searching trace spans");
    expect(humanize("create_custom_eval_config")).toBe(
      "Creating custom eval config",
    );
    expect(humanize("list_projects")).toBe("Listing projects");
  });

  it("falls back to the name with the underscores taken out", () => {
    expect(humanize("whoami")).toBe("whoami");
    expect(humanize("fix_with_falcon")).toBe("fix with falcon");
  });

  it("survives an empty name", () => {
    expect(humanize("")).toBe("Working");
    expect(humanize()).toBe("Working");
  });
});

// The flow the eval-build skill declares, trimmed to its tool names. Read off
// the real skill so the alignment is tested against what ships, not a fixture
// invented to pass.
const EVAL_BUILD = {
  user: "Go ahead and build the four evals you recommended.",
  steps: [
    { tool: "get_project" },
    { tool: "get_eval_template_by_name" },
    { tool: "get_project_eval_attributes" },
    { tool: "get_trace_spans_by_type" },
    { tool: "read_trace_span" },
    { tool: "test_eval_template" },
    { tool: "check_eval_config_exists" },
    { tool: "create_custom_eval_config" },
    { tool: "create_eval_task" },
    { tool: "get_eval_task" },
    { tool: "get_eval_task_logs" },
  ],
};

const PLAN = declaredSteps(EVAL_BUILD);

const call = (tool, i) => ({ call_id: `c${i}`, tool_name: tool });

describe("declaredSteps", () => {
  it("reads the ordered tool names off a declared trajectory", () => {
    expect(PLAN).toHaveLength(11);
    expect(PLAN[0]).toBe("get_project");
    expect(PLAN[10]).toBe("get_eval_task_logs");
  });

  it("survives a skill that declares nothing", () => {
    expect(declaredSteps(null)).toEqual([]);
    expect(declaredSteps({ steps: [{ params: {} }] })).toEqual([]);
  });
});

describe("pickTrajectory", () => {
  const short = { steps: [{ tool: "analyze_project_traces" }] };
  const long = {
    steps: [
      { tool: "explore_trace_legacy" },
      { tool: "read_trace_span" },
      { tool: "submit_trace_finding" },
      { tool: "submit_trace_scores" },
    ],
  };

  it("picks the flow that explains what actually ran", () => {
    const picked = pickTrajectory(
      [short, long],
      [call("explore_trace_legacy", 1), call("read_trace_span", 2)],
    );
    expect(picked).toBe(long);
  });

  it("picks the fullest flow before anything has run", () => {
    expect(pickTrajectory([short, long], [])).toBe(long);
  });

  it("has no flow when the skill declares none", () => {
    expect(pickTrajectory([], [])).toBeNull();
    expect(pickTrajectory([{ steps: [] }], [])).toBeNull();
    expect(planFor([], [])).toEqual([]);
  });
});

describe("alignToPlan", () => {
  it("says which declared step each call is", () => {
    const { steps, done, planned } = alignToPlan(
      [
        call("get_project", 1),
        call("get_eval_template_by_name", 2),
        call("get_project_eval_attributes", 3),
      ],
      PLAN,
    );
    expect(steps.map((s) => s.planIndex)).toEqual([0, 1, 2]);
    expect(steps.every((s) => s.planKind === "plan")).toBe(true);
    expect(done).toBe(3);
    expect(planned).toBe(11);
  });

  it("keeps a tool that is not in the flow, and calls it extra", () => {
    const { steps, extra, done } = alignToPlan(
      [
        call("get_project", 1),
        call("search_docs", 2),
        call("read_trace_span", 3),
      ],
      PLAN,
    );
    expect(steps).toHaveLength(3);
    expect(steps[1].planKind).toBe("extra");
    expect(steps[1].planIndex).toBeNull();
    expect(extra).toBe(1);
    expect(done).toBe(2);
  });

  it("holds one declared step while the tool repeats", () => {
    const { steps, done, extra } = alignToPlan(
      [
        call("get_project", 1),
        call("read_trace_span", 2),
        call("read_trace_span", 3),
        call("read_trace_span", 4),
      ],
      PLAN,
    );
    expect(steps.map((s) => s.planIndex)).toEqual([0, 4, 4, 4]);
    expect(steps.map((s) => s.planKind)).toEqual([
      "plan",
      "plan",
      "revisit",
      "revisit",
    ]);
    expect(done).toBe(2);
    expect(extra).toBe(0);
  });

  it("credits a declared step taken out of order without going backwards", () => {
    const { steps, done } = alignToPlan(
      [
        call("get_project", 1),
        call("get_project_eval_attributes", 2),
        call("get_eval_template_by_name", 3),
      ],
      PLAN,
    );
    expect(steps.map((s) => s.planIndex)).toEqual([0, 2, 1]);
    expect(steps[2].planKind).toBe("revisit");
    expect(done).toBe(3);
  });

  it("names the declared steps the run never reached", () => {
    const { pending, done } = alignToPlan(
      [call("get_project", 1), call("get_eval_template_by_name", 2)],
      PLAN,
    );
    expect(done).toBe(2);
    expect(pending).toHaveLength(9);
    expect(pending[0]).toEqual({
      index: 2,
      tool: "get_project_eval_attributes",
    });
  });

  it("counts a jumped step as not run", () => {
    const { done, pending } = alignToPlan(
      [call("get_project", 1), call("get_project_eval_attributes", 2)],
      PLAN,
    );
    expect(done).toBe(2);
    expect(pending.map((p) => p.index)).toContain(1);
  });

  it("leaves the run untouched when there is no flow", () => {
    const calls = [call("get_project", 1)];
    const out = alignToPlan(calls, []);
    expect(out.steps).toBe(calls);
    expect(out.planned).toBe(0);
    expect(out.done).toBe(0);
  });
});

describe("slugFromMessage", () => {
  it("reads the skill the turn ran from the message that triggered it", () => {
    expect(slugFromMessage("/eval-build go")).toBe("eval-build");
    expect(slugFromMessage("/eval-build")).toBe("eval-build");
  });

  it("finds no skill in a plain message", () => {
    expect(slugFromMessage("build me four evals")).toBeNull();
    expect(slugFromMessage("please run /eval-build")).toBeNull();
    expect(slugFromMessage("/")).toBeNull();
    expect(slugFromMessage()).toBeNull();
  });
});
