import React from "react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import process from "node:process";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { isSurfaceShown, setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { useAddColumnDrawerStore, useDeleteColumnStore } from "../../states";

const h = vi.hoisted(() => ({ mutations: [], snack: vi.fn() }));
vi.mock("@tanstack/react-query", () => ({
  useMutation: (options) => {
    h.mutations.push(options);
    return { mutate: vi.fn(), isPending: false, error: null, reset: vi.fn() };
  },
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}));
vi.mock("src/utils/axios", () => ({
  default: { post: vi.fn(), delete: vi.fn() },
  endpoints: { develop: { addColumn: () => "/add", deleteColumn: () => "/delete" } },
}));
vi.mock("notistack", () => ({ enqueueSnackbar: h.snack }));
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: h.snack }));
vi.mock("src/sections/common/ux/toast", () => ({ notify: vi.fn() }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/utils/Mixpanel", () => ({
  trackEvent: vi.fn(), Events: {}, PropertyName: {},
}));
vi.mock("src/pages/dashboard/Develop/Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({ setActionSource: vi.fn() }),
}));
vi.mock("src/sections/develop-detail/Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({ refreshGrid: vi.fn() }),
}));
vi.mock("src/api/develop/develop-detail", () => ({
  useDevelopDatasetList: () => ({ data: [] }),
}));
vi.mock("../Versions/changeSetStore", () => ({
  useCreatedColumnsStore: { getState: () => ({ add: vi.fn(), remove: vi.fn() }) },
}));
vi.mock("../../AddColumn/Expression/ExpressionColumnDialog", () => ({ default: () => null }));
vi.mock("../../AddColumn/common", () => ({ StaticColumns: [], DynamicColumns: [] }));

import DeleteColumn from "../DeleteColumn";
import AddColumnDrawer from "../../AddColumn/AddColumnDrawer";
import { AddSplitChip } from "../DatapointDrawerV2/RowHeaderPills";
import { COLUMN_TYPES, typeChangeConfirmText } from "../columnTypeText";
import { emptyTrainLine } from "../Splits/trainingSetModel";
import { SERVER_BLOCK_COPY, serverBlockOf } from "../../Experiment/serverBlockCopy";

const sealedDelete =
  "Deleting this column removes it and its values from the current dataset. This can't be undone.";
const sealedBlock =
  "A new experiment can't run with this dataset selection. Start a new experiment without it to use the dataset's current rows.";
const sealedTypeChanges = [
  ["text", "Text",
    "Changing this column to Text updates the current dataset and converts its values. Values that fail conversion are kept and marked with an error. This type change has no undo."],
  ["boolean", "Boolean",
    "Changing this column to Boolean updates the current dataset and converts its values. Values that aren't recognized as true or false become false. This type change has no undo."],
  ["integer", "Integer",
    "Changing this column to Integer updates the current dataset and converts its values. Values that fail conversion are kept and marked with an error. Fractional parts are discarded, and large numbers can lose precision without an error. This type change has no undo."],
  ["float", "Float",
    "Changing this column to Float updates the current dataset and converts its values. Values that fail conversion are kept and marked with an error. Numbers can be rounded or changed during conversion without an error. This type change has no undo."],
  ["json", "JSON",
    "Changing this column to JSON updates the current dataset and converts its values. Values that fail conversion are kept and marked with an error. Values can be changed during conversion, including by automatic repair, without an error. This type change has no undo."],
  ["array", "Array",
    "Changing this column to Array updates the current dataset and converts its values. Values that fail conversion are kept and marked with an error. Values inside the array can be changed during conversion without an error. This type change has no undo."],
  ["datetime", "Datetime",
    "Changing this column to Datetime updates the current dataset and converts its values. Values that fail conversion are kept and marked with an error. Some timestamps can be changed during conversion, for example read as a different date, without an error. This type change has no undo."],
  ["image", "Image",
    "Changing this column to Image updates the current dataset and converts its values. Values that fail conversion are kept and marked with an error. Media content or quality can be lost during conversion without an error. This type change has no undo."],
  ["images", "Images",
    "Changing this column to Images updates the current dataset and converts its values. Values that fail conversion are kept and marked with an error. False, zero, null and empty entries can be dropped without an error. Media content or quality can be lost during conversion without an error. This type change has no undo."],
  ["audio", "Audio",
    "Changing this column to Audio updates the current dataset and converts its values. Values that fail conversion are kept and marked with an error. Media content or quality can be lost during conversion without an error. This type change has no undo."],
  ["document", "Document",
    "Changing this column to Document updates the current dataset and converts its values. Values that fail conversion are kept and marked with an error. This type change has no undo."],
];

const noVersions = (text) => expect(text).not.toMatch(/version|history/i);
const textOf = (node) => node.textContent.replace(/\s+/g, " ").trim();

beforeEach(() => {
  h.mutations = [];
  h.snack.mockClear();
  useAddColumnDrawerStore.setState({ openAddColumnDrawer: false });
  useDeleteColumnStore.setState({ deleteColumn: { id: "c1", name: "Input" } });
});
afterEach(() => {
  cleanup();
  setLaunchOverrideForTest(null);
  useDeleteColumnStore.setState({ deleteColumn: null });
});

describe.each([
  ["launch", true, false],
  ["prototype", false, true],
])("dataset copy: %s", (_name, launch, shown) => {
  beforeEach(() => setLaunchOverrideForTest({ launch, experimentsBasic: true }));

  it("uses the exact sealed delete body on both profiles", () => {
    render(<MemoryRouter><DeleteColumn dataset="ds-1" /></MemoryRouter>);
    const body = screen.getByText(sealedDelete, { exact: true });
    expect(body.textContent).toBe(sealedDelete);
    noVersions(textOf(screen.getByRole("dialog")));
    expect(screen.getByRole("button", { name: "Delete", exact: true })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Cancel", exact: true })).toBeTruthy();
  });

  it("uses the exact sealed add-column toast on both profiles", () => {
    render(<MemoryRouter><AddColumnDrawer /></MemoryRouter>);
    act(() => h.mutations.at(-1).onSuccess({}, { new_column_name: "Input" }));
    expect(h.snack).toHaveBeenCalledWith("Column “Input” added", { variant: "success" });
    noVersions(h.snack.mock.calls.at(-1)[0]);
  });

  it("renders the immediate-save split tooltip on both profiles", async () => {
    // Keep committed claims: an editable current row exposes + Split at launch.
    expect(isSurfaceShown("dataset.splits")).toBe(true);
    expect(isSurfaceShown("dataset.versions")).toBe(shown);
    const onAssign = vi.fn();
    render(<AddSplitChip onClick={onAssign} disabled={false} />);
    const trigger = screen.getByRole("button", { name: "Assign splits" });
    expect(trigger.disabled).toBe(false);
    fireEvent.mouseOver(trigger);
    const tooltip = await screen.findByRole("tooltip");
    const description = within(tooltip).getByText("Changes are saved immediately", { exact: true });
    expect(description.textContent).toBe("Changes are saved immediately");
    noVersions(tooltip.textContent);
    fireEvent.click(trigger);
    expect(onAssign).toHaveBeenCalledTimes(1);
  });

  it("covers every offered type with a literal sealed expectation", () => {
    expect(sealedTypeChanges).toHaveLength(11);
    expect(COLUMN_TYPES.map(({ value, label }) => [value, label])).toEqual(
      sealedTypeChanges.map(([type, label]) => [type, label]),
    );
  });

  it.each(sealedTypeChanges)("uses the sealed %s conversion disclosure on both profiles", (type, label, expected) => {
    for (const invalidCount of [undefined, null, 0, 1, 2, 1000]) {
      const copy = typeChangeConfirmText({ invalidCount, type, label });
      expect(copy).not.toMatch(/\b(?:versions?|versioned|history|restor\w*)\b/i);
      expect(copy).toBe(invalidCount === undefined
        ? `Checking which values fit ${label}…`
        : invalidCount === null
          ? `Couldn't check the values. ${expected}`
          : expected);
    }
  });

  it("retains the label fallback and case-insensitive type handling", () => {
    const expected = sealedTypeChanges.find(([type]) => type === "images")[2];
    expect(typeChangeConfirmText({ invalidCount: 0, type: "IMAGES", label: "Images" })).toBe(expected);
    expect(typeChangeConfirmText({ invalidCount: 0, type: "images" })).toBe(
      expected.replace("to Images", "to images"),
    );
  });

  it("does not offer a hidden version as the empty training-split remedy", () => {
    const plan = { parts: [{ key: "train", splitName: "train", written: 0, read: 0 }] };
    expect(emptyTrainLine(plan, 6)).toBe(shown
      ? "Split train has no rows in v6. Pick another split or version to train on."
      : "Split train has no current saved rows. Pick another split to train on.");
    expect(emptyTrainLine(plan)).toBe("Split train has no current saved rows. Pick another split to train on.");
  });

  it("uses the exact sealed unsupported-scope alert text on both profiles", () => {
    const copy = SERVER_BLOCK_COPY.dataset_version_id.text();
    expect(copy).toBe(sealedBlock);
    expect(serverBlockOf("dataset_version_id", "v1").text).toBe(sealedBlock);
    noVersions(copy);
  });
});

// Static contract for the deeply nested scratch-run footer; no app or network
// is run here. The component's original shown sentence remains in its branch.
it("gates the Try-on-one-row empty-dataset sentence at its copy site", () => {
  const source = readFileSync(resolve(process.cwd(), "src/sections/develop-detail/Experiment/TryOnOneRow.jsx"), "utf8");
  expect(source).toMatch(/isSurfaceShown\("dataset\.versions"\)\s*\? "This dataset version has no rows"\s*:\s*"This dataset has no rows"/);
});
