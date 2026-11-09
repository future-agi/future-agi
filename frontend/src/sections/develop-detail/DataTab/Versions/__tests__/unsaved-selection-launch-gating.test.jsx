// Static RED-first packet: hidden assertions fail on the pinned base.
// Render the real DataStep, hook and banner; only I/O and unrelated widgets
// are stubbed. The unguarded probe mirrors the Evaluations banner contract.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { render } from "src/utils/test-utils";
import { isSurfaceShown, setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { datasetCaps, setRealBackendForTests } from "src/sections/develop/realBackendCaps";
import { useRunExperimentStore } from "src/sections/develop-detail/states";
import { EXPERIMENT_STEP } from "src/sections/develop-detail/Experiment/constants";
import { ExperimentFormContext } from "src/sections/develop-detail/Experiment/ExperimentFormContext";
import DataStep from "src/sections/develop-detail/Experiment/StepperComponentExperiment/DataStep";
import { useChangeSetStore } from "../changeSetStore";
import { unsavedBannerSentence, useUnsavedSelection } from "../useUnsavedSelection";
import UnsavedRowsBanner from "../UnsavedRowsBanner";

const io = vi.hoisted(() => ({
  save: vi.fn(),
  openSave: vi.fn(),
  refresh: vi.fn(),
  follow: vi.fn(),
  notify: vi.fn(),
}));
vi.mock("src/api/develop/dataset-versions", async (importOriginal) => ({
  ...(await importOriginal()),
  useSaveDatasetVersion: () => ({ mutateAsync: io.save, isPending: false }),
}));
vi.mock("../PendingChangesGate", () => ({ useOpenSaveVersion: () => io.openSave }));
vi.mock("src/sections/develop-detail/Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({ refreshGrid: io.refresh }),
}));
vi.mock("src/sections/develop-detail/DataTab/DataGrid/newRowsReveal", () => ({
  followSavedRows: io.follow,
}));
vi.mock("src/sections/common/ux/toast", () => ({ notify: io.notify }));
vi.mock("notistack", async (importOriginal) => ({ ...(await importOriginal()), enqueueSnackbar: vi.fn() }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/sections/common/Datasets/datasetKind", () => ({
  useDatasetKind: () => ({ kind: "rows", dataset: null, isLoading: false }),
}));
vi.mock("src/sections/develop-detail/Experiment/useVersionScope", () => ({
  useVersionSplitCounts: () => ({}),
}));
vi.mock("../selectedSample", async () => {
  const { useExperimentForm } = await import(
    "src/sections/develop-detail/Experiment/ExperimentFormContext"
  );
  return { useSelectedSampleContext: useExperimentForm };
});
vi.mock("src/sections/develop-detail/Experiment/ClassicExperimentData", () => ({
  default: () => null,
}));
vi.mock("src/sections/develop-detail/Experiment/VariableMappingTable", () => ({
  default: () => null,
}));
vi.mock("src/sections/develop-detail/Experiment/StepperComponentExperiment/ReadsOnSampleTable", () => ({
  default: () => null,
}));
vi.mock("src/sections/develop-detail/Experiment/StepperComponentExperiment/SampleSetting", () => ({
  default: () => null,
}));

const HIDDEN = { launch: true, experimentsBasic: true, madeReal: [] };
const SHOWN = { launch: false, experimentsBasic: true, madeReal: [] };
const DS = "dataset-selection-test";
const RUN = [{ targetType: "external", runName: "Existing run" }];
const pending = (splitIds = []) => ({
  op: "add_row", temp_id: "imp-1", cells: { c1: "pending" }, split_ids: splitIds,
});
const context = {
  datasetId: DS,
  versions: [{ id: "v6", number: 6, row_count: 4 }],
  splits: [{ id: "s1", name: "smoke", row_count: 2 }],
  rows: [], columns: [], userColumns: [], mappingColumns: [],
  exampleCount: 4, rowsInScope: 4, sampleRow: null, sampleRowLabel: "Row 1",
  setSampleRowId: vi.fn(),
};
let form;
let probe;
let queryClient;

// eslint-disable-next-line react/prop-types
function UnguardedBanner({ control }) {
  probe = useUnsavedSelection(control);
  return <UnsavedRowsBanner sentence={probe.sentence} saveLabel={probe.saveLabel}
    onSave={probe.saveAndContinue} note={probe.note} onEditNote={probe.editNote}
    saving={probe.saving} />;
}

// eslint-disable-next-line react/prop-types
function Host({ defaults = {}, unguarded = false }) {
  const methods = useForm({ defaultValues: {
    rowIds: ["imp-1"], splitIds: [], datasetVersionId: null,
    columnId: null, experimentType: "llm", promptConfig: RUN,
    trials: 1, userEvalMetrics: [], ...defaults,
  } });
  form = methods;
  return <ExperimentFormContext.Provider value={context}>
    {unguarded ? <UnguardedBanner control={methods.control} />
      : <DataStep control={methods.control} setValue={methods.setValue} />}
    <button type="button" onClick={() => useRunExperimentStore.getState().nextStep()}>
      Continue
    </button>
  </ExperimentFormContext.Provider>;
}

function mount(defaults = {}, unguarded = false) {
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>
    <Host defaults={defaults} unguarded={unguarded} />
  </QueryClientProvider>);
}

function seed(ops = [pending()]) {
  // Deliberately bypass addOp: normal launch actions cannot create this state.
  useChangeSetStore.setState({ sets: { [DS]: { baseVersionId: "v6", ops } } });
}

function expectHidden() {
  const banner = screen.queryByTestId("pending-rows");
  const warning = screen.queryByTestId("unsaved-rows-continue-note");
  expect([banner?.textContent, warning?.textContent].join(" "))
    .not.toMatch(/\bv\d+\b|version/i);
  expect(banner).toBeNull();
  expect(warning).toBeNull();
  expect(screen.queryByRole("button", { name: /Save as|Edit note/i })).toBeNull();
}

beforeEach(() => {
  vi.clearAllMocks();
  setRealBackendForTests(null);
  setLaunchOverrideForTest(HIDDEN);
  useChangeSetStore.setState({ sets: {} });
  useRunExperimentStore.setState({
    currentStep: EXPERIMENT_STEP.DATA, visitedSteps: [true, true, false, false],
  });
  io.save.mockResolvedValue({
    version: { id: "v7", number: 7 }, row_id_map: { "imp-1": "ROW_7" },
  });
  form = null;
  probe = null;
});
afterEach(() => {
  cleanup();
  queryClient?.clear();
  queryClient = null;
  useChangeSetStore.setState({ sets: {} });
  useRunExperimentStore.getState().reset();
  setRealBackendForTests(null);
  setLaunchOverrideForTest(null);
});

describe("launch pending-selection reachability", () => {
  it("refuses pending adds while keeping the experiment wizard shown", () => {
    expect(isSurfaceShown("exp.new")).toBe(true);
    expect(isSurfaceShown("dataset.versions")).toBe(false);
    expect(datasetCaps()).toMatchObject({ gridWrites: "classic", pendingChanges: false });
    expect(useChangeSetStore.getState().addOp(DS, pending())).toBe(false);
    expect(useChangeSetStore.getState().getOps(DS)).toEqual([]);
  });
});

describe("hidden pending-selection UI (RED on base)", () => {
  it.each([
    ["all pending", ["imp-1"], [], []],
    ["mixed", ["imp-1", "ROW_2"], [], []],
    ["split", [], ["s1"], ["s1"]],
  ])("hides the real banner/Continue warning for %s without discarding input", (_name, rowIds, splitIds, pendingSplits) => {
    seed([pending(pendingSplits)]);
    const beforeOps = useChangeSetStore.getState().getOps(DS);
    mount({ rowIds, splitIds });
    expectHidden();
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    expectHidden();
    expect(useRunExperimentStore.getState().currentStep).toBe(EXPERIMENT_STEP.EVALUATIONS);
    expect(form.getValues("rowIds")).toEqual(rowIds);
    expect(form.getValues("splitIds")).toEqual(splitIds);
    expect(form.getValues("datasetVersionId")).toBeNull();
    expect(useChangeSetStore.getState().getOps(DS)).toBe(beforeOps);
    expect(io.save).not.toHaveBeenCalled();
    expect(io.openSave).not.toHaveBeenCalled();
  });

  it("hides the unguarded banner and makes its hidden save callbacks inert", async () => {
    seed();
    const beforeOps = useChangeSetStore.getState().getOps(DS);
    mount({}, true);
    expectHidden();
    expect(probe.saveLabel).toBeNull();
    expect(probe.note).toBeNull();
    await act(async () => { await probe.saveAndContinue(); probe.editNote(); });
    expect(io.save).not.toHaveBeenCalled();
    expect(io.openSave).not.toHaveBeenCalled();
    expect(form.getValues("rowIds")).toEqual(["imp-1"]);
    expect(useChangeSetStore.getState().getOps(DS)).toBe(beforeOps);
    expect(useRunExperimentStore.getState().currentStep).toBe(EXPERIMENT_STEP.DATA);
  });

  it.each([0, 1, 2])("hides helper wording with %i runs", (runs) => {
    expect(unsavedBannerSentence({ total: 6, unsaved: 6 },
      { versionNumber: 6, next: 7, runs, trials: 3 })).toBeNull();
  });
});

describe("shown pending-selection UI stays unchanged", () => {
  beforeEach(() => setLaunchOverrideForTest(SHOWN));

  it("keeps the all-pending copy and Continue warning byte-for-byte", () => {
    seed();
    mount();
    expect(within(screen.getByTestId("pending-rows")).getByText(
      "1 new row (not saved yet) × 1 run = 1 try. Save it as v7 to run it.",
    )).toBeTruthy();
    expect(screen.getByTestId("save-note").textContent).toBe("Version note: Added 1 row · Edit note");
    expect(screen.getByRole("button", { name: "Save as v7 and continue" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(screen.getByTestId("unsaved-rows-continue-note").textContent).toBe(
      'Continue waits for these rows: a run reads saved rows only. Use "Save as v7 and continue".',
    );
    expect(useRunExperimentStore.getState().currentStep).toBe(EXPERIMENT_STEP.DATA);
    expect(useRunExperimentStore.getState().visitedSteps).toEqual([true, true, false, false]);
    expect(io.save).not.toHaveBeenCalled();
  });

  it("keeps the mixed-selection warning and dialog path", () => {
    seed([pending(), { op: "update_cell", row_id: "ROW_2", column_id: "c1", value: "edit" }]);
    mount({ rowIds: ["imp-1", "ROW_2"] });
    expect(within(screen.getByTestId("pending-rows")).getByText(
      "1 of the 2 selected rows isn't in v6 yet. Save it as a new version first, or it is skipped.",
    )).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Save as v7 and continue" }));
    expect(io.openSave).toHaveBeenCalledWith({ onSaved: expect.any(Function) });
    expect(io.save).not.toHaveBeenCalled();
  });

  it("keeps the direct save body, remapping and Evaluations destination", async () => {
    seed();
    mount();
    fireEvent.click(screen.getByRole("button", { name: "Save as v7 and continue" }));
    await waitFor(() => expect(form.getValues("rowIds")).toEqual(["ROW_7"]));
    expect(io.save).toHaveBeenCalledTimes(1);
    expect(io.save).toHaveBeenCalledWith({
      base_version_id: "v6", description: "Added 1 row", operations: [pending()],
    });
    expect(form.getValues("datasetVersionId")).toBe("v7");
    expect(useChangeSetStore.getState().getOps(DS)).toEqual([]);
    expect(useRunExperimentStore.getState().currentStep).toBe(EXPERIMENT_STEP.EVALUATIONS);
    expect(io.follow).toHaveBeenCalledWith(DS, { "imp-1": "ROW_7" });
    expect(io.openSave).not.toHaveBeenCalled();
  });

  it("keeps Edit note opening the save dialog", () => {
    seed();
    mount({}, true);
    fireEvent.click(screen.getByRole("button", { name: "Edit note" }));
    expect(io.openSave).toHaveBeenCalledWith({ onSaved: expect.any(Function) });
    expect(io.save).not.toHaveBeenCalled();
  });

  it("keeps the multi-trial helper sentence byte-for-byte", () => {
    expect(unsavedBannerSentence({ total: 6, unsaved: 6 },
      { versionNumber: 6, next: 7, runs: 2, trials: 3 })).toBe(
      "6 new rows (not saved yet) × 3 tries × 2 runs = 36 tries. Save them as v7 to run them.",
    );
  });
});
