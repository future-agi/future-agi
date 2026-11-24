import { describe, expect, it } from "vitest";
import {
  keptLine,
  phaseRows,
  stepOfStage,
  stopCase,
  stopSentence,
  stoppedStep,
  tabWaits,
} from "../buildSteps";

describe("stepOfStage", () => {
  it("numbers stages in the order the build runs them", () => {
    expect(
      [
        "generating_environment",
        "generating_scenarios",
        "validating_environment",
        "validating_scenarios",
        "connecting_agent",
      ].map((stage) => stepOfStage(stage).no),
    ).toEqual([2, 5, 6, 7, 8]);
  });

  it("starts a build that reads the agent at step 1 and an approved one at step 2", () => {
    expect(stepOfStage("queued").no).toBe(1);
    expect(stepOfStage("queued", { readsAgent: false }).no).toBe(2);
  });
});

describe("stoppedStep", () => {
  it("names the stage a failure records", () => {
    const step = stoppedStep({
      stage: "failed",
      failure: { stage: "validating_environment" },
    });
    expect(step.label).toBe("Validating environment");
  });

  it("places a cancel by what the build already produced", () => {
    const step = stoppedStep(
      { stage: "canceled" },
      { outputs: [{ kind: "contract" }, { kind: "environment" }] },
    );
    expect(step.label).toBe("Generating scenarios");
  });
});

describe("phaseRows", () => {
  it("keeps finished phases, stops at one and leaves the rest not run", () => {
    const rows = phaseRows(stepOfStage("validating_environment"), {
      stopped: true,
      counts: { tools: 3, tables: 2 },
    });
    expect(rows.map((row) => [row.label, row.state, row.note])).toEqual([
      ["Writing the world", "done", "Kept · 3 tools · 2 tables"],
      [
        "Writing and checking scenarios",
        "stopped",
        "Stopped at Validating environment · 1 of 3 steps done",
      ],
      ["Running the release checks", "next", "Not run"],
    ]);
  });
});

describe("the stopped build's words", () => {
  it("names the tool that failed certification", () => {
    const status = {
      stage: "failed",
      failure: {
        domain: "environment",
        stage: "validating_environment",
        message: "Tool contract certification rejected: estimate_eta",
      },
    };
    expect(stopSentence(status, stoppedStep(status))).toBe(
      "estimate_eta didn't answer truthfully, so the world wasn't finished.",
    );
  });

  it("tells an unreachable agent from another release-check stop", () => {
    const connecting = stepOfStage("connecting_agent");
    const grading = stepOfStage("grading");
    expect(stopCase({ stage: "failed", failure: {} }, connecting)).toBe(
      "checks",
    );
    expect(stopCase({ stage: "failed", failure: {} }, grading)).toBe(
      "checksOther",
    );
  });

  it("says nothing was kept only when nothing was", () => {
    expect(keptLine(stepOfStage("generating_environment"))).toBe("");
    expect(
      keptLine(stepOfStage("connecting_agent"), { tables: 2, scenarios: 3 }),
    ).toBe("The world and its 2 tables are kept, and its 3 scenarios too.");
  });
});

describe("tabWaits", () => {
  it("holds each tab until the build has written its part", () => {
    expect(Object.keys(tabWaits(stepOfStage("generating_scenarios")))).toEqual([
      "actors",
      "scenarios",
      "evals",
    ]);
    expect(tabWaits(stepOfStage("connecting_agent"))).toEqual({});
  });
});
