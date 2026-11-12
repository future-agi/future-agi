// L6 verify 2 must-fix: with experimentsBasic ON a launch build's New
// experiment shows only the settings dev honours. The Configuration step has
// no Description and no More settings, the prompt card has no Limits per
// row, and the Data step has no Dataset version pin, no split chips, no
// door-preselected rows and no random sample. A hidden setting that already
// holds a value (a door's rows, a copied experiment, a stale form) shows with
// that value and stays in the body the form sends: it is never dropped
// without a word (L2's serializer-derived checks block Run on it). A
// prototype build (no override) still shows every setting.
import React from "react";
import PropTypes from "prop-types";
import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, within } from "@testing-library/react";
import { useForm } from "react-hook-form";
import { render } from "src/utils/test-utils";
import { setLaunchOverrideForTest } from "../launchSwitch";
import { launchRedirect } from "../launchRoutes";
import { WIZARD_SETTING_SURFACE, wizardSettingShown } from "../wizardSettings";
import { SURFACE_BY_ID, surfacesOfOption } from "../launchSurfaces";
import {
  emptyExperimentValues,
  experimentExtras,
} from "src/sections/develop-detail/Experiment/experimentFormValues";
import ConfigureStepExperimentCreation from "src/sections/develop-detail/Experiment/StepperComponentExperiment/ConfigureStepExperimentCreation";
import DataStep from "src/sections/develop-detail/Experiment/StepperComponentExperiment/DataStep";
import SampleSetting from "src/sections/develop-detail/Experiment/StepperComponentExperiment/SampleSetting";
import RowLimits from "src/sections/common/RunSettings/RowLimits";

vi.mock(
  "src/sections/develop-detail/Experiment/StepperComponentExperiment/ConfigureStepLLM",
  () => ({ default: () => <div data-testid="runs-stub" /> }),
);
vi.mock(
  "src/sections/develop-detail/Experiment/StepperComponentExperiment/ConfigureStepTTS",
  () => ({ default: () => null }),
);
vi.mock(
  "src/sections/develop-detail/Experiment/StepperComponentExperiment/ConfigureStepSTT",
  () => ({ default: () => null }),
);
vi.mock("src/sections/develop-detail/Experiment/StartFromExperiment", () => ({
  default: () => <div data-testid="start-from-stub">Start from</div>,
}));
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
// The Data step's sample context, with data a launch build might still see:
// a version and a split exist, so an absent control is the gate, not data.
vi.mock("src/sections/develop-detail/DataTab/Versions/selectedSample", () => ({
  useSelectedSampleContext: () => ({
    datasetId: "d1",
    versions: [{ id: "v1", number: 1, row_count: 3 }],
    splits: [{ id: "s1", name: "smoke", row_count: 2 }],
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

const BASIC = { launch: true, experimentsBasic: true, madeReal: [] };

afterEach(() => setLaunchOverrideForTest(null));

let formValues = null;
function Host({ defaults = {}, children }) {
  const form = useForm({
    defaultValues: { ...emptyExperimentValues(), ...defaults },
  });
  formValues = form.watch();
  return children(form);
}
Host.propTypes = { defaults: PropTypes.object, children: PropTypes.func };

const configure = (defaults) =>
  render(
    <Host defaults={defaults}>
      {(f) => (
        <ConfigureStepExperimentCreation
          control={f.control}
          getValues={f.getValues}
          setValue={f.setValue}
          unregister={f.unregister}
          clearErrors={f.clearErrors}
          watch={f.watch}
          errors={{}}
        />
      )}
    </Host>,
  );

const dataStep = (defaults) =>
  render(
    <Host defaults={defaults}>
      {(f) => <DataStep control={f.control} setValue={f.setValue} />}
    </Host>,
  );

const sampleSetting = (defaults) =>
  render(
    <Host defaults={defaults}>
      {(f) => <SampleSetting control={f.control} inScope={3} />}
    </Host>,
  );

const rowLimits = (limits) =>
  render(
    <Host defaults={{ promptConfig: [{ rowLimits: limits }] }}>
      {(f) => <RowLimits control={f.control} fieldPrefix="promptConfig.0" />}
    </Host>,
  );

// A run is on the card, so the Configuration step has its usual layout
// (the picker-first layout is checked separately).
const RUN = [{ targetType: "prompt", promptId: "p1", name: "support" }];

describe("L6 wizard settings: the registry", () => {
  it("each hidden wizard setting names the surface that classes its option", () => {
    [
      ["description", "exp:description"],
      ["moreSettings", "more:settings-section"],
      ["rowLimits", "knob:limits:tries"],
      ["sample", "knob:sample"],
      ["theseRows", "data:rows:these"],
      ["version", "data:version"],
      ["splits", "data:rows:split"],
    ].forEach(([setting, option]) => {
      const id = WIZARD_SETTING_SURFACE[setting];
      expect(SURFACE_BY_ID[id], setting).toBeTruthy();
      expect(
        surfacesOfOption(option).map((s) => s.id),
        `${setting} ${option}`,
      ).toContain(id);
    });
    expect(() => wizardSettingShown("nope")).toThrow(/Unknown wizard setting/);
  });

  it("experimentsBasic ON hides them; a held value or a prototype build shows them", () => {
    setLaunchOverrideForTest(BASIC);
    Object.keys(WIZARD_SETTING_SURFACE).forEach((k) => {
      expect(wizardSettingShown(k), k).toBe(false);
      expect(wizardSettingShown(k, true), k).toBe(true);
    });
    setLaunchOverrideForTest(null);
    Object.keys(WIZARD_SETTING_SURFACE).forEach((k) =>
      expect(wizardSettingShown(k), k).toBe(true),
    );
  });
});

describe("L6 wizard settings: Configuration step", () => {
  it("experimentsBasic ON: no Description, no More settings; the name stays", () => {
    setLaunchOverrideForTest(BASIC);
    configure({ promptConfig: RUN });
    expect(screen.getByLabelText(/Experiment name/)).toBeTruthy();
    expect(screen.queryByLabelText(/Description/)).toBeNull();
    expect(screen.queryByText("More settings")).toBeNull();
    expect(screen.queryByTestId("new-experiment-more")).toBeNull();
    expect(screen.queryByTestId("start-from-stub")).toBeNull();
    expect(screen.getByTestId("runs-stub")).toBeTruthy();
  });

  it("experimentsBasic ON, no run yet: no + Description button either", () => {
    setLaunchOverrideForTest(BASIC);
    configure({});
    expect(screen.getByLabelText(/Experiment name/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Description/ })).toBeNull();
    expect(screen.queryByLabelText(/Description/)).toBeNull();
    expect(screen.queryByText("More settings")).toBeNull();
  });

  it("a stale description shows with its text and stays in the body", () => {
    setLaunchOverrideForTest(BASIC);
    configure({ promptConfig: RUN, description: "from a copied experiment" });
    const field = screen.getByLabelText(/Description/);
    expect(field.value).toBe("from a copied experiment");
    expect(experimentExtras(formValues).description).toBe(
      "from a copied experiment",
    );
    // Clearing it is the user's own choice; the field then leaves.
    fireEvent.change(field, { target: { value: "" } });
    expect(experimentExtras(formValues).description).toBe("");
    expect(screen.queryByLabelText(/Description/)).toBeNull();
  });

  it("a non-default More setting (a Start from) keeps More settings", () => {
    setLaunchOverrideForTest(BASIC);
    configure({
      promptConfig: RUN,
      startFrom: { experimentId: "e1", preset: "same-runs" },
    });
    expect(screen.getByText("More settings")).toBeTruthy();
  });

  it("prototype build: Description and More settings are there", () => {
    configure({ promptConfig: RUN });
    expect(screen.getByLabelText(/Description/)).toBeTruthy();
    expect(screen.getByText("More settings")).toBeTruthy();
  });
});

describe("L6 wizard settings: Data step", () => {
  it("experimentsBasic ON: no version pin, no split chips, no sample; every row", () => {
    setLaunchOverrideForTest(BASIC);
    dataStep({});
    expect(screen.queryByText("Dataset version")).toBeNull();
    expect(screen.queryByTestId("experiment-version-select")).toBeNull();
    expect(
      screen.queryByText(/The experiment is pinned to this version/),
    ).toBeNull();
    expect(screen.queryByText("Splits")).toBeNull();
    expect(screen.queryByTestId("split-smoke")).toBeNull();
    expect(screen.queryByText("All rows")).toBeNull();
    expect(screen.queryByText(/Pick none to run on every row/)).toBeNull();
    expect(screen.queryByTestId("sample-setting")).toBeNull();
    expect(screen.queryByText(/A random/)).toBeNull();
    expect(screen.queryByTestId("these-rows")).toBeNull();
    // What stays: the reference column and the honest row count.
    expect(screen.getByText("Expected answers column")).toBeTruthy();
    expect(screen.getByText("Rows")).toBeTruthy();
    expect(screen.getByText("3 examples (all rows)")).toBeTruthy();
    expect(formValues.datasetVersionId).toBeNull();
    expect(formValues.splitIds).toEqual([]);
  });

  it("prototype build: version, splits, all rows and the sample are there", () => {
    dataStep({});
    expect(screen.getByText("Dataset version")).toBeTruthy();
    expect(screen.getByTestId("experiment-version-select")).toBeTruthy();
    expect(screen.getByText("Splits")).toBeTruthy();
    expect(screen.getByTestId("split-smoke")).toBeTruthy();
    expect(screen.getByTestId("sample-setting")).toBeTruthy();
  });

  it("stale held settings show with their values and stay in the body", () => {
    setLaunchOverrideForTest(BASIC);
    dataStep({
      datasetVersionId: "v1",
      splitIds: ["s1"],
      rowIds: ["r1", "r2"],
    });
    expect(screen.queryByTestId("experiment-version-select")).toBeNull();
    expect(screen.queryByText("Dataset version")).toBeNull();
    expect(screen.queryByText(/The experiment is pinned to this version/)).toBeNull();
    const sealedTitle = "Dataset selection";
    const sealedHelper =
      "A new experiment can't run with this selection. Clear it to use the dataset's current rows; row and split selections stay as they are. Existing experiments keep their original data.";
    expect(screen.getByText(sealedTitle, { exact: true }).textContent).toBe(sealedTitle);
    expect(screen.getByText(sealedHelper, { exact: true }).textContent).toBe(sealedHelper);
    const clearSelection = screen.getByRole("button", { name: "Clear selection", exact: true });
    expect(clearSelection.disabled).toBe(false);
    expect(screen.getByText("Splits")).toBeTruthy();
    expect(screen.getByTestId("split-smoke")).toBeTruthy();
    const these = screen.getByTestId("these-rows");
    expect(these.textContent).toBe("These 2 rows");
    expect(these.getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByText("Runs on 2 selected rows", { exact: true })).toBeTruthy();
    expect(screen.queryByText(/Runs on 2 selected rows · v1/)).toBeNull();
    const body = experimentExtras(formValues);
    expect(body.dataset_version_id).toBe("v1");
    expect(body.split_ids).toEqual(["s1"]);
    expect(body.row_ids).toEqual(["r1", "r2"]);
    // Only explicit clearing removes the held scope; row/split choices survive.
    fireEvent.click(clearSelection);
    expect(formValues.datasetVersionId).toBe("");
    expect(experimentExtras(formValues).dataset_version_id).toBeUndefined();
    expect(experimentExtras(formValues).split_ids).toEqual(["s1"]);
    expect(experimentExtras(formValues).row_ids).toEqual(["r1", "r2"]);
    expect(screen.queryByText(sealedTitle)).toBeNull();
    expect(screen.queryByRole("button", { name: "Clear selection", exact: true })).toBeNull();
    // All rows is the user's way out: the rows choice leaves the body.
    fireEvent.click(screen.getByText("All rows"));
    expect(experimentExtras(formValues).row_ids).toBeUndefined();
    expect(experimentExtras(formValues).split_ids).toEqual([]);
  });

  it("only the held setting shows: stale rows alone bring no split chips", () => {
    setLaunchOverrideForTest(BASIC);
    dataStep({ rowIds: ["r1"] });
    expect(screen.getByTestId("these-rows").textContent).toBe("This row");
    expect(screen.queryByTestId("split-smoke")).toBeNull();
    expect(screen.queryByTestId("experiment-version-select")).toBeNull();
    expect(screen.queryByText(/Pick none to run on every row/)).toBeNull();
  });
});

describe("L6 wizard settings: sample and limits per row", () => {
  it("experimentsBasic ON: the sample is absent until a stale one is on", () => {
    setLaunchOverrideForTest(BASIC);
    const { unmount } = sampleSetting({});
    expect(screen.queryByTestId("sample-setting")).toBeNull();
    expect(experimentExtras(formValues).sample).toBeUndefined();
    unmount();
    sampleSetting({ sample: { on: true, n: 2, seed: 7 } });
    const box = screen.getByTestId("sample-setting");
    expect(within(box).getByRole("checkbox").checked).toBe(true);
    expect(experimentExtras(formValues).sample).toEqual({ n: 2, seed: 7 });
  });

  it("experimentsBasic ON: Limits per row is absent until one is set", () => {
    setLaunchOverrideForTest(BASIC);
    const { unmount } = rowLimits({});
    expect(screen.queryByTestId("row-limits")).toBeNull();
    expect(screen.queryByText(/Limits per row/)).toBeNull();
    unmount();
    rowLimits({ tokens: 500 });
    expect(screen.getByTestId("row-limits")).toBeTruthy();
    expect(screen.getByTestId("row-limit-tokens").value).toBe("500");
    expect(formValues.promptConfig[0].rowLimits.tokens).toBe(500);
  });

  it("prototype build: the sample and Limits per row are there", () => {
    sampleSetting({});
    expect(screen.getByTestId("sample-setting")).toBeTruthy();
    rowLimits({});
    expect(screen.getByTestId("row-limits")).toBeTruthy();
  });
});

describe("L6 wizard settings: deep links into New experiment", () => {
  const page = "/dashboard/develop/8d3c1f7e-2b6a-4c11-9d0e-5f6a7b8c9d01";

  it("experimentsBasic ON drops the version pin and the split, keeps the drawer", () => {
    const to = launchRedirect(
      {
        pathname: page,
        search: "?new_experiment=1&version_id=v9&split=smoke&prompt_id=p1",
      },
      { ...BASIC, madeReal: new Set() },
    );
    expect(to).not.toBeNull();
    const p = new URLSearchParams(to.to.split("?")[1] ?? "");
    expect(p.get("new_experiment")).toBe("1");
    expect(p.get("prompt_id")).toBe("p1");
    expect(p.has("version_id")).toBe(false);
    expect(p.has("split")).toBe(false);
  });

  it("experimentsBasic ON keeps `rows=`: the form shows them and Run is blocked, never a silent all-rows run", () => {
    const to = launchRedirect(
      { pathname: page, search: "?new_experiment=1&rows=r1,r2" },
      { ...BASIC, madeReal: new Set() },
    );
    expect(to).toBeNull();
  });

  it("the default launch build (OFF) keeps the classic drawer key but drops NEW scope keys", () => {
    const to = launchRedirect(
      {
        pathname: page,
        search: "?new_experiment=1&rows=r1&version_id=v9&split=smoke",
      },
      { launch: true, experimentsBasic: false, madeReal: new Set() },
    );
    const p = new URLSearchParams(to.to.split("?")[1] ?? "");
    // Census section A: exp.classic owns this key and stays shown at OFF.
    expect(p.get("new_experiment")).toBe("1");
    ["rows", "version_id", "split"].forEach((k) =>
      expect(p.has(k), k).toBe(false),
    );
  });
});
