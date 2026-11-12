import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, screen, within } from "@testing-library/react";
import { useForm } from "react-hook-form";
import { render } from "src/utils/test-utils";
import { isSurfaceShown, setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { emptyExperimentValues } from "../../experimentFormValues";
import DataStep from "../DataStep";

// Match launch-wizard-settings.test.jsx: real launch gates and form controls;
// only the surrounding data hooks and unrelated mapping tables are stubbed.
const h = vi.hoisted(() => ({ versions: [] }));
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
vi.mock("src/sections/develop-detail/DataTab/Versions/selectedSample", () => ({
  useSelectedSampleContext: () => ({
    datasetId: "d1",
    versions: h.versions,
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

const priorHelper =
  "The experiment is pinned to this version. Later edits to the dataset don't change its inputs.";
const sealedTitle = "Dataset selection";
const sealedHelper =
  "A new experiment can't run with this selection. Clear it to use the dataset's current rows; row and split selections stay as they are. Existing experiments keep their original data.";
let formValues;

// eslint-disable-next-line react/prop-types
function Host({ defaults = {}, isEditing = false }) {
  const form = useForm({
    defaultValues: { ...emptyExperimentValues(), rowIds: [], ...defaults },
  });
  formValues = form.watch();
  return (
    <DataStep
      control={form.control}
      setValue={form.setValue}
      isEditing={isEditing}
    />
  );
}

beforeEach(() => {
  h.versions = [
    { id: "v1", number: 1, row_count: 3 },
    { id: "v2", number: 2, row_count: 4 },
  ];
  formValues = null;
});
afterEach(() => {
  cleanup();
  setLaunchOverrideForTest(null);
});

describe.each([false, true])("hidden held scope (editing=%s)", (isEditing) => {
  beforeEach(() => {
    setLaunchOverrideForTest({ launch: true, experimentsBasic: true });
    expect(isSurfaceShown("exp.new")).toBe(true);
    expect(isSurfaceShown("dataset.versions")).toBe(false);
  });

  it.each(["v1", "missing-scope"])(
    "shows and clears %s without version copy or an implicit form rewrite",
    (datasetVersionId) => {
      const { container } = render(<Host defaults={{ datasetVersionId }} isEditing={isEditing} />);
      expect(container.textContent).not.toMatch(/version|history/i);
      expect(screen.queryByTestId("experiment-version-select")).toBeNull();
      expect(screen.getByText(sealedTitle, { exact: true }).textContent).toBe(sealedTitle);
      expect(screen.getByText(sealedHelper, { exact: true }).textContent).toBe(sealedHelper);
      expect(formValues.datasetVersionId).toBe(datasetVersionId);

      const clear = screen.getByRole("button", { name: "Clear selection", exact: true });
      expect(clear.disabled).toBe(false);
      expect(clear.getAttribute("type")).toBe("button");
      fireEvent.click(clear);
      expect(formValues.datasetVersionId).toBe("");
      expect(formValues.splitIds).toEqual([]);
      expect(formValues.rowIds).toEqual([]);
      expect(screen.queryByText(sealedTitle)).toBeNull();
      expect(screen.queryByRole("button", { name: "Clear selection" })).toBeNull();
      expect(container.textContent).not.toMatch(/version|history/i);
    },
  );

  it("can clear held scope before its metadata has loaded", () => {
    h.versions = [];
    const { container } = render(<Host defaults={{ datasetVersionId: "v1" }} isEditing={isEditing} />);
    expect(container.textContent).not.toMatch(/version|history/i);
    expect(formValues.datasetVersionId).toBe("v1");
    fireEvent.click(screen.getByRole("button", { name: "Clear selection" }));
    expect(formValues.datasetVersionId).toBe("");
  });

  it("keeps held row and split choices when clearing only the saved scope", () => {
    const { container } = render(
      <Host defaults={{ datasetVersionId: "v1", splitIds: ["s1"], rowIds: ["r1", "r2"] }} isEditing={isEditing} />,
    );
    expect(container.textContent).not.toMatch(/version|history/i);
    expect(screen.getByText("Runs on 2 selected rows", { exact: true })).toBeTruthy();
    expect(container.textContent).not.toMatch(/· v\d/);
    expect(screen.getByTestId("these-rows")).toBeTruthy();
    expect(screen.getByTestId("split-smoke")).toBeTruthy();
    expect(formValues.datasetVersionId).toBe("v1");
    fireEvent.click(screen.getByRole("button", { name: "Clear selection" }));
    expect(formValues.datasetVersionId).toBe("");
    expect(formValues.splitIds).toEqual(["s1"]);
    expect(formValues.rowIds).toEqual(["r1", "r2"]);
  });

  it.each([null, ""])("adds no scope notice for the empty value %s", (datasetVersionId) => {
    const { container } = render(<Host defaults={{ datasetVersionId }} isEditing={isEditing} />);
    expect(container.textContent).not.toMatch(/version|history/i);
    expect(screen.queryByText(sealedTitle)).toBeNull();
    expect(screen.queryByRole("button", { name: "Clear selection" })).toBeNull();
    expect(formValues.datasetVersionId).toBe(datasetVersionId);
  });
});

describe("shown profile control", () => {
  beforeEach(() => {
    setLaunchOverrideForTest({ launch: false, experimentsBasic: true });
    expect(isSurfaceShown("dataset.versions")).toBe(true);
  });

  it.each([false, true])("retains the title, helper and edit lock (editing=%s)", (isEditing) => {
    render(<Host defaults={{ datasetVersionId: "v1", rowIds: ["r1", "r2"] }} isEditing={isEditing} />);
    expect(screen.getByText("Dataset version", { exact: true })).toBeTruthy();
    expect(screen.getByText(priorHelper, { exact: true }).textContent).toBe(priorHelper);
    const picker = screen.getByTestId("experiment-version-select");
    expect(within(picker).getByText("v1", { exact: true })).toBeTruthy();
    expect(within(picker).getByRole("combobox").getAttribute("aria-disabled")).toBe(isEditing ? "true" : null);
    expect(screen.getByText("Runs on 2 selected rows · v1", { exact: true })).toBeTruthy();
    expect(screen.queryByText(sealedTitle)).toBeNull();
    expect(screen.queryByRole("button", { name: "Clear selection" })).toBeNull();
    expect(formValues.datasetVersionId).toBe("v1");
  });

  it("still selects another choice through the original picker", () => {
    render(<Host defaults={{ datasetVersionId: "v1" }} />);
    fireEvent.mouseDown(within(screen.getByTestId("experiment-version-select")).getByRole("combobox"));
    fireEvent.click(screen.getByRole("option", { name: /Latest · v2/ }));
    expect(formValues.datasetVersionId).toBe("v2");
    expect(screen.getByText(priorHelper, { exact: true }).textContent).toBe(priorHelper);
  });
});
