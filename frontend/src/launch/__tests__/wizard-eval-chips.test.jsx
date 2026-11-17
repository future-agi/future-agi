import React from "react";
import PropTypes from "prop-types";
import { madeRealSurfaceIds } from "../madeReal";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, screen, within } from "@testing-library/react";
import { render } from "src/utils/test-utils";
import EvaluationStepExperimentCreation from "src/sections/develop-detail/Experiment/StepperComponentExperiment/EvaluationStepExperimentCreation";
import axios from "src/utils/axios";
import {
  experimentsBasicFromEnv,
  setLaunchOverrideForTest,
  surfaceShownIn,
} from "../launchSwitch";
import { SURFACE_BY_ID } from "../launchSurfaces";

// Exercise the real step, card, form and launch gates. Peripheral drawers and
// data hooks are inert; any unexpected request fails without leaving the test.
vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn(() => {
      throw new Error("Unexpected GET in chip visibility test");
    }),
    post: vi.fn(() => {
      throw new Error("Unexpected POST in chip visibility test");
    }),
  },
  endpoints: {},
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/sections/common/EvalPicker", () => ({
  EvalPickerDrawer: () => null,
}));
vi.mock(
  "src/sections/develop-detail/Experiment/StepperComponentExperiment/DraftJudgeDialog",
  () => ({ default: () => null }),
);
vi.mock("src/sections/common/EvalHandoffs/AddEvalGroupButton", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/DecisionJudges/DecisionCallPanel", () => ({
  default: () => null,
}));
vi.mock("src/sections/evals/hooks/useEvalDetail", () => ({
  useEvalDetail: () => ({ data: null }),
}));
vi.mock("src/sections/common/Datasets/datasetKind", () => ({
  useDatasetKind: () => ({ dataset: null }),
}));
vi.mock("src/sections/common/RunSettings/modelCapabilities", () => ({
  useJudgeMediaWarnings: () => [],
}));
vi.mock("src/sections/develop-detail/states", () => ({
  useRunEvaluationStore: (select) => select({ setOpenRunEvaluation: () => {} }),
}));
vi.mock("src/sections/develop-detail/DataTab/Versions/changeSetStore", () => ({
  useChangeSetStore: (select) => select({ sets: {} }),
}));
vi.mock("src/sections/develop-detail/DataTab/Versions/versionedSource", () => ({
  tableRequestFor: () => {
    throw new Error("Unexpected split-row query in chip visibility test");
  },
}));
vi.mock(
  "src/sections/develop-detail/DataTab/Versions/useUnsavedSelection",
  () => ({
    useUnsavedSelection: () => ({
      sentence: null,
      saveLabel: "Save",
      saveAndContinue: () => {},
    }),
  }),
);
vi.mock("src/sections/develop-detail/DataTab/Versions/selectedSample", () => ({
  useSelectedSampleContext: () => ({
    datasetId: "dataset-fixture",
    columns: [{ id: "question-column", name: "Question", data_type: "text" }],
    rows: [],
    exampleCount: 1,
    sampleRow: { row_id: "row-fixture", "question-column": "Example question" },
    sampleRowLabel: "Row 1",
    versions: [],
  }),
}));

const CHIPS = "exp.new.evalCardChips";
const DEFAULTS = "dataset.experimentDefaults";
const READS = "exp.new.readsTable";
const generalTestIds = [
  "eval-card-chips",
  "eval-auto-chip",
  "eval-pass-at-chip",
  "eval-judge-chip",
  "eval-decision-chip",
];
const state = (
  claims = [],
  experimentsBasic = experimentsBasicFromEnv({}),
  launch = true,
) => ({
  launch,
  experimentsBasic,
  madeReal: new Set(claims),
});
const baseCard = {
  templateId: "template-fixture",
  outputType: "score",
  version: "v3",
  higherIsBetter: true,
  model: "turing_large",
  config: { threshold: 0.75, mapping: { output: "output" } },
};
const cards = [
  { ...baseCard, name: "Default score", isDefault: true },
  { ...baseCard, name: "Production score", fromProduction: true },
  { ...baseCard, name: "Pass check", outputType: "pass_fail" },
  { ...baseCard, name: "Decision check", model: "jev-latest" },
];
const clients = [];
function Host({ evaluations }) {
  const form = useForm({
    defaultValues: {
      userEvalMetrics: evaluations,
      datasetVersionId: null,
      columnId: null,
      splitIds: [],
      rowIds: [],
      promptConfig: [],
    },
  });
  return <EvaluationStepExperimentCreation control={form.control} />;
}
Host.propTypes = { evaluations: PropTypes.array.isRequired };
function renderStep(current, evaluations = cards) {
  setLaunchOverrideForTest(current);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  clients.push(client);
  return render(
    <QueryClientProvider client={client}>
      <Host evaluations={evaluations} />
    </QueryClientProvider>,
  );
}
function expectGeneralHidden() {
  for (const id of generalTestIds)
    expect(screen.queryByTestId(id), id).toBeNull();
  for (const text of ["Score 0–1", "Pass/fail", "Higher is better", "v3"])
    expect(screen.queryByText(text), text).toBeNull();
}
function expectGeneralShown() {
  expect(screen.getAllByTestId("eval-card-chips")).toHaveLength(4);
  for (const id of generalTestIds.slice(1))
    expect(screen.getAllByTestId(id).length, id).toBeGreaterThan(0);
  expect(screen.getAllByText("v3")).toHaveLength(4);
}
function expectDefaultShown() {
  expect(screen.getAllByTestId("eval-default-chip")).toHaveLength(1);
  expect(
    within(screen.getByTestId("eval-card-Default score")).getByTestId(
      "eval-default-chip",
    ),
  ).toBeTruthy();
  expect(
    screen
      .getByTestId("eval-default-chip")
      .closest('[data-testid="eval-card-chips"]'),
  ).toBeNull();
}
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  setLaunchOverrideForTest(null);
  vi.clearAllMocks();
});
// The switch ships on, so every chip surface shows (the last block below).
// The cases that pin what a launch build hides render with it off in the
// override, so the gates decide as they do with the switch off.
const off = (current) => ({ ...current, showUnreleased: false });

describe("wizard eval chip ownership", () => {
  it.each([
    { label: "empty", evaluations: [] },
    { label: "saved cards", evaluations: cards },
  ])(
    "classic evaluation copy does not promise hidden dataset-default seeding ($label)",
    ({ evaluations }) => {
      renderStep(
        {
          launch: true,
          showUnreleased: false,
          experimentsBasic: false,
          madeReal: madeRealSurfaceIds(),
        },
        evaluations,
      );
      expect(screen.queryByText(/preselected/i)).toBeNull();
      expect(screen.queryByText(/Use in new experiments/i)).toBeNull();
      expect(screen.queryByTestId("eval-default-chip")).toBeNull();
      expect(
        screen.getByRole("button", { name: "Add evaluation" }),
      ).toBeEnabled();
      if (!evaluations.length)
        expect(screen.getByText("Add at least one evaluation.")).toBeVisible();
    },
  );

  it.each([
    { label: "no claims", claims: [] },
    { label: "a redundant F67 claim", claims: [DEFAULTS] },
  ])("keeps the default-seeding explanation with $label", ({ claims }) => {
    renderStep(state(claims), []);
    expect(screen.getAllByText(/preselected/i)).toHaveLength(2);
  });

  it("default-ON basic shows the default badge without general or production chips", () => {
    renderStep(off(state()));
    expectGeneralHidden();
    expectDefaultShown();
    for (const item of cards) expect(screen.getByText(item.name)).toBeTruthy();
    expect(
      screen.getAllByRole("button", { name: "Test on Row 1" }),
    ).toHaveLength(4);
    expect(
      screen.getAllByRole("button", { name: "Edit evaluation" }),
    ).toHaveLength(4);
    expect(
      screen.getAllByRole("button", { name: "Remove evaluation" }),
    ).toHaveLength(3);
    expect(axios.get).not.toHaveBeenCalled();
    expect(axios.post).not.toHaveBeenCalled();
  });

  it("a general-chips claim leaves the independently shown default badge intact", () => {
    renderStep(off(state([CHIPS])));
    expectGeneralShown();
    expectDefaultShown();
    expect(screen.queryByText("Produced by each run")).toBeNull();
  });

  it("F67 defaults-only ON shows its badge without claiming sibling chips (EVAL-E2E-011 contract)", () => {
    renderStep(off(state([DEFAULTS])));
    expectDefaultShown();
    expectGeneralHidden();
    expect(screen.queryByText("Produced by each run")).toBeNull();
  });

  it("independent claims show the default badge outside the general wrapper", () => {
    renderStep(state([CHIPS, DEFAULTS]));
    expectGeneralShown();
    expectDefaultShown();
  });

  it.each([
    { label: "no claims", claims: [] },
    { label: "chips only", claims: [CHIPS] },
    { label: "defaults only", claims: [DEFAULTS] },
    { label: "both claims", claims: [CHIPS, DEFAULTS] },
  ])(
    "experimentsBasic OFF keeps both chip surfaces hidden with $label",
    ({ claims }) => {
      expect(
        Array.isArray(claims),
        "OFF case must receive the entire claims array",
      ).toBe(true);
      const current = state(
        claims,
        experimentsBasicFromEnv({ VITE_EXPERIMENTS_NEW_PAGES: "false" }),
      );
      expect(current.madeReal.size).toBe(claims.length);
      expect(current.madeReal.has(CHIPS)).toBe(claims.includes(CHIPS));
      expect(current.madeReal.has(DEFAULTS)).toBe(claims.includes(DEFAULTS));
      renderStep(off(current));
      expectGeneralHidden();
      expect(screen.queryByTestId("eval-default-chip")).toBeNull();
    },
  );

  it("prototype mode keeps all chips without claims", () => {
    renderStep(state([], false, false));
    expectGeneralShown();
    expect(screen.getByTestId("eval-default-chip")).toBeTruthy();
    expect(
      screen
        .getByTestId("eval-default-chip")
        .closest('[data-testid="eval-card-chips"]'),
    ).toBeNull();
  });

  it("hiding chips leaves the independent mapping table and Test disclosure intact", () => {
    renderStep(off(state([READS])));
    expectGeneralHidden();
    const card = within(screen.getByTestId("eval-card-Default score"));
    expect(card.getByText("Produced by each run")).toBeTruthy();
    fireEvent.click(card.getByRole("button", { name: "Test on Row 1" }));
    expect(card.getByRole("button", { name: "Run test" })).toBeTruthy();
    expect(axios.get).not.toHaveBeenCalled();
    expect(axios.post).not.toHaveBeenCalled();
  });

  it("documents default-ON F67 without widening the independent general-chip gate", () => {
    setLaunchOverrideForTest({ showUnreleased: false });
    expect(SURFACE_BY_ID[CHIPS]).toMatchObject({
      ships: "hidden",
      options: ["wiz:card-chips"],
    });
    expect(SURFACE_BY_ID[CHIPS].gate).toContain("isSurfaceShown");
    expect(SURFACE_BY_ID[CHIPS].why).toContain("dataset.experimentDefaults");
    expect(SURFACE_BY_ID[DEFAULTS]).toMatchObject({
      ships: "shown",
      experiments: true,
    });
    expect(SURFACE_BY_ID[DEFAULTS].gate).toContain("eval-default-chip");
    expect(surfaceShownIn(CHIPS, state())).toBe(false);
    expect(surfaceShownIn(CHIPS, state([DEFAULTS]))).toBe(false);
    expect(surfaceShownIn(DEFAULTS, state())).toBe(true);
    expect(surfaceShownIn(DEFAULTS, state([CHIPS]))).toBe(true);
    for (const current of [state(), state([CHIPS, DEFAULTS])])
      for (const { id } of Object.values(SURFACE_BY_ID))
        if (id.startsWith("exp.results."))
          expect(surfaceShownIn(id, current), id).toBe(
            id === "exp.results.ciComment",
          );
  });
});

// The release as shipped: the switch stays on, so both chip surfaces and the
// DSX Results surfaces show without a claim, flag OFF and ON. A gate check
// only; it renders nothing and checks no words.
describe("wizard eval chip ownership as shipped (unreleased parts shown)", () => {
  it("shows chips and Results including CI comments without a claim", () => {
    for (const experimentsBasic of [false, true]) {
      const current = state([], experimentsBasic);
      const hidden = [
        CHIPS,
        DEFAULTS,
        READS,
        ...Object.keys(SURFACE_BY_ID).filter((id) =>
          id.startsWith("exp.results."),
        ),
      ].filter((id) => !surfaceShownIn(id, current));
      expect(hidden, `experimentsBasic=${experimentsBasic}`).toEqual([]);
    }
  });
});
