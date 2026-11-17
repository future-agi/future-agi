/* eslint-disable react/prop-types */
// The Edit footer's running total for a save that changes scores: an
// evaluation edited alone, an evaluation added, or one edited and another
// added shows that save's sentence alone; an evaluation edited with a re-run
// prompt and a kept agent shows the same edited-evaluation sentence, then the
// re-run estimate as the total showed it, then what that estimate leaves out.
// Any other save that starts runs while an evaluation is edited or added and
// another run is kept shows its estimate, then the same words. A save that
// scores nothing, and any save of an experiment with an HTTP source, keeps
// today's text.
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { useForm } from "react-hook-form";
import { ExperimentFormContext } from "../ExperimentFormContext";
import { SimpleEstimate } from "../CostEstimateLine";
import { PROMPT_CONFIG_TYPE } from "../constants";

const scopeState = vi.hoisted(() => ({ override: null }));
vi.mock("../experimentFormValues", async (original) => {
  const real = await original();
  return {
    ...real,
    editRunScope: (...args) =>
      scopeState.override ?? real.editRunScope(...args),
  };
});
vi.mock("../useEngineCostInputs", () => ({
  useEngineCostInputs: (promptConfig) => ({ promptConfig, customPrices: {} }),
}));
vi.mock("src/api/develop/r2", async (original) => ({
  ...(await original()),
  useRunConfigs: () => ({ data: { results: [] } }),
}));
vi.mock("src/sections/common/Datasets/datasetKind", async (original) => ({
  ...(await original()),
  useDatasetKind: () => ({ kind: "dataset" }),
}));

const SAVED_ID = "7c9e6679-7425-40de-944b-e07fc1f90ae7";
const tone = {
  actualEvalCreatedId: SAVED_ID,
  id: SAVED_ID,
  templateId: "1b4e28ba-2fa1-41d2-883f-0016d3cca427",
  name: "Tone",
  config: {
    mapping: { output: "col-answer" },
    config: { criteria: "Is the answer polite?" },
  },
  model: "turing_small",
};
const editedTone = {
  ...tone,
  config: { ...tone.config, config: { criteria: "Is it short?" } },
};
const brevity = {
  templateId: "9d3e1a5b-0000-4000-8000-000000000002",
  name: "Brevity",
  config: { mapping: { output: "col-answer" } },
};
const original = { promptConfig: [], userEvalMetrics: [tone], trials: 1 };
const NOT_ESTIMATED = "Scoring on kept runs is not included in this estimate.";

const rerunCard = {
  targetType: PROMPT_CONFIG_TYPE.PROMPT,
  name: "Support",
  model: [{ value: "gpt-4o" }],
};
// An Edit save's scope with one card that runs and nothing else changed.
const runsOnly = {
  promptConfig: [rerunCard],
  rerun: [],
  added: [],
  kept: [],
  keptAgents: [],
  rerunAgents: [],
  evalsEdited: [],
  evalsNew: [],
};
// Saves that start runs while an evaluation is edited or added and another
// run is kept, other than the mixed save.
const keptScored = Object.entries({
  "an evaluation edited, a prompt re-run and only prompts kept": {
    rerun: ["#4A"],
    kept: ["#4B"],
    evalsEdited: ["Tone"],
  },
  "an evaluation edited and an agent re-run": {
    rerun: ["#4C"],
    rerunAgents: ["#4C"],
    kept: ["#4A", "#4B"],
    evalsEdited: ["Tone"],
  },
  "an evaluation added and a run re-run": {
    rerun: ["#4A"],
    kept: ["#4B"],
    evalsNew: ["Brevity"],
  },
  "an evaluation edited and a new run": {
    added: ["#4C"],
    kept: ["#4A", "#4B"],
    evalsEdited: ["Tone"],
  },
});

function Chip({ values }) {
  const { control } = useForm({ defaultValues: values });
  return <SimpleEstimate control={control} chip />;
}

function renderChip(current, base = original) {
  const context = {
    datasetId: "dataset-1",
    nextOrdinal: 4,
    editBase: { values: base, runs: [] },
    mappingColumns: [],
    columns: [],
    rows: [],
    versions: [],
    splits: [],
    exampleCount: 3,
  };
  render(
    <ExperimentFormContext.Provider value={context}>
      <Chip values={current} />
    </ExperimentFormContext.Provider>,
  );
  return screen.getByTestId("config-running-total");
}

// The footer's text for an Edit save of `scope`.
function totalFor(scope) {
  scopeState.override = scope;
  const text = renderChip(original).textContent;
  cleanup();
  return text;
}

afterEach(() => {
  scopeState.override = null;
});

describe("Edit footer", () => {
  it("shows only the edited-evaluation sentence", () => {
    const chip = renderChip({ ...original, userEvalMetrics: [editedTone] });

    expect(chip.textContent).toBe(
      "The edited evaluation is scored again on every run.",
    );
    expect(chip.textContent).not.toMatch(/\$/);
  });

  it("shows the edited-evaluation sentence for a mixed save, then the re-run estimate as before", () => {
    const scope = {
      ...runsOnly,
      rerun: ["#4A"],
      kept: ["#4B", "#4C"],
      keptAgents: ["#4C"],
    };
    // The same re-run with no evaluation edited: today's total.
    const before = totalFor(scope);
    const chip = totalFor({ ...scope, evalsEdited: ["Tone"] });

    expect(before).toMatch(/^Re-run #4A/);
    expect(chip).toBe(
      `The edited evaluation is scored again on every run. ${before}. ${NOT_ESTIMATED}`,
    );
    expect(chip).not.toMatch(/cleared|run the evaluation again/);
  });

  it.each(keptScored)("adds the words after the amount: %s", (_, change) => {
    const scope = { ...runsOnly, ...change };
    // The same runs with no evaluation edited or added: today's total.
    const before = totalFor({ ...scope, evalsEdited: [], evalsNew: [] });
    const chip = totalFor(scope);

    expect(before).toMatch(/^(Re-run|New) #4/);
    expect(before).not.toMatch(/not estimated/);
    expect(chip).toBe(`${before}. ${NOT_ESTIMATED}`);
  });

  it("keeps today's total for a re-run save that keeps no run", () => {
    const scope = { ...runsOnly, rerun: ["#4A"], evalsEdited: ["Tone"] };
    const before = totalFor({ ...scope, evalsEdited: [] });
    const chip = totalFor(scope);

    expect(chip).toBe(before);
    expect(chip).not.toMatch(/not estimated/);
  });

  it("shows only the added-evaluation sentence", () => {
    const chip = renderChip({ ...original, userEvalMetrics: [tone, brevity] });

    expect(chip.textContent).toBe(
      "The added evaluation is scored on every run.",
    );
    expect(chip.textContent).not.toMatch(/\$/);
  });

  it("shows only the edited-and-added sentence", () => {
    const chip = renderChip({
      ...original,
      userEvalMetrics: [editedTone, brevity],
    });

    expect(chip.textContent).toBe(
      "The edited and added evaluations are scored on every run.",
    );
    expect(chip.textContent).not.toMatch(/\$/);
  });

  it("keeps today's text when the save scores nothing", () => {
    const chip = renderChip({ ...original, userEvalMetrics: [{ ...tone }] });

    expect(chip.textContent).toBe("Nothing re-runs · $0");
  });

  it("keeps today's text for an experiment with an HTTP source", () => {
    scopeState.override = {
      promptConfig: [],
      rerun: [],
      added: [],
      kept: ["Orders API"],
      evalsEdited: ["Tone"],
      evalsNew: [],
    };
    const http = {
      ...original,
      promptConfig: [
        { targetType: PROMPT_CONFIG_TYPE.HTTP, name: "Orders API" },
      ],
    };
    const chip = renderChip(http, http);

    expect(chip.textContent).toBe("Nothing re-runs · $0");
  });
});
