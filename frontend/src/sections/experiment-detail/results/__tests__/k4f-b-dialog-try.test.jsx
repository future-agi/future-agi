// integration review fix (DI-06 d): the try picker shows only when the action
// reads the run's output (never while only tagging or keeping the row's
// expected), and the before → after preview prints the picked try's output —
// the text the save writes.
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
const rows = [
  {
    example_id: "e1",
    row_order: 18,
    row_label: "Row 18",
    expected: "Old expected 18",
    results: {
      r4a: {
        output: "Try one of row 18",
        trial_outputs: [
          "Try one of row 18",
          "Try two of row 18",
          "Try three of row 18",
        ],
      },
    },
  },
  {
    example_id: "e2",
    row_order: 20,
    row_label: "Row 20",
    expected: "Old expected 20",
    results: {
      r4a: {
        output: "Try one of row 20",
        trial_outputs: [
          "Try one of row 20",
          "Try two of row 20",
          "Try three of row 20",
        ],
      },
    },
  },
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
          rows={rows}
          runs={runs}
          baselineId="r3a"
          pills={[]}
          splits={[{ id: "H", name: "hard" }]}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
const dialog = () => screen.getByRole("dialog");
const picker = () => within(dialog()).queryByTestId("add-to-dataset-trial");
const pickOption = (combobox, name) => {
  fireEvent.mouseDown(combobox);
  fireEvent.click(screen.getByRole("option", { name }));
};
const useOutput = () =>
  fireEvent.click(
    within(dialog()).getByLabelText("Use #4A's output as the expected value"),
  );

describe("Add to dataset dialog · try picker (DI-06 d review)", () => {
  it("this dataset: no picker while only tagging; shown when the output is the expected", () => {
    renderDialog();
    expect(picker()).toBeNull();
    fireEvent.click(within(dialog()).getByText("hard"));
    expect(picker()).toBeNull();
    useOutput();
    expect(picker()).not.toBeNull();
    expect(picker().textContent).toMatch(
      /Output of try[\s\u200b]*1[\s\u200b]*of 3/,
    );
  });

  it("the preview prints the picked try's output, and the body sends that try", () => {
    renderDialog();
    useOutput();
    const preview = () =>
      within(dialog()).getByTestId("add-to-dataset-expected-preview");
    expect(preview().textContent).toContain("Try one of row 18");
    expect(preview().textContent).toContain("#4A's try 1 output");
    pickOption(within(picker()).getByRole("combobox"), "2");
    expect(preview().textContent).toContain("Try two of row 18");
    expect(preview().textContent).toContain("Try two of row 20");
    expect(preview().textContent).not.toContain("Try one of row");
    expect(preview().textContent).toContain("#4A's try 2 output");
    fireEvent.click(
      within(dialog()).getByRole("button", {
        name: /^Replace 2 expected values/,
      }),
    );
    expect(sent.at(-1)).toMatchObject({
      targetId: "GS",
      trial: 2,
      expected_from: "output",
    });
  });

  it("a try picked, then the output unticked: the picker hides and the tag sends try 1", () => {
    renderDialog();
    useOutput();
    pickOption(within(picker()).getByRole("combobox"), "3");
    useOutput();
    expect(picker()).toBeNull();
    fireEvent.click(within(dialog()).getByText("hard"));
    fireEvent.click(
      within(dialog()).getByRole("button", { name: /^Tag 2 rows as hard/ }),
    );
    expect(sent.at(-1)).toMatchObject({ trial: 1, expected_from: "keep" });
  });

  it.each([["Another dataset"], ["New dataset"]])(
    "%s: no picker with Keep or Leave empty; shown with output and needs review",
    (label) => {
      renderDialog();
      fireEvent.click(within(dialog()).getByLabelText(label));
      expect(picker()).toBeNull();
      fireEvent.click(
        within(dialog()).getByLabelText("Leave empty to fill in later"),
      );
      expect(picker()).toBeNull();
      fireEvent.click(
        within(dialog()).getByLabelText(
          "Use #4A's output as the expected value",
        ),
      );
      expect(picker()).not.toBeNull();
      fireEvent.click(
        within(dialog()).getByLabelText(
          "Use #4A's reply as expected · needs review",
        ),
      );
      expect(picker()).not.toBeNull();
      fireEvent.click(
        within(dialog()).getByLabelText("Keep the row's expected"),
      );
      expect(picker()).toBeNull();
    },
  );
});
