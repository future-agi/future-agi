// Organizing splits on the dataset page (dataset.splits) and running an
// experiment on a split (exp.new.splitScope) are separate claims. These
// cases exercise independent dataset-page and run-scope claims.
// Without run scope, New experiment offers no split;
// with it, a picked split reaches the create body the server accepts.
import React from "react";
import { isRealBackend } from "src/sections/develop/realBackendCaps";
import { isLaunchBuild } from "src/launch/launchSwitch";
import PropTypes from "prop-types";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { render } from "src/utils/test-utils";
import {
  isSurfaceShown,
  setLaunchOverrideForTest,
} from "src/launch/launchSwitch";
import { madeRealSurfaceIds } from "src/launch/madeReal";
import { sanitizeLaunchSearch } from "src/launch/launchSearch";
import { SURFACE_BY_ID, surfacesOfOption } from "src/launch/launchSurfaces";
import {
  WIZARD_SETTING_SURFACE,
  wizardSettingShown,
} from "src/launch/wizardSettings";
import {
  emptyExperimentValues,
  experimentExtras,
} from "src/sections/develop-detail/Experiment/experimentFormValues";
import { wireExperimentBody } from "src/sections/develop-detail/Experiment/serverBody";
import DataStep from "src/sections/develop-detail/Experiment/StepperComponentExperiment/DataStep";
import RunOnRowsButton from "src/sections/common/RunSources/RunOnRowsButton";

const SPLIT_ID = "4f9d2c1a-7b3e-4c5d-8e6f-0a1b2c3d4e5f";

vi.mock(
  "src/sections/develop-detail/Experiment/StepperComponentExperiment/ReadsOnSampleTable",
  () => ({ default: () => null }),
);
vi.mock("src/sections/develop-detail/Experiment/VariableMappingTable", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/Datasets/datasetKind", () => ({
  useDatasetKind: () => ({ kind: "rows", dataset: null, isLoading: false }),
}));
vi.mock("src/sections/develop-detail/Experiment/useVersionScope", () => ({
  useVersionSplitCounts: () => ({}),
}));
vi.mock(
  "src/sections/develop-detail/DataTab/Versions/useUnsavedSelection",
  () => ({ useUnsavedSelection: () => ({ sentence: null }) }),
);
// A split exists on the dataset, so an absent chip is the gate, not data.
vi.mock("src/sections/develop-detail/DataTab/Versions/selectedSample", () => ({
  useSelectedSampleContext: () => ({
    datasetId: "d1",
    versions: [{ id: "v1", number: 1, row_count: 3 }],
    splits: [
      {
        id: "4f9d2c1a-7b3e-4c5d-8e6f-0a1b2c3d4e5f",
        name: "smoke",
        row_count: 2,
      },
    ],
    exampleCount: 3,
    rowsInScope: 3,
    rows: [],
    sampleRow: null,
    sampleRowLabel: "Row 1",
    setSampleRowId: () => {},
    userColumns: [{ id: "c1", name: "question" }],
    mappingColumns: [],
  }),
}));

const RUN_SCOPE_OPTIONS = [
  "data:rows:split",
  "estimate:smoke-split-suggestion",
  "entry:run-on-rows:training-split",
];
const DATASET_PAGE = "/dashboard/develop/8d3c1f7e-2b6a-4c11-9d0e-5f6a7b8c9d01";
const committed = () => [...madeRealSurfaceIds()];
const withoutRunScope = () => [
  ...committed().filter((id) => id !== "exp.new.splitScope"),
  "dataset.splits",
];
const state = (madeReal) => ({
  launch: true,
  experimentsBasic: true,
  madeReal: new Set(madeReal),
});

afterEach(() => setLaunchOverrideForTest(null));

let formValues = null;
function Host({ children }) {
  const form = useForm({ defaultValues: emptyExperimentValues() });
  formValues = form.watch();
  return children(form);
}
Host.propTypes = { children: PropTypes.func };

const dataStep = () =>
  render(
    <Host>
      {(f) => <DataStep control={f.control} setValue={f.setValue} />}
    </Host>,
  );

const runOnSplit = () =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RunOnRowsButton
        datasetId="d1"
        split="smoke"
        label="Run on the smoke split"
        variant="toastAction"
      />
    </QueryClientProvider>,
  );

describe("dataset.splits claim: what it turns on", () => {
  it("carries only dataset-page split options; run scope has its own surface", () => {
    expect(committed()).toContain("dataset.splits");
    setLaunchOverrideForTest(state(committed()));
    // All-on F50: out of the 749 hold, so the committed GRID claim admits it.
    expect(isSurfaceShown("dataset.splits")).toBe(true);
    expect(SURFACE_BY_ID["dataset.splits"].options).toEqual([
      "splits:*",
      "grid:split-filter",
      "training:export",
    ]);
    for (const option of RUN_SCOPE_OPTIONS)
      expect(
        surfacesOfOption(option)
          .map((surface) => surface.id)
          .filter((id) => id !== "exp.new"),
        option,
      ).toEqual(["exp.new.splitScope"]);
    expect(WIZARD_SETTING_SURFACE.splits).toBe("exp.new.splitScope");
  });
});

describe("dataset.splits claimed, run scope not claimed, experimentsBasic ON", () => {
  it("New experiment shows no split chips", () => {
    setLaunchOverrideForTest(state(withoutRunScope()));
    expect(wizardSettingShown("splits")).toBe(false);
    dataStep();
    expect(screen.queryByTestId("split-smoke")).toBeNull();
    expect(formValues.splitIds).toEqual([]);
  });

  it("a ?split= deep link is dropped before the page reads it", () => {
    const out = sanitizeLaunchSearch(
      "?new_experiment=1&split=smoke",
      DATASET_PAGE,
      state(withoutRunScope()),
    );
    expect(out.dropped).toContain("split=smoke");
    expect(new URLSearchParams(out.search).has("split")).toBe(false);
    expect(new URLSearchParams(out.search).get("new_experiment")).toBe("1");
  });

  it("Run on a split has no door", () => {
    setLaunchOverrideForTest(state(withoutRunScope()));
    const { container } = runOnSplit();
    expect(container.innerHTML).toBe("");
  });
});

describe("dataset and experiment split search keys", () => {
  it.each([
    [false, false],
    [false, true],
    [true, false],
    [true, true],
  ])("dataset claim %s, run scope claim %s", (datasetShown, runShown) => {
    const claims = committed().filter(
      (id) => id !== "dataset.splits" && id !== "exp.new.splitScope",
    );
    if (datasetShown) claims.push("dataset.splits");
    if (runShown) claims.push("exp.new.splitScope");
    const out = sanitizeLaunchSearch(
      "?new_experiment=1&splits=smoke&split=smoke",
      DATASET_PAGE,
      state(claims),
    );
    const params = new URLSearchParams(out.search);
    expect(params.get("new_experiment")).toBe("1");
    // All-on F50: each key follows its own claim, with no hold over either.
    expect(params.get("splits")).toBe(datasetShown ? "smoke" : null);
    expect(params.get("split")).toBe(runShown ? "smoke" : null);
  });
});

describe("committed claims, experimentsBasic ON", () => {
  it("the split chip shows and a picked split reaches the create body unblocked", () => {
    // All-on F50: the default launch build shows the chip; no census opt-in.
    setLaunchOverrideForTest(state(committed()));
    expect(isLaunchBuild()).toBe(true);
    expect(isRealBackend()).toBe(true);
    expect(committed()).toContain("exp.new.splitScope");
    dataStep();
    fireEvent.click(screen.getByTestId("split-smoke"));
    expect(formValues.splitIds).toEqual([SPLIT_ID]);
    const extras = experimentExtras(formValues);
    expect(extras.split_ids).toEqual([SPLIT_ID]);
    const wire = wireExperimentBody({ split_ids: extras.split_ids }, {}, false);
    expect(wire.blocks).toEqual([]);
    expect(wire.body.split_ids).toEqual([SPLIT_ID]);
  });

  it("default launch shows the chip without changing backend mode", () => {
    setLaunchOverrideForTest(state(committed()));
    expect(committed()).toContain("exp.new.splitScope");
    expect(isRealBackend()).toBe(true);
    dataStep();
    expect(screen.getByTestId("split-smoke")).toBeTruthy();
    expect(formValues.splitIds).toEqual([]);
  });

  it("a claimed ?split= deep link is kept for the wizard to preselect", () => {
    const out = sanitizeLaunchSearch(
      "?new_experiment=1&split=smoke",
      DATASET_PAGE,
      state(committed()),
    );
    expect(out.dropped).toEqual([]);
    expect(new URLSearchParams(out.search).get("split")).toBe("smoke");
  });
});

beforeEach(() => vi.stubEnv("VITE_DSX_CENSUS_B_TEST", "false"));
afterEach(() => vi.unstubAllEnvs());
