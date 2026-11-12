import { describe, expect, it } from "vitest";
import { canRerunHere, kindLabel, runKindOf } from "../runKind";
import { kindOfRun } from "src/sections/develop-detail/ExperimentTab/leaderboardModel";

// An agent graph run as the R1 run list (results_catalog.project_run) and the
// R3 summary send it: task_kind "graph", kind "run" / "execution" (not a run
// kind), source "native" (a string), no target_type.
const R1_AGENT = {
  run_id: "r-agent",
  task_kind: "graph",
  kind: "execution",
  source: "native",
  label: "#1",
  config: { model: { name: "gpt-4o-mini" } },
};
const R1_PROMPT = {
  run_id: "r-prompt",
  task_kind: "prompt",
  kind: "execution",
  source: "native",
  label: "#2",
};
const R1_EXTERNAL = {
  run_id: "r-external",
  task_kind: "external",
  kind: "run",
  source: "external",
};

describe("agent graph run kind", () => {
  it("reads task_kind graph as an agent run", () => {
    expect(runKindOf(R1_AGENT)).toBe("agent");
    expect(runKindOf({ ...R1_AGENT, kind: "run" })).toBe("agent");
    expect(kindLabel(runKindOf(R1_AGENT))).toBe("Agent");
  });

  it("names the agent run Agent on the Experiments tab, not Prompt", () => {
    expect(kindOfRun(R1_AGENT)).toBe("Agent");
    expect(kindOfRun(R1_PROMPT)).toBe("Prompt");
  });

  it("keeps prompt, http and other kinds as they were", () => {
    expect(runKindOf(R1_PROMPT)).toBe("prompt");
    expect(kindLabel(runKindOf(R1_PROMPT))).toBe("Prompt");
    expect(runKindOf({ run_id: "h", target_type: "http" })).toBe("http");
    expect(runKindOf({ run_id: "u", target_type: "external" })).toBe("upload");
    // An explicit kind still wins over task_kind.
    expect(
      runKindOf({ ...R1_AGENT, config: { kind: "code" }, task_kind: "graph" }),
    ).toBe("code");
    // task_kind external is not re-labelled here (its Rerun stays as before).
    expect(runKindOf(R1_EXTERNAL)).toBe("prompt");
  });

  it("an agent run can still be rerun here", () => {
    expect(canRerunHere(R1_AGENT)).toBe(true);
  });
});
