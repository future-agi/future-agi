// L2: RunExperiment sends what serverBody.js says dev reads, and a setting dev
// cannot honour blocks Run (or Save) on its own step. The drawer is too wide
// to mount here (the real-stack walk clicks it on a non-prototype build);
// this pins the wiring in the source and runs the same pure path the drawer
// runs on every render (experimentBodyOfValues -> wireExperimentBody ->
// withServerBlocks).
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { experimentBodyOfValues } from "../experimentBody";
import {
  serverBlockOnStep,
  wireExperimentBody,
  withServerBlocks,
} from "../serverBody";
import { stepIssues } from "../stepIssues";
import { emptyExperimentValues } from "../experimentFormValues";
import { EXPERIMENT_STEP } from "../constants";

const SRC = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), "..", "RunExperiment.jsx"),
  "utf8",
);

describe("RunExperiment wiring (source)", () => {
  it("builds the body in experimentBody.js and sends the wire body only", () => {
    expect(SRC).toMatch(
      /wireExperimentBody\(\s*experimentBody\(data, getValues\(\), \{ isEditing, datasetId \}\),\s*serverCtx,\s*\)/,
    );
    expect(SRC).not.toMatch(/experimentExtras\(/);
    expect(SRC).toMatch(
      /const wire = bodyOf\(data\);\s*if \(blockedSubmit\(wire\.blocks\)\) return;\s*create\.mutate\(wire\.body,/,
    );
    expect(SRC).toMatch(
      /const wire = bodyOf\(data\);\s*if \(blockedSubmit\(wire\.blocks\)\) return;\s*setConfirmEdit\(\{ body: wire\.body, summary \}\);/,
    );
  });

  it("merges the server blocks into the step issues every render", () => {
    expect(SRC).toMatch(
      /const serverBlocks = wireExperimentBody\(\s*experimentBodyOfValues\(values, \{ isEditing, datasetId \}\),\s*serverCtx,\s*\)\.blocks;/,
    );
    expect(SRC).toMatch(/const issues = withServerBlocks\(\s*stepIssues\(/);
    // A Review-step block disables Run with its reason.
    expect(SRC).toMatch(/reviewServerBlock\?\.text/);
    expect(SRC).toMatch(/data-testid="server-blocked"/);
  });

  it("an earlier step's block shows on that step as text, not only in a tooltip", () => {
    // Regression: the Data step's
    // reason was only in the disabled Continue's tooltip.
    expect(SRC).toMatch(
      /const stepServerBlock = isLastStep\s*\?\s*reviewServerBlock\s*:\s*serverBlockOnStep\(\s*serverBlocks,\s*currentStep,/,
    );
    expect(SRC).toMatch(
      /earlierBlock === -1 && stepServerBlock && !stepIssuesNow\.length/,
    );
    expect(SRC).toMatch(/`Not ready: \$\{stepServerBlock\.text\}`/);
    expect(SRC).not.toMatch(/`Not ready: \$\{reviewServerBlock\.text\}`/);
  });

  it("Undo deletes with DELETE and the toast never reads #null", () => {
    expect(SRC).toMatch(
      /axios\.delete\(\s*endpoints\.develop\.experiment\.delete,\s*\{\s*data: \{ experiment_ids: \[expId\] \},/,
    );
    expect(SRC).not.toMatch(
      /axios\.post\(endpoints\.develop\.experiment\.delete/,
    );
    expect(SRC).not.toMatch(
      /`Experiment #\$\{ordinal\} (started|created|cancelled)/,
    );
  });
});

describe("the drawer's per-render path", () => {
  const columns = [
    { id: "c-question", name: "question" },
    { id: "c-prompt", name: "prompt" },
  ];
  const card = {
    experimentType: "llm",
    targetType: "prompt",
    promptId: "1b2c3d4e-0000-4000-8000-000000000001",
    promptVersion: "1b2c3d4e-0000-4000-8000-000000000002",
    model: [{ value: "gpt-4o-mini" }],
    modelParams: {},
    configuration: { toolChoice: "auto", tools: [] },
    variables: ["question"],
    variableMapping: { question: "c-question" },
  };
  const issuesOf = (values, prototype = false) =>
    withServerBlocks(
      stepIssues(values, { nameTaken: false, columns, isEditing: false }),
      wireExperimentBody(
        experimentBodyOfValues(values, { datasetId: "d" }),
        { columns },
        prototype,
      ).blocks,
    );
  const base = {
    ...emptyExperimentValues(),
    name: "Exp",
    promptConfig: [card],
    userEvalMetrics: [{ templateId: "t" }],
  };

  it("a form dev can run has no server issue", () => {
    const issues = issuesOf(base);
    expect(issues[EXPERIMENT_STEP.REVIEW]).toEqual([]);
    expect(issues.flat().join(" ")).not.toMatch(/(isn't|aren't) supported/);
  });

  it("Trials 3 blocks Run on Review with its reason; prototype mode does not", () => {
    const issues = issuesOf({ ...base, trials: 3 });
    expect(issues[EXPERIMENT_STEP.REVIEW]).toContain(
      "3 tries per row aren't supported here: each row runs once. Set Trials to 1 to run.",
    );
    expect(
      issuesOf({ ...base, trials: 3 }, true)[EXPERIMENT_STEP.REVIEW],
    ).toEqual([]);
  });

  it("the Data step finds its own block to show; other steps do not", () => {
    const values = {
      ...base,
      promptConfig: [{ ...card, variableMapping: { question: "c-prompt" } }],
    };
    const blocks = wireExperimentBody(
      experimentBodyOfValues(values, { isEditing: false, datasetId: "d" }),
      { isEditing: false, initial: {}, columns },
      false,
    ).blocks;
    const n = Object.keys(EXPERIMENT_STEP).length;
    expect(serverBlockOnStep(blocks, EXPERIMENT_STEP.DATA, n)?.text).toMatch(
      /^\{\{question\}\} reads column “prompt”\./,
    );
    expect(serverBlockOnStep(blocks, EXPERIMENT_STEP.CONFIGURATION, n)).toBe(
      null,
    );
    expect(serverBlockOnStep([], EXPERIMENT_STEP.DATA, n)).toBe(null);
  });

  it("a remapped variable blocks on Data", () => {
    const issues = issuesOf({
      ...base,
      promptConfig: [{ ...card, variableMapping: { question: "c-prompt" } }],
    });
    expect(issues[EXPERIMENT_STEP.DATA].join(" ")).toMatch(
      /^\{\{question\}\} reads column “prompt”\./,
    );
  });
});
