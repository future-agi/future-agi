import { describe, expect, it } from "vitest";
import {
  keptLine,
  landedOf,
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

  it("gives the release checks and finalizing a step", () => {
    expect(
      [
        "running",
        "grading",
        "finalizing",
        "uploading_artifacts",
        "cleaning_up",
      ].map((stage) => stepOfStage(stage).no),
    ).toEqual([9, 10, 11, 11, 12]);
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

  it("leaves out the step count while no step of the phase is done", () => {
    const rows = phaseRows(stepOfStage("generating_scenarios"));
    expect(rows.find((row) => row.id === "scenarios").note).toBe(
      "Now: Generating scenarios",
    );
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

  it("counts the tools only when there are some", () => {
    const step = stepOfStage("generating_scenarios");
    expect(keptLine(step, { tools: 3 })).toBe(
      "The world and its 3 tools are kept.",
    );
    expect(keptLine(step, { tools: 0 })).toBe("The world is kept.");
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

  it("holds the contract tab until the contract has landed, even past its step", () => {
    const step = stepOfStage("generating_environment", { readsAgent: false });
    const waits = tabWaits(step, { landed: { contract: false } });
    expect(waits.contract).toMatchObject({ now: true, step: step.label });
    expect(tabWaits(step, { landed: { contract: true } }).contract).toBe(
      undefined,
    );
  });

  it("holds World & tools past its step until the world's tables land", () => {
    const step = stepOfStage("generating_scenarios");
    const codeAgent = { agentMode: "environment_backed", stores: [] };
    expect(
      tabWaits(step, { landed: landedOf(codeAgent, step) }).world,
    ).toMatchObject({ now: true });
    const seeded = {
      ...codeAgent,
      stores: [{ tables: [{ name: "orders", rows: 4 }] }],
    };
    expect(tabWaits(step, { landed: landedOf(seeded, step) }).world).toBe(
      undefined,
    );
    const validated = stepOfStage("validating_scenarios");
    expect(
      tabWaits(validated, { landed: landedOf(codeAgent, validated) }).world,
    ).toBe(undefined);
  });

  it("never holds World & tools for a connect-only agent past its step", () => {
    const step = stepOfStage("generating_scenarios");
    const hosted = {
      agentMode: "connect_only",
      stores: [{ tables: [{ name: "harness_calls", internal: true }] }],
    };
    expect(tabWaits(step, { landed: landedOf(hosted, step) }).world).toBe(
      undefined,
    );
  });

  it("does not count the harness's own tables as the world", () => {
    const step = stepOfStage("generating_scenarios");
    const env = {
      agentMode: "environment_backed",
      stores: [{ tables: [{ name: "harness_calls", internal: true }] }],
    };
    expect(landedOf(env, step).world).toBe(false);
  });

  it("shows the stopped note on World & tools for a build stopped before the world was sealed", () => {
    const step = stepOfStage("validating_environment");
    const waits = tabWaits(step, {
      stopped: true,
      reason: "It stopped.",
      landed: landedOf({ agentMode: "environment_backed" }, step),
    });
    expect(waits.world).toMatchObject({ stopped: true, now: false });
  });

  it("holds Versions until the build saves a version", () => {
    const step = stepOfStage("generating_scenarios");
    expect(
      tabWaits(step, { landed: landedOf({}, step, { versions: false }) })
        .versions,
    ).toMatchObject({ phase: "Running the release checks", now: false });
    expect(
      tabWaits(step, { landed: landedOf({}, step, { versions: true }) })
        .versions,
    ).toBe(undefined);
    expect(tabWaits(step).versions).toBe(undefined);
  });
});
