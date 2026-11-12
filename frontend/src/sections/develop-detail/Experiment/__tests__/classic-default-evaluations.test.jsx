import { act, cleanup, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  experimentsBasicFromEnv,
  isSurfaceShown,
  setLaunchOverrideForTest,
} from "src/launch/launchSwitch";
import { madeRealSurfaceIds } from "src/launch/madeReal";
import { ui, mountDrawer } from "./createAdmissionDrawer.fixture";

vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn(() => {
      throw new Error("Unexpected GET in default-evaluation test");
    }),
    post: vi.fn(() => {
      throw new Error("Unexpected POST in default-evaluation test");
    }),
  },
  endpoints: {},
}));

const columns = [
  { id: "output-column", name: "Answer", origin_type: "run_prompt" },
];
const savedDefault = {
  id: "saved-eval",
  template_id: "saved-template",
  name: "Dataset default score",
  is_experiment_default: true,
  mapping: { output: "output-column" },
};
const configuredEvaluations = () =>
  JSON.parse(screen.getByLabelText("Configured evaluations").textContent);
const clients = [];

beforeEach(() => {
  ui.emptyDefaults = true;
  ui.store.currentStep = 0;
  ui.suggestion = {};
  ui.columns = columns;
  ui.datasetEvals = [
    savedDefault,
    { ...savedDefault, id: "ordinary-eval", is_experiment_default: false },
  ];
  ui.sampleRows = [];
  ui.nameAvailable = true;
  setLaunchOverrideForTest({
    launch: true,
    experimentsBasic: experimentsBasicFromEnv({
      VITE_DSX_EXPERIMENTS_BASIC: "false",
    }),
    madeReal: madeRealSurfaceIds(),
  });
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  setLaunchOverrideForTest(null);
  ui.emptyDefaults = false;
  ui.store.currentStep = 3;
  ui.columns = [];
  ui.datasetEvals = [];
});

it.each(["cached", "late columns", "late evaluations"])(
  "classic creation starts with no evaluations despite %s dataset defaults",
  async (arrival) => {
    const evals = ui.datasetEvals;
    if (arrival === "late columns") ui.columns = [];
    if (arrival === "late evaluations") ui.datasetEvals = undefined;
    const { rerender, view, client } = mountDrawer();
    clients.push(client);
    expect(isSurfaceShown("exp.new")).toBe(false);
    expect(isSurfaceShown("dataset.experimentDefaults")).toBe(false);
    expect(configuredEvaluations()).toEqual([]);
    ui.columns = columns;
    ui.datasetEvals = evals;
    await act(async () => rerender(view()));
    expect(configuredEvaluations()).toEqual([]);
  },
);

it.each(["cached", "late columns", "late evaluations"])(
  "default-ON creation seeds %s dataset defaults without an F67 claim",
  async (arrival) => {
    const evals = ui.datasetEvals;
    if (arrival === "late columns") ui.columns = [];
    if (arrival === "late evaluations") ui.datasetEvals = undefined;
    setLaunchOverrideForTest({
      launch: true,
      experimentsBasic: experimentsBasicFromEnv({}),
      madeReal: [],
    });
    const { rerender, view, client } = mountDrawer();
    clients.push(client);
    expect(isSurfaceShown("exp.new")).toBe(true);
    expect(isSurfaceShown("dataset.experimentDefaults")).toBe(true);
    if (arrival !== "cached") expect(configuredEvaluations()).toEqual([]);
    ui.columns = columns;
    ui.datasetEvals = evals;
    await act(async () => rerender(view()));
    expect(configuredEvaluations()).toEqual([
      expect.objectContaining({
        name: savedDefault.name,
        datasetEvalId: savedDefault.id,
        templateId: savedDefault.template_id,
        isDefault: true,
        config: { mapping: { output: "output" } },
      }),
    ]);
  },
);

it("an F67 claim cannot seed defaults when the build explicitly sets basic false", () => {
  setLaunchOverrideForTest({
    launch: true,
    experimentsBasic: experimentsBasicFromEnv({
      VITE_DSX_EXPERIMENTS_BASIC: "false",
    }),
    madeReal: [...madeRealSurfaceIds(), "dataset.experimentDefaults"],
  });
  const { client } = mountDrawer();
  clients.push(client);
  expect(isSurfaceShown("dataset.experimentDefaults")).toBe(false);
  expect(configuredEvaluations()).toEqual([]);
});

it("keeps default seeding and output remapping when F67 is explicitly claimed", () => {
  setLaunchOverrideForTest({
    launch: true,
    experimentsBasic: true,
    madeReal: [...madeRealSurfaceIds(), "dataset.experimentDefaults"],
  });
  const { client } = mountDrawer();
  clients.push(client);
  expect(isSurfaceShown("exp.new")).toBe(true);
  expect(isSurfaceShown("dataset.experimentDefaults")).toBe(true);
  expect(configuredEvaluations()).toEqual([
    expect.objectContaining({
      name: savedDefault.name,
      datasetEvalId: savedDefault.id,
      templateId: savedDefault.template_id,
      isDefault: true,
      config: { mapping: { output: "output" } },
    }),
  ]);
});

it("does not replace existing evaluation choices when defaults are claimed", () => {
  ui.emptyDefaults = false;
  setLaunchOverrideForTest({
    launch: true,
    experimentsBasic: true,
    madeReal: [...madeRealSurfaceIds(), "dataset.experimentDefaults"],
  });
  const { client } = mountDrawer();
  clients.push(client);
  expect(configuredEvaluations()).toEqual([
    expect.objectContaining({ name: "submission-score" }),
  ]);
});
