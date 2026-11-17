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
      /wireExperimentBody\(\s*withHttpSourceSettings\(\s*experimentBody\(data, getValues\(\), \{ isEditing, datasetId \}\),\s*getValues\("promptConfig"\),\s*serverCtx,\s*\),\s*serverCtx,\s*\)/,
    );
    expect(SRC).not.toMatch(/experimentExtras\(/);
    expect(SRC).toMatch(
      /const wire = bodyOf\(data\);\s*if \(blockedSubmit\(wire\.blocks\)\) return;\s*create\.mutate\(wire\.body,/,
    );
    expect(SRC).toMatch(
      /const wire = bodyOf\(data\);\s*if \(blockedSubmit\(wire\.blocks\)\) return;\s*setConfirmEdit\(\{\s*summary,\s*\.\.\.editSavePlan\(wire, \{ original, current: getValues\(\), datasetId \}\),\s*\}\);/,
    );
  });

  it("exp.rename: an edit saves the name and description on their own first", () => {
    // Dev refuses a body that mixes them with run settings. editSave.js
    // runEditSave runs the two saves in order (its behaviour is run in
    // exp-rename-edit-refusal.test.jsx).
    expect(SRC).toMatch(
      /const outcome = await runEditSave\(plan, \{\s*saveMetadata: saveNameAndDescription,\s*saveSettings,\s*\}\);/,
    );
    expect(SRC).toMatch(
      /update\.mutate\(body, \{ onSuccess: resolve, onError: reject \}\)/,
    );
    expect(SRC).toMatch(
      /saveMetadata\.mutate\(body, \{ onSuccess: resolve, onError: reject \}\)/,
    );
    // A refused name or description is said under its field.
    expect(SRC).toMatch(
      /if \(renameFieldErrors\(failure, setError\)\) \{\s*setConfirmEdit\(null\);\s*setCurrentStep\(EXPERIMENT_STEP\.CONFIGURATION\);\s*return;\s*\}/,
    );
    // A saved rename is kept: a retry sends only the run settings, and a
    // failed settings save says the name was saved.
    expect(SRC).toMatch(
      /setConfirmEdit\(\(c\) => \(c \? \{ \.\.\.c, metadata: null \} : c\)\);/,
    );
    expect(SRC).toMatch(/settingsErrorText\(outcome\.failed\.error, kept\)/);
    // The Save label: a rename alone says Save; an HTTP edit and a run
    // change keep their own labels (one nested choice since S1b).
    expect(SRC).toMatch(/renameOnly\s*\?\s*"Save"\s*:/);
    expect(SRC).toMatch(/:\s*"Save & run changes"\}/);
  });

  it("exp.rename: Edit checks a changed name, never its own", () => {
    expect(SRC).toMatch(
      /useExperimentNameCheck\(datasetId, debouncedName, \{\s*enabled: nameChecked,\s*\}\)/,
    );
    expect(SRC).toMatch(/const nameUnavailable =\s*nameChecked &&/);
    // After a partial save the saved name is its own (and is not sent
    // again); a name sent without an answer is not called taken.
    expect(SRC).toMatch(
      /initial: savedMeta \? \{ \.\.\.initialValues, \.\.\.savedMeta \} : initialValues,/,
    );
    expect(SRC).toMatch(
      /setSavedMeta\(\(s\) => \(\{ \.\.\.s, \.\.\.plan\.metadata \}\)\);/,
    );
    expect(SRC).toMatch(/setUnconfirmedName\(plan\.metadata\.name\);/);
  });

  it("merges the server blocks into the step issues every render", () => {
    expect(SRC).toMatch(
      /const serverBlocks = wireExperimentBody\(\s*withHttpSourceSettings\(\s*experimentBodyOfValues\(values, \{ isEditing, datasetId \}\),\s*values\.promptConfig,\s*serverCtx,\s*\),\s*serverCtx,\s*\)\.blocks;/,
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
    const wire = wireExperimentBody(
      experimentBodyOfValues(base, { datasetId: "d" }),
      { columns },
      false,
    );
    expect(wire.blocks).toEqual([]);
    expect(wire.body).not.toHaveProperty("error_policy");
    expect(wire.body).not.toHaveProperty("metadata");
    const issues = issuesOf(base);
    expect(issues[EXPERIMENT_STEP.REVIEW]).toEqual([]);
    expect(issues.flat().join(" ")).not.toMatch(/(isn't|aren't) supported/);
  });

  it("a chosen error policy travels and does not block Review", () => {
    for (const errorPolicy of ["zero", "exclude"]) {
      expect(
        issuesOf({ ...base, errorPolicy })[EXPERIMENT_STEP.REVIEW],
      ).toEqual([]);
      const wire = wireExperimentBody(
        experimentBodyOfValues({ ...base, errorPolicy }, { datasetId: "d" }),
        { columns },
        false,
      );
      expect(wire.body.error_policy).toBe(errorPolicy);
    }
    expect(
      issuesOf({ ...base, errorPolicy: "exclude" }, true)[EXPERIMENT_STEP.REVIEW],
    ).toEqual([]);
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
    expect(serverBlockOnStep(blocks, EXPERIMENT_STEP.DATA, n)?.text).toBe(
      "Experiments fill {{question}} only from a column named “question”. Pick “question” for {{question}}, or rename {{question}} to {{prompt}} in the prompt.",
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
      /^Experiments fill \{\{question\}\} only from a column named “question”\. Pick “question” for \{\{question\}\}/,
    );
    expect(issues.flat().join(" ")).not.toMatch(/template path/);
  });

  it("a variable with no column of its name says to rename it or add the column", () => {
    // What a person saw: {{name}} picked column `input`, then a dead end.
    const walkColumns = [
      { id: "c-input", name: "input" },
      { id: "c-expected", name: "expected_output" },
    ];
    const values = {
      ...base,
      promptConfig: [
        { ...card, variables: ["name"], variableMapping: { name: "c-input" } },
      ],
    };
    const blocks = wireExperimentBody(
      experimentBodyOfValues(values, { isEditing: false, datasetId: "d" }),
      { isEditing: false, initial: {}, columns: walkColumns },
      false,
    ).blocks;
    const n = Object.keys(EXPERIMENT_STEP).length;
    expect(serverBlockOnStep(blocks, EXPERIMENT_STEP.DATA, n)?.text).toBe(
      "Experiments fill {{name}} only from a column named “name”. Rename {{name}} to {{input}} in the prompt, or add a column named “name” to the dataset.",
    );
  });
});


describe("HTTP source wiring", () => {
  it("uses the same HTTP settings for live blocks and submission", () => {
    expect(SRC).toContain("withHttpSourceSettings");
    expect(SRC).toContain("hydrateHttpSourceValues");
    expect(SRC).toContain("sourceDetail?.http_configs?.length");
  });

  it("SRC-S1b-HTTP-FE-01 compares saved HTTP sources with the form's columns", () => {
    // Both the live blocks and the submission keep an unchanged source as read.
    expect(SRC).toMatch(/values\.promptConfig,\s*serverCtx,\s*\)/);
    expect(SRC).toMatch(/getValues\("promptConfig"\),\s*serverCtx,\s*\)/);
  });

  it("SRC-S1b-HTTP-FE-01 an HTTP Edit only saves, and says so", () => {
    expect(SRC).toMatch(
      /const httpEdit = isEditing && Boolean\(sourceDetail\?\.http_configs\?\.length\);/,
    );
    expect(SRC).toMatch(/httpEdit: httpEdit\s*\?\s*httpEditChanges\(initialValues, values,/);
    expect(SRC).toMatch(/httpEdit\s*\?\s*HTTP_RESEND_COPY\.editSaveOnly\s*:\s*editKeptLine\(/);
    expect(SRC).toMatch(
      /httpEdit\s*\?\s*HTTP_RESEND_COPY\.editSave\s*:\s*"Save & run changes"/,
    );
    expect(SRC).toMatch(/httpEditSummaryText\(confirmEdit\?\.summary\?\.text\)/);
    // The Review summary of an HTTP Edit save runs nothing.
    expect(SRC).toMatch(/editRunsScope && httpEdit\s*\?\s*\{ \.\.\.editRunsScope, promptConfig: \[\] \}/);
  });

  it("lifts the wizard server block for HTTP only", () => {
    const body = { name: "HTTP run", prompt_config: [{ target_type: "http", http: {
      url: "https://fixture.example/run", method: "POST", body_template: "{}", output_path: "$.reply",
    } }], user_eval_metrics: [] };
    expect(wireExperimentBody(body, {}, false).blocks).toEqual([]);
    const other = { ...body, prompt_config: [{ target_type: "code", code: { mode: "upload" } }] };
    expect(wireExperimentBody(other, {}, false).blocks.map((b) => b.field)).toContain("prompt_config[].target_type");
  });
});
