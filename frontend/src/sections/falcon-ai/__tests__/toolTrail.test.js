import { describe, it, expect } from "vitest";
import {
  classifySteps,
  formatElapsed,
  groupBlocks,
  humanize,
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
