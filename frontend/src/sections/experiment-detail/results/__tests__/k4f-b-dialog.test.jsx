// integration (DI-06): the Add to dataset dialog — another dataset's Columns step
// (Skip all → disabled with the reason; one mapped → enabled), the try
// picker on a run with 3 tries, the title that follows the action, and the
// R6 body (column_map, trial).
import { describe, expect, it, vi } from "vitest";
import React from "react";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const sent = [];
vi.mock("src/api/develop/develop-detail", () => ({
  useDevelopDatasetList: () => ({
    data: [
      { datasetId: "GS", name: "Golden" },
      { datasetId: "RP", name: "Refund policy QA" },
    ],
  }),
  useGetDatasetColumns: (id) => ({
    isFetched: Boolean(id),
    data:
      id === "GS"
        ? [
            { id: "s-in", name: "input" },
            { id: "s-exp", name: "expected_reply" },
          ]
        : id === "RP"
          ? [
              { id: "t-q", name: "question" },
              { id: "t-in", name: "input" },
            ]
          : [],
  }),
}));
vi.mock("src/api/develop/dataset-versions", () => ({
  datasetVersionsKey: (id) => ["v", id],
  latestOf: (list) => (list ?? []).slice(-1)[0] ?? null,
  useDatasetVersions: () => ({ data: [{ number: 6 }] }),
}));
vi.mock("src/api/develop/run-results", () => ({
  useAddResultsToDataset: () => ({
    isPending: false,
    mutate: (vars) => sent.push(vars),
  }),
}));
vi.mock("notistack", () => ({
  enqueueSnackbar: () => {},
  closeSnackbar: () => {},
}));
import AddResultsToDatasetDialog from "../AddResultsToDatasetDialog";

const runs = [
  { run_id: "r3a", label: "#3A", trials: 3 },
  { run_id: "r4a", label: "#4A", trials: 3 },
];
const renderDialog = () =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
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
          splits={[{ id: "H", name: "hard" }]}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
const dialog = () => screen.getByRole("dialog");
const pickOption = (combobox, name) => {
  fireEvent.mouseDown(combobox);
  fireEvent.click(screen.getByRole("option", { name }));
};

describe("Add to dataset dialog (DI-06)", () => {
  it("title follows the action and the try picker reads 'of 3'", () => {
    renderDialog();
    expect(within(dialog()).getByText("Tag 2 rows with a split")).toBeTruthy();
    fireEvent.click(
      within(dialog()).getByLabelText("Use #4A's output as the expected value"),
    );
    expect(
      within(dialog()).getByText("Use #4A's output as expected (2 rows)"),
    ).toBeTruthy();
    const trial = within(dialog()).getByTestId("add-to-dataset-trial");
    // MUI's Select adds a zero-width space after its value.
    expect(trial.textContent).toMatch(
      /Output of try[\s\u200b]*1[\s\u200b]*of 3/,
    );
    pickOption(within(trial).getByRole("combobox"), "2");
    fireEvent.click(within(dialog()).getByText("hard"));
    expect(
      within(dialog()).getByText("Tag 2 rows and update expected"),
    ).toBeTruthy();
    fireEvent.click(
      within(dialog()).getByRole("button", { name: /^Tag 2 rows as hard/ }),
    );
    expect(sent.at(-1)).toMatchObject({ targetId: "GS", trial: 2 });
  });

  it("Columns step: Skip all disables with the reason; one map enables", () => {
    renderDialog();
    fireEvent.click(within(dialog()).getByLabelText("Another dataset"));
    pickOption(
      within(dialog()).getAllByRole("combobox")[0],
      "Refund policy QA",
    );
    expect(within(dialog()).getByText("Add 2 rows to a dataset")).toBeTruthy();
    const step = within(dialog()).getByTestId("add-to-dataset-columns");
    // `input` maps by name by default.
    expect(
      within(step).getByTestId("add-to-dataset-columns-count").textContent,
    ).toBe("1 of 2 columns map");
    const selects = within(step).getAllByRole("combobox");
    pickOption(selects[0], "Skip");
    expect(
      within(step).getByTestId("add-to-dataset-columns-count").textContent,
    ).toBe("0 of 2 columns map");
    expect(
      within(dialog()).getByTestId("add-to-dataset-reason").textContent,
    ).toBe(
      "None of Golden's columns map to Refund policy QA. Pick a column for at least one.",
    );
    const add = within(dialog()).getByRole("button", {
      name: /^Add 2 rows to Refund policy QA/,
    });
    expect(add.disabled).toBe(true);
    pickOption(within(step).getAllByRole("combobox")[1], "question");
    expect(add.disabled).toBe(false);
    fireEvent.click(add);
    expect(sent.at(-1)).toMatchObject({
      targetId: "RP",
      mode: "copy",
      column_map: { "s-in": null, "s-exp": "t-q" },
    });
  });
});
