/* eslint-disable react/prop-types */
// New experiment › Data step › Dataset version in a launch build. The server
// copies the dataset's current rows when it creates the experiment, or the
// rows of an earlier version picked here. The picker offers Current rows
// first, then every earlier version it read (most are copies the server kept
// for earlier experiments), and one line under it counts them.
// Versions have the server's own shape (the contract fixture): an id, a
// date, a note, metadata and an item count, and no version number.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, within } from "@testing-library/react";
import { useForm } from "react-hook-form";
import { render } from "src/utils/test-utils";
import { fDateTime } from "src/utils/format-time";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import versionList from "src/api/contracts/__tests__/fixtures/dataset-version-list.json";
import { emptyExperimentValues } from "src/sections/develop-detail/Experiment/experimentFormValues";
import { experimentBodyOfValues } from "src/sections/develop-detail/Experiment/experimentBody";
import { serverExperimentBody } from "src/sections/develop-detail/Experiment/serverBody";
import DataStep from "src/sections/develop-detail/Experiment/StepperComponentExperiment/DataStep";

const NOT_IN_RELEASE = "Not available in this release";
const CURRENT_ROWS = "Current rows";
const CREATED_ROWS = "The rows it was created with";
const HELPER =
  "The rows are copied when the experiment is created. Later edits to the dataset don't change its inputs.";
const PICKED_HELPER =
  "The experiment copies this version's rows when it is created. Later edits to the dataset don't change its inputs.";
const PICK_LINE = "Choose one to run on its rows.";
const FAILED =
  "Couldn't load this dataset's earlier versions. The experiment uses the current rows either way.";
// The same failed list while an earlier version is chosen: the create sends
// that version and runs on its rows.
const FAILED_PICKED =
  "Couldn't load this dataset's earlier versions. The experiment still runs on the chosen version.";
// serverBlockCopy.js: what Run says about a version id that came with the
// form while the picker is held (switch off).
const SWITCH_OFF_BLOCK =
  "A new experiment can't run with this dataset selection. Start a new experiment without it to use the dataset's current rows.";

const [SERVER_VERSION] = versionList.result.versions;
// What the server keeps for every experiment it creates
// (native_run_binding.py: metadata.capture "native-snapshot-v1").
const copyFor = (id, createdAt) => ({
  ...SERVER_VERSION,
  id,
  created_at: createdAt,
  item_count: 3,
  metadata: { capture: "native-snapshot-v1", columns: {} },
});
const COPY_A = copyFor(
  "0b5b7b9e-7777-4c8f-9a8e-0e2f8d1d2c3a",
  "2026-11-10T08:00:00.000000Z",
);
const COPY_B = copyFor(
  "0b5b7b9e-8888-4c8f-9a8e-0e2f8d1d2c3a",
  "2026-11-11T10:20:00.000000Z",
);
// A version saved through the API: no capture metadata, with a note.
const SAVED = { ...SERVER_VERSION, description: "before the holdout edit" };
const UNLISTED = "0b5b7b9e-9999-4c8f-9a8e-0e2f8d1d2c3a";

// The show-unreleased switch as the product modules read it. Shipped: the
// override leaves it to the build and the export reads true. Off: the
// override says false and so does the export.
const unreleased = vi.hoisted(() => ({ shown: true }));
vi.mock("src/launch/launchSwitch", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    get SHOW_UNRELEASED_PARTS() {
      return unreleased.shown;
    },
  };
});

const launchContext = (over = {}) => ({
  datasetId: "d1",
  versions: [],
  versionsHeld: false,
  savedVersions: [],
  runsCurrentRows: true,
  savedVersionsLoading: false,
  savedVersionsFailed: false,
  retrySavedVersions: vi.fn(),
  splits: [],
  exampleCount: 3,
  rowsInScope: 3,
  rows: [],
  sampleRow: null,
  sampleRowLabel: "Row 1",
  setSampleRowId: () => {},
  userColumns: [{ id: "c1", name: "question" }],
  mappingColumns: [],
  ...over,
});

const h = vi.hoisted(() => ({ ctx: null, unsaved: { sentence: null } }));

// Same module mocks as honest-lines-version-picker-held.test.jsx.
vi.mock(
  "src/sections/develop-detail/Experiment/StepperComponentExperiment/ConfigureStepLLM",
  () => ({ default: () => null }),
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
  default: () => null,
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
  () => ({ useUnsavedSelection: () => h.unsaved }),
);
vi.mock("src/sections/develop-detail/DataTab/Versions/selectedSample", () => ({
  useSelectedSampleContext: () => h.ctx,
}));

let formValues;

function Host({ defaults = {}, isEditing = false }) {
  const form = useForm({
    defaultValues: { ...emptyExperimentValues(), ...defaults },
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

const picker = () => screen.getByTestId("experiment-version-select");
const combobox = () => within(picker()).getByRole("combobox");
const openPicker = () => fireEvent.mouseDown(combobox());
const earlier = () => screen.getByTestId("earlier-versions");
const optionTexts = () =>
  screen.getAllByRole("option").map((o) => o.textContent);
const enabled = (option) => option.getAttribute("aria-disabled") !== "true";

// What Run says about the dataset version for these form values.
const versionBlocks = (values, ctx = {}) =>
  serverExperimentBody(experimentBodyOfValues(values, { datasetId: "d1" }), {
    columns: [],
    ...ctx,
  })
    .blocks.filter((b) => b.field === "dataset_version_id")
    .map((b) => b.text);

const labelOf = (v, kind) =>
  [
    kind,
    fDateTime(v.created_at, "MMM d, yyyy, HH:mm"),
    v.description || null,
    `${v.item_count} examples`,
  ]
    .filter(Boolean)
    .join(" · ");

afterEach(() => {
  setLaunchOverrideForTest(null);
  unreleased.shown = true;
});

describe("Dataset version picker in a launch build, as shipped (unreleased parts shown)", () => {
  beforeEach(() => {
    unreleased.shown = true;
    setLaunchOverrideForTest({ launch: true, experimentsBasic: true });
    h.ctx = launchContext();
    h.unsaved = { sentence: null };
    formValues = null;
  });

  it("offers the dataset's current rows as the one choice, and nothing pins the run to an earlier version", () => {
    const { container } = render(<Host />);
    expect(picker().className).not.toMatch(/Mui-disabled/);
    expect(combobox().textContent).toBe(CURRENT_ROWS);
    expect(combobox().getAttribute("tabindex")).toBe("0");
    expect(screen.getByText(HELPER, { exact: true })).toBeTruthy();
    expect(container.textContent).not.toMatch(/pinned to this version/);
    expect(container.textContent).not.toMatch(/Latest|Loading versions/);
    expect(screen.queryByTestId("earlier-versions")).toBeNull();
    openPicker();
    expect(screen.getAllByRole("option").map((o) => o.textContent)).toEqual([
      CURRENT_ROWS,
    ]);
    expect(
      experimentBodyOfValues(formValues, { datasetId: "d1" })
        .dataset_version_id,
    ).toBeUndefined();
    expect(versionBlocks(formValues, { currentRowsChoice: true })).toEqual([]);
  });

  it("earlier versions are listed under Current rows, and one line counts them", () => {
    h.ctx = launchContext({ savedVersions: [COPY_A, COPY_B] });
    const { container } = render(<Host />);
    expect(earlier().textContent).toBe(
      `2 earlier versions, copied when experiments were created. ${PICK_LINE}`,
    );
    const helper = screen.getByText(HELPER, { exact: true });
    expect(combobox().getAttribute("aria-describedby")).toBe(
      `${helper.id} ${earlier().id}`,
    );
    expect(container.textContent).not.toContain(NOT_IN_RELEASE);
    expect(container.textContent).not.toMatch(/isn't available/);
    openPicker();
    expect(optionTexts()).toEqual([
      CURRENT_ROWS,
      labelOf(COPY_A, "Copied for an experiment"),
      labelOf(COPY_B, "Copied for an experiment"),
    ]);
    screen.getAllByRole("option").forEach((o) => expect(enabled(o)).toBe(true));
  });

  it.each([
    [
      "copies and saved versions",
      [COPY_A, COPY_B, SAVED],
      `3 earlier versions, 2 of them copied when experiments were created. ${PICK_LINE}`,
    ],
    [
      "one copy",
      [COPY_A],
      `1 earlier version, copied when an experiment was created. ${PICK_LINE}`,
    ],
    ["one saved version", [SAVED], `1 earlier version. ${PICK_LINE}`],
  ])(
    "the line says how many earlier versions are copies (%s)",
    (_l, list, line) => {
      h.ctx = launchContext({ savedVersions: list });
      render(<Host />);
      expect(earlier().textContent).toBe(line);
    },
  );

  it("a full page of versions says there may be more and that the newest are listed", () => {
    const page = Array.from({ length: 100 }, (_, i) =>
      copyFor(
        `0b5b7b9e-${String(i).padStart(4, "0")}-4c8f-9a8e-0e2f8d1d2c3a`,
        COPY_A.created_at,
      ),
    );
    h.ctx = launchContext({ savedVersions: page });
    render(<Host />);
    expect(earlier().textContent).toBe(
      `100 or more earlier versions; the newest 100 are listed. ${PICK_LINE}`,
    );
    openPicker();
    expect(screen.getAllByRole("option")).toHaveLength(101);
  });

  it("while the list loads, a placeholder line holds its place", () => {
    h.ctx = launchContext({ savedVersionsLoading: true });
    render(<Host />);
    expect(screen.getByTestId("earlier-versions-loading")).toBeTruthy();
    expect(earlier().getAttribute("aria-busy")).toBe("true");
    expect(earlier().textContent).toBe("");
    expect(combobox().textContent).toBe(CURRENT_ROWS);
  });

  it("when the list can't be read, it says so and Retry reads it again", () => {
    const retry = vi.fn();
    h.ctx = launchContext({
      savedVersionsFailed: true,
      retrySavedVersions: retry,
    });
    render(<Host />);
    expect(within(earlier()).getByText(FAILED, { exact: true })).toBeTruthy();
    const button = within(earlier()).getByRole("button", { name: "Retry" });
    expect(button.getAttribute("type")).toBe("button");
    fireEvent.click(button);
    expect(retry).toHaveBeenCalledTimes(1);
    expect(combobox().textContent).toBe(CURRENT_ROWS);
  });

  it.each([
    ["no earlier version is chosen", {}, FAILED, FAILED_PICKED, CURRENT_ROWS],
    [
      "an earlier version came with the form",
      { datasetVersionId: UNLISTED },
      FAILED_PICKED,
      FAILED,
      "Earlier version",
    ],
  ])(
    "when the list can't be read and %s, the line names the rows the experiment uses",
    (_l, defaults, line, otherLine, shown) => {
      h.ctx = launchContext({ savedVersionsFailed: true });
      render(<Host defaults={defaults} />);
      expect(combobox().textContent).toBe(shown);
      expect(within(earlier()).getByText(line, { exact: true })).toBeTruthy();
      expect(within(earlier()).queryByText(otherLine, { exact: true })).toBe(
        null,
      );
      expect(
        experimentBodyOfValues(formValues, { datasetId: "d1" })
          .dataset_version_id,
      ).toBe(defaults.datasetVersionId);
      expect(
        within(earlier()).getByRole("button", { name: "Retry" }),
      ).toBeTruthy();
    },
  );

  it("when the list can't be read, going back to Current rows says the current rows are used", () => {
    h.ctx = launchContext({ savedVersionsFailed: true });
    render(<Host defaults={{ datasetVersionId: UNLISTED }} />);
    expect(
      within(earlier()).getByText(FAILED_PICKED, { exact: true }),
    ).toBeTruthy();
    openPicker();
    fireEvent.click(screen.getByRole("option", { name: CURRENT_ROWS }));
    expect(formValues.datasetVersionId).toBe("");
    expect(combobox().textContent).toBe(CURRENT_ROWS);
    expect(within(earlier()).getByText(FAILED, { exact: true })).toBeTruthy();
    expect(
      within(earlier()).queryByText(FAILED_PICKED, { exact: true }),
    ).toBeNull();
    expect(
      experimentBodyOfValues(formValues, { datasetId: "d1" })
        .dataset_version_id,
    ).toBeUndefined();
  });

  it("choosing an earlier version runs on its rows: the body sends its id and the helper says its rows are copied", () => {
    h.ctx = launchContext({ savedVersions: [COPY_A, COPY_B] });
    render(<Host />);
    openPicker();
    fireEvent.click(
      screen.getByRole("option", {
        name: labelOf(COPY_B, "Copied for an experiment"),
      }),
    );
    expect(formValues.datasetVersionId).toBe(COPY_B.id);
    expect(combobox().textContent).toBe("Copied for an experiment");
    expect(screen.getByText(PICKED_HELPER, { exact: true })).toBeTruthy();
    expect(screen.queryByText(HELPER, { exact: true })).toBeNull();
    expect(
      experimentBodyOfValues(formValues, { datasetId: "d1" })
        .dataset_version_id,
    ).toBe(COPY_B.id);

    openPicker();
    fireEvent.click(screen.getByRole("option", { name: CURRENT_ROWS }));
    expect(formValues.datasetVersionId).toBe("");
    expect(combobox().textContent).toBe(CURRENT_ROWS);
    expect(screen.getByText(HELPER, { exact: true })).toBeTruthy();
    expect(
      experimentBodyOfValues(formValues, { datasetId: "d1" })
        .dataset_version_id,
    ).toBeUndefined();
  });

  it("an earlier version that came with the form is chosen, with its whole label in the tooltip", () => {
    h.ctx = launchContext({ savedVersions: [COPY_A, COPY_B] });
    const { container } = render(
      <Host defaults={{ datasetVersionId: COPY_B.id }} />,
    );
    expect(combobox().textContent).toBe("Copied for an experiment");
    const label = labelOf(COPY_B, "Copied for an experiment");
    expect(label).toContain("2026");
    expect(
      screen.getByTestId("picked-earlier-version").getAttribute("title"),
    ).toBe(label);
    expect(container.textContent).not.toContain(NOT_IN_RELEASE);
    expect(screen.getByText(PICKED_HELPER, { exact: true })).toBeTruthy();

    openPicker();
    const options = screen.getAllByRole("option");
    expect(options).toHaveLength(3);
    expect(options[2].textContent).toBe(label);
    expect(options[2].getAttribute("aria-selected")).toBe("true");
    expect(enabled(options[2])).toBe(true);
    fireEvent.click(screen.getByRole("option", { name: CURRENT_ROWS }));
    expect(formValues.datasetVersionId).toBe("");
    expect(combobox().textContent).toBe(CURRENT_ROWS);
  });

  it("a saved version's long note is cut to one line in the menu and shown whole in the tooltip", () => {
    const note =
      `Holdout rows before the relabel. ${"Every row was checked by two reviewers. ".repeat(6)}`.trim();
    const saved = { ...SAVED, description: note };
    h.ctx = launchContext({ savedVersions: [saved] });
    render(<Host defaults={{ datasetVersionId: saved.id }} />);
    expect(combobox().textContent).toBe("Saved version");
    const label = labelOf(saved, "Saved version");
    expect(
      screen.getByTestId("picked-earlier-version").getAttribute("title"),
    ).toBe(label);
    openPicker();
    const row = screen.getAllByRole("option")[1];
    const text = within(row).getByText(label, { exact: true });
    expect(text.className).toMatch(/MuiTypography-noWrap/);
  });

  it("an earlier version missing from the list stays chosen and can be kept", () => {
    render(<Host defaults={{ datasetVersionId: UNLISTED }} />);
    expect(combobox().textContent).toBe("Earlier version");
    expect(screen.queryByTestId("earlier-versions")).toBeNull();
    openPicker();
    expect(optionTexts()).toEqual([CURRENT_ROWS, "Earlier version"]);
    expect(enabled(screen.getAllByRole("option")[1])).toBe(true);
    expect(formValues.datasetVersionId).toBe(UNLISTED);
  });

  it("Edit shows the rows the experiment was created with, read-only, with no earlier-versions line", () => {
    h.ctx = launchContext({ savedVersions: [COPY_A] });
    const { container } = render(<Host isEditing />);
    expect(picker().className).toMatch(/Mui-disabled/);
    expect(combobox().textContent).toBe(CREATED_ROWS);
    expect(screen.getByText(HELPER, { exact: true })).toBeTruthy();
    expect(screen.queryByTestId("earlier-versions")).toBeNull();
    expect(container.textContent).not.toContain(CURRENT_ROWS);
  });

  it("unsaved rows still hold Continue with a plain line next to the picker", () => {
    h.unsaved = {
      sentence:
        "2 new rows (not saved yet) × 1 run = 2 tries. Save them as v3 to run them.",
      saveLabel: "Save as v3 and continue",
      saveAndContinue: vi.fn(),
      editNote: vi.fn(),
      nudge: 1,
      note: null,
      saving: false,
    };
    render(<Host />);
    expect(screen.getByTestId("unsaved-rows-continue-note").textContent).toBe(
      'Continue waits for these rows: a run reads saved rows only. Use "Save as v3 and continue".',
    );
    expect(combobox().textContent).toBe(CURRENT_ROWS);
  });
});

describe("Dataset version picker in a launch build with the show-unreleased switch off", () => {
  beforeEach(() => {
    unreleased.shown = false;
    setLaunchOverrideForTest({
      launch: true,
      experimentsBasic: true,
      showUnreleased: false,
    });
    // What the form passes when the build refuses the version list.
    h.ctx = launchContext({ versionsHeld: true });
    h.unsaved = { sentence: null };
    formValues = null;
  });

  it("offers no version choice and no earlier-versions line", () => {
    const { container } = render(<Host />);
    expect(screen.queryByTestId("experiment-version-select")).toBeNull();
    expect(screen.queryByTestId("earlier-versions")).toBeNull();
    expect(container.textContent).not.toContain(CURRENT_ROWS);
  });

  it("a version id that came with the form keeps its Clear selection and the existing Run line", () => {
    render(<Host defaults={{ datasetVersionId: UNLISTED }} />);
    expect(screen.queryByTestId("experiment-version-select")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Clear selection" }));
    expect(formValues.datasetVersionId).toBe("");
    expect(
      versionBlocks({ ...formValues, datasetVersionId: UNLISTED }),
    ).toEqual([SWITCH_OFF_BLOCK]);
  });
});
