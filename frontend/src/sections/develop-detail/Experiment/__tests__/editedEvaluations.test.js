// An Edit save scores a kept evaluation again on every run when it changes
// what decides its scores (the fields dev compares before scoring it again:
// template, name, mapping, settings, Choices scores, model, error localizer,
// knowledge base, version pin). The form tells it from the saved id. An
// evaluation the save sends with no id is added: dev creates and scores it.
// The summary also names the kept and re-run agent runs.
import { describe, expect, it } from "vitest";
import {
  addedEvaluationsOf,
  editChangeSummary,
  editRunScope,
  editedEvaluationsOf,
  valuesFromExperiment,
} from "../experimentFormValues";
import { flattenRuns } from "../runPlan";

const SAVED_ID = "7c9e6679-7425-40de-944b-e07fc1f90ae7";
const tone = {
  actualEvalCreatedId: SAVED_ID,
  id: SAVED_ID,
  templateId: "1b4e28ba-2fa1-41d2-883f-0016d3cca427",
  name: "Tone",
  config: {
    mapping: { output: "col-answer" },
    config: { criteria: "Is the answer polite?" },
    reason_column: true,
    version_pin: "v2",
  },
  model: "turing_small",
};
const edit = (changes, config = {}) => ({
  ...tone,
  ...changes,
  config: { ...tone.config, ...config },
});
const noRuns = { runs: [], flatten: () => [] };

describe("editedEvaluationsOf", () => {
  it("finds none when the evaluation is unchanged, whatever its key order", () => {
    const same = {
      model: "turing_small",
      config: {
        version_pin: "v2",
        reason_column: true,
        config: { criteria: "Is the answer polite?" },
        mapping: { output: "col-answer" },
      },
      name: "Tone",
      templateId: tone.templateId,
      id: SAVED_ID,
      actualEvalCreatedId: SAVED_ID,
    };
    expect(editedEvaluationsOf([tone], [same])).toEqual([]);
  });

  it.each([
    ["instructions", edit({}, { config: { criteria: "Is it short?" } })],
    ["model", edit({ model: "turing_large" })],
    ["mapping", edit({}, { mapping: { output: "col-other" } })],
    ["version pin", edit({}, { version_pin: "v3" })],
    [
      "Choices scores",
      edit({}, { run_config: { choice_scores: { Yes: 1, No: 0.5 } } }),
    ],
    ["error localizer", edit({ errorLocalizer: true })],
    ["knowledge base", edit({ kbId: "kb-1" })],
    ["template", edit({ templateId: "9d3e1a5b-0000-4000-8000-000000000002" })],
    ["name", edit({ name: "Politeness" })],
  ])("finds the evaluation when its %s changes", (_, changed) => {
    expect(editedEvaluationsOf([tone], [changed])).toEqual([changed.name]);
  });

  it("does not count a setting dev does not compare", () => {
    const changed = edit({}, { reason_column: false });
    expect(editedEvaluationsOf([tone], [changed])).toEqual([]);
  });

  it("does not count added or removed evaluations as edited", () => {
    const added = { ...tone, actualEvalCreatedId: undefined, name: "Brevity" };
    expect(editedEvaluationsOf([tone], [tone, added])).toEqual([]);
    expect(editedEvaluationsOf([tone], [])).toEqual([]);
  });
});

describe("the Edit summary names edited evaluations", () => {
  it("lists the edited evaluation for the confirm and the footer", () => {
    const original = { promptConfig: [], userEvalMetrics: [tone] };
    const current = {
      promptConfig: [],
      userEvalMetrics: [edit({}, { config: { criteria: "Is it short?" } })],
    };

    const summary = editChangeSummary(original, current, noRuns);
    const scope = editRunScope(original, current, noRuns);

    expect(summary.evalsEdited).toEqual(["Tone"]);
    expect(scope.evalsEdited).toEqual(["Tone"]);
  });

  it("lists none for a save that leaves the evaluations alone", () => {
    const original = { promptConfig: [], userEvalMetrics: [tone] };
    const current = { promptConfig: [], userEvalMetrics: [{ ...tone }] };

    const summary = editChangeSummary(original, current, noRuns);

    expect(summary.evalsEdited).toEqual([]);
  });
});

describe("the Edit summary names added evaluations", () => {
  const brevity = {
    templateId: "9d3e1a5b-0000-4000-8000-000000000002",
    name: "Brevity",
    config: { mapping: { output: "col-answer" } },
  };

  it("counts an evaluation with no saved id as added", () => {
    expect(addedEvaluationsOf([tone, brevity])).toEqual(["Brevity"]);
    expect(addedEvaluationsOf([tone])).toEqual([]);
  });

  it("lists the added evaluation for the confirm and the footer", () => {
    const original = { promptConfig: [], userEvalMetrics: [tone] };
    const current = { promptConfig: [], userEvalMetrics: [tone, brevity] };

    const summary = editChangeSummary(original, current, noRuns);
    const scope = editRunScope(original, current, noRuns);

    expect(summary.evalsNew).toEqual(["Brevity"]);
    expect(summary.evalsEdited).toEqual([]);
    expect(scope.evalsNew).toEqual(["Brevity"]);
  });
});

describe("the Edit summary names the agent runs", () => {
  const detail = {
    name: "e",
    user_eval_metrics: [],
    runs: [
      {
        run_id: "RA",
        label: "#4A",
        letter: "A",
        experiment_ordinal: 4,
        target_type: "agent",
        target: { graph_id: "G", version_id: "GV3", version_name: "v3" },
      },
      {
        run_id: "RB",
        label: "#4B",
        letter: "B",
        experiment_ordinal: 4,
        target_type: "prompt",
        target: {
          prompt_id: "P",
          prompt_version_id: "PV3",
          prompt_version_name: "V3",
          model: "gpt-4o",
          model_params: { temperature: 0.2 },
        },
      },
    ],
  };
  const summarise = (edit) => {
    const original = valuesFromExperiment(detail, { mode: "edit" });
    const current = structuredClone(original);
    edit(current);
    const options = { runs: detail.runs, flatten: flattenRuns };
    return {
      summary: editChangeSummary(original, current, options),
      scope: editRunScope(original, current, options),
    };
  };

  it("names the kept agent run when a prompt run re-runs", () => {
    const { summary, scope } = summarise((c) => {
      const p = c.promptConfig[1];
      p.promptVersion = "PV2";
      p.versionLabel = "V2";
      p.model[0].value = "gpt-4.1";
      p.modelParams = { "gpt-4.1": { temperature: 0.7 } };
    });

    expect(summary.rerun).toEqual(["#4B"]);
    expect(summary.keptAgents).toEqual(["#4A"]);
    expect(summary.rerunAgents).toEqual([]);
    expect(scope.keptAgents).toEqual(["#4A"]);
    expect(scope.rerunAgents).toEqual([]);
  });

  it("names the re-run agent run", () => {
    const { summary } = summarise((c) => {
      c.promptConfig[0].agentVersion = "GV2";
      c.promptConfig[0].versionLabel = "v2";
    });

    expect(summary.rerun).toEqual(["#4A"]);
    expect(summary.rerunAgents).toEqual(["#4A"]);
    expect(summary.keptAgents).toEqual([]);
  });
});
