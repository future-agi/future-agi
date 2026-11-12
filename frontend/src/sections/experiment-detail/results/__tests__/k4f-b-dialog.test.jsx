// results-add-to-dataset: the Add to dataset dialog copies the picked result
// rows (inputs, the run's output, the chosen scores) into a new dataset or
// another one through a column map, with mode `copy`. It never offers this
// dataset's split tagging or version saving, which the server refuses.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "src/utils/test-utils";

const h = vi.hoisted(() => ({ sent: [] }));

vi.mock("src/api/develop/develop-detail", async (importOriginal) => ({
  ...(await importOriginal()),
  useDevelopDatasetList: () => ({
    data: [
      { datasetId: "GS", name: "Golden" },
      { datasetId: "RP", name: "Refund policy QA" },
      { datasetId: "NT", name: "Notes" },
      { datasetId: "EM", name: "Empty" },
    ],
    isLoading: false,
    isError: false,
  }),
  useGetDatasetColumns: (id) => ({
    isFetched: Boolean(id),
    isError: false,
    data:
      {
        GS: [
          { id: "s-in", name: "input" },
          { id: "s-exp", name: "expected_reply" },
        ],
        RP: [
          { id: "t-q", name: "question" },
          { id: "t-in", name: "input" },
          { id: "t-out", name: "output" },
          { id: "t-g", name: "Groundedness" },
        ],
        NT: [{ id: "t-note", name: "notes" }],
      }[id] ?? [],
  }),
}));
vi.mock("src/api/develop/run-results", async (importOriginal) => ({
  ...(await importOriginal()),
  useAddResultsToDataset: () => ({
    isPending: false,
    mutate: (vars) => h.sent.push(vars),
  }),
}));
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
  closeSnackbar: vi.fn(),
}));

import AddResultsToDatasetDialog from "../AddResultsToDatasetDialog";

const runs = [
  { run_id: "r3a", label: "#3A", trials: 1, task_kind: "prompt" },
  { run_id: "r4a", label: "#4A", trials: 1, task_kind: "prompt" },
];
const metrics = [
  { key: "m-g", name: "Groundedness", runs: ["r3a", "r4a"] },
  { key: "m-x", name: "Exact match", runs: ["r3a"] },
];
const renderDialog = (props = {}) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <AddResultsToDatasetDialog
        open
        onClose={() => {}}
        datasetId="GS"
        datasetName="Golden"
        exampleIds={["e1", "e2"]}
        rows={[]}
        runs={runs}
        baselineId="r3a"
        pills={[]}
        metrics={metrics}
        {...props}
      />
    </QueryClientProvider>,
  );
const dialog = () => screen.getByRole("dialog");
const pickOption = (combobox, name) => {
  fireEvent.mouseDown(combobox);
  fireEvent.click(screen.getByRole("option", { name }));
};
const columnSelect = (label) =>
  within(dialog()).getByRole("combobox", {
    name: `Target column for ${label}`,
  });
const nameNew = (value) =>
  fireEvent.change(within(dialog()).getByLabelText("New dataset name"), {
    target: { value },
  });

beforeEach(() => {
  h.sent.length = 0;
});
afterEach(cleanup);

describe("Add to dataset dialog · copy into a new dataset", () => {
  it("opens on a new dataset with inputs, output and the run's scores; no tagging of this dataset", () => {
    renderDialog();
    expect(
      within(dialog()).getByText("Add 2 rows to a dataset"),
    ).toBeInTheDocument();
    expect(within(dialog()).getByLabelText("New dataset")).toBeChecked();
    expect(within(dialog()).queryByLabelText(/^This dataset/)).toBeNull();
    expect(within(dialog()).queryByText("Split")).toBeNull();
    expect(within(dialog()).queryByText(/save v\d/)).toBeNull();
    const step = within(dialog()).getByTestId("add-to-dataset-columns");
    // This dataset's columns are the inputs, each its own new column.
    const inputLine = within(step).getByTestId("add-to-dataset-column-s-in");
    expect(within(inputLine).getAllByText("input")).toHaveLength(2);
    expect(within(inputLine).getByLabelText("Copy input")).toBeChecked();
    expect(within(step).getByText("Output (#4A)")).toBeInTheDocument();
    // Only the picked run's scores; a score starts unpicked.
    expect(
      within(step).getByLabelText("Copy Groundedness (score)"),
    ).not.toBeChecked();
    expect(within(step).queryByText(/Exact match/)).toBeNull();
    expect(
      within(step).getByTestId("add-to-dataset-columns-count").textContent,
    ).toBe("3 new columns");
    expect(
      within(dialog()).getByTestId("add-to-dataset-reason").textContent,
    ).toBe("Name the new dataset");
  });

  it("sends mode copy with the named columns and the picked score", () => {
    renderDialog();
    nameNew("Regressions");
    fireEvent.click(
      within(dialog()).getByLabelText("Copy Groundedness (score)"),
    );
    fireEvent.click(
      within(dialog()).getByRole("button", {
        name: "Add 2 rows to Regressions",
      }),
    );
    expect(h.sent).toHaveLength(1);
    const body = h.sent[0];
    expect(body).toMatchObject({
      targetId: "new",
      source_dataset_id: "GS",
      run_id: "r4a",
      example_ids: ["e1", "e2"],
      trial: 1,
      mode: "copy",
      new_dataset_name: "Regressions",
      expected_from: "keep",
      score_columns: ["m-g"],
      version_description: "Added 2 rows from #4A review",
    });
    expect(body.column_map).toEqual({
      "s-in": "input",
      "s-exp": "expected_reply",
      output: "output",
      "score:m-g": "Groundedness",
    });
    for (const refused of ["split_ids", "new_splits", "save_version"])
      expect(body).not.toHaveProperty(refused);
  });

  it("an external run copies the whole input and the row's expected", () => {
    renderDialog({
      runs: [{ run_id: "x1", label: "#2U", trials: 1, task_kind: "external" }],
      baselineId: undefined,
      metrics: [],
    });
    nameNew("Uploads");
    fireEvent.click(
      within(dialog()).getByRole("button", { name: "Add 2 rows to Uploads" }),
    );
    expect(h.sent[0].column_map).toEqual({
      inputs: "input",
      expected: "expected",
      output: "output",
    });
  });

  it("the run's output as the expected value is always copied", () => {
    renderDialog();
    nameNew("Fixes");
    fireEvent.click(
      within(dialog()).getByLabelText(
        "Use #4A's reply as expected · needs review",
      ),
    );
    const expected = within(dialog()).getByLabelText(
      "Copy Expected (#4A's output)",
    );
    expect(expected).toBeChecked();
    expect(expected).toBeDisabled();
    fireEvent.click(
      within(dialog()).getByRole("button", { name: "Add 2 rows to Fixes" }),
    );
    expect(h.sent.at(-1)).toMatchObject({
      expected_from: "output",
      needs_review: true,
      expected_source: "production_output",
    });
    expect(h.sent.at(-1).column_map.expected).toBe("expected");
  });

  it("a run on this dataset's rows has no 'Leave empty': its expected is one of the copied columns, left out by unticking it", () => {
    renderDialog();
    nameNew("Fixes");
    expect(
      within(dialog()).queryByLabelText("Leave empty to fill in later"),
    ).toBeNull();
    expect(
      within(dialog()).getByTestId("add-to-dataset-expected-hint").textContent,
    ).toBe(
      "The row's expected is copied with this dataset's columns. To leave it empty, untick its column under Columns.",
    );
    fireEvent.click(within(dialog()).getByLabelText("Copy expected_reply"));
    fireEvent.click(
      within(dialog()).getByRole("button", { name: "Add 2 rows to Fixes" }),
    );
    expect(h.sent.at(-1)).toMatchObject({ expected_from: "keep" });
    expect(h.sent.at(-1).column_map).toEqual({
      "s-in": "input",
      output: "output",
    });
  });

  it("an external run can leave the expected empty: nothing is sent for it", () => {
    renderDialog({
      runs: [{ run_id: "x1", label: "#2U", trials: 1, task_kind: "external" }],
      baselineId: undefined,
      metrics: [],
    });
    nameNew("Uploads");
    expect(
      within(dialog()).queryByTestId("add-to-dataset-expected-hint"),
    ).toBeNull();
    fireEvent.click(
      within(dialog()).getByLabelText("Leave empty to fill in later"),
    );
    expect(
      within(dialog()).queryByTestId("add-to-dataset-column-expected"),
    ).toBeNull();
    fireEvent.click(
      within(dialog()).getByRole("button", { name: "Add 2 rows to Uploads" }),
    );
    expect(h.sent.at(-1)).toMatchObject({ expected_from: "empty" });
    expect(h.sent.at(-1)).not.toHaveProperty("needs_review");
    expect(h.sent.at(-1).column_map).toEqual({
      inputs: "input",
      output: "output",
    });
  });

  it("only the columns saved with the run are listed and sent; a column added later is named as not copied", () => {
    renderDialog({
      rows: [
        {
          example_id: "e1",
          row_label: "1",
          inputs: { values: { "s-in": "Where is my order?" } },
        },
        {
          example_id: "e2",
          row_label: "2",
          inputs: { values: { "s-in": "Can I get a refund?" } },
        },
      ],
    });
    nameNew("Saved inputs");
    const step = within(dialog()).getByTestId("add-to-dataset-columns");
    expect(
      within(step).queryByTestId("add-to-dataset-column-s-exp"),
    ).toBeNull();
    expect(
      within(step).getByTestId("add-to-dataset-columns-count").textContent,
    ).toBe("2 new columns");
    expect(
      within(dialog()).getByTestId("add-to-dataset-columns-unsaved")
        .textContent,
    ).toBe("Not saved with this run, so not copied: expected_reply.");
    fireEvent.click(
      within(dialog()).getByRole("button", {
        name: "Add 2 rows to Saved inputs",
      }),
    );
    expect(h.sent.at(-1).column_map).toEqual({
      "s-in": "input",
      output: "output",
    });
  });

  it("more than 1,000 picked rows are refused before sending", () => {
    renderDialog({
      exampleIds: Array.from({ length: 1001 }, (_, i) => `e${i}`),
    });
    nameNew("Too many");
    expect(
      within(dialog()).getByTestId("add-to-dataset-reason").textContent,
    ).toBe("Select at most 1,000 rows to copy at a time");
    expect(
      within(dialog()).getByRole("button", {
        name: "Add 1001 rows to Too many",
      }),
    ).toBeDisabled();
  });
});

describe("Add to dataset dialog · copy into another dataset", () => {
  const openOther = (name = "Refund policy QA") => {
    fireEvent.click(within(dialog()).getByLabelText("Another dataset"));
    pickOption(
      within(dialog()).getByRole("combobox", { name: "Target dataset" }),
      name,
    );
  };

  it("maps by name, says a column landing under another name, and sends only mapped columns", () => {
    renderDialog();
    openOther();
    const step = within(dialog()).getByTestId("add-to-dataset-columns");
    // input → input and the output → output by name; scores start skipped.
    expect(
      within(step).getByTestId("add-to-dataset-columns-count").textContent,
    ).toBe("2 of 4 columns map");
    pickOption(columnSelect("input"), "Skip");
    pickOption(columnSelect("expected_reply"), "question");
    pickOption(columnSelect("Groundedness (score)"), "Groundedness");
    // A column landing under another name is said before the add.
    expect(
      within(dialog()).getByTestId("add-to-dataset-columns-cross").textContent,
    ).toContain("expected_reply → question");
    const add = within(dialog()).getByRole("button", {
      name: "Add 2 rows to Refund policy QA",
    });
    expect(add).not.toBeDisabled();
    fireEvent.click(add);
    expect(h.sent[0]).toMatchObject({
      targetId: "RP",
      mode: "copy",
      score_columns: ["m-g"],
    });
    expect(h.sent[0].column_map).toEqual({
      "s-exp": "t-q",
      output: "t-out",
      "score:m-g": "t-g",
    });
    expect(h.sent[0]).not.toHaveProperty("new_dataset_name");
  });

  it("nothing maps: the add is off and says why", () => {
    renderDialog();
    openOther("Notes");
    expect(
      within(dialog()).getByTestId("add-to-dataset-columns-count").textContent,
    ).toBe("0 of 4 columns map");
    expect(
      within(dialog()).getByTestId("add-to-dataset-reason").textContent,
    ).toBe("None of the columns map to Notes. Pick a column for at least one.");
    expect(
      within(dialog()).getByRole("button", { name: "Add 2 rows to Notes" }),
    ).toBeDisabled();
  });

  it("a dataset with no columns says so instead of asking for a pick", () => {
    renderDialog();
    openOther("Empty");
    expect(
      within(dialog()).getByTestId("add-to-dataset-reason").textContent,
    ).toBe(
      "Empty has no columns to copy into. Add a column to it first, or add the rows to a new dataset.",
    );
    expect(
      within(dialog()).getByRole("button", { name: "Add 2 rows to Empty" }),
    ).toBeDisabled();
  });

  it("two lines on one column are refused before sending", () => {
    renderDialog();
    openOther();
    pickOption(columnSelect("expected_reply"), "input");
    expect(
      within(dialog()).getByTestId("add-to-dataset-reason").textContent,
    ).toBe("Two columns go to input. Pick a different column for one of them.");
    fireEvent.click(
      within(dialog()).getByRole("button", {
        name: "Add 2 rows to Refund policy QA",
      }),
    );
    expect(h.sent).toHaveLength(0);
  });

  it("the run's output as the expected value claims the expected column", () => {
    renderDialog();
    openOther();
    fireEvent.click(
      within(dialog()).getByLabelText("Use #4A's output as the expected value"),
    );
    // The target has no `expected` column: the add says what to pick.
    expect(
      within(dialog()).getByTestId("add-to-dataset-reason").textContent,
    ).toBe("Pick a column for the expected value, or keep the row's expected");
    pickOption(columnSelect("Expected (#4A's output)"), "question");
    fireEvent.click(
      within(dialog()).getByRole("button", {
        name: "Add 2 rows to Refund policy QA",
      }),
    );
    expect(h.sent[0]).toMatchObject({ expected_from: "output" });
    expect(h.sent[0].column_map).toMatchObject({ expected: "t-q" });
  });
});
