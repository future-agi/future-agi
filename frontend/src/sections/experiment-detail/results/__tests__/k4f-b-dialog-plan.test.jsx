// integration review 2 (DI-06 d): the expected update is said before the click as
// it will happen — the struck text is the expected as it stands now (the
// latest version), only the rows that change are counted and listed, rows
// that already hold the try's output are named as left as is, and a save
// that would change nothing is off with its reason.
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
import { expectedPlanOf, latestExpectedOf } from "../addToDatasetModel";

const runs = [
  { run_id: "r3a", label: "#3A", trials: 3 },
  { run_id: "r4a", label: "#4A", trials: 3 },
];
const tries = (n) => ({
  output: `Try one of row ${n}`,
  trial_outputs: [1, 2, 3].map(
    (t) => `Try ${["one", "two", "three"][t - 1]} of row ${n}`,
  ),
});
const row = (id, n, expectedNow, latest = null) => ({
  example_id: id,
  row_order: n,
  row_label: `Row ${n}`,
  expected: {
    value: { expected_reply: expectedNow },
    primary: "expected_reply",
    latest: latest ? { expected_reply: latest } : null,
  },
  results: { r4a: tries(n) },
});
// Row 18: the baseline expected differs from try 2. Row 20: it already
// equals try 2 (the save leaves it as is).
const firstRows = [
  row("e1", 18, "Old expected 18"),
  row("e2", 20, "Try two of row 20"),
];
// After that save: Row 18's latest expected is try 2 (`changed in v7`).
const savedRows = [
  row("e1", 18, "Old expected 18", "Try two of row 18"),
  row("e2", 20, "Try two of row 20"),
];

const renderDialog = (rows, exampleIds = ["e1", "e2"]) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        <AddResultsToDatasetDialog
          open
          onClose={() => {}}
          datasetId="GS"
          datasetName="Golden"
          exampleIds={exampleIds}
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
const pickTry = (name) => {
  fireEvent.mouseDown(
    within(within(dialog()).getByTestId("add-to-dataset-trial")).getByRole(
      "combobox",
    ),
  );
  fireEvent.click(screen.getByRole("option", { name }));
};
const useOutput = () =>
  fireEvent.click(
    within(dialog()).getByLabelText("Use #4A's output as the expected value"),
  );
const preview = () =>
  within(dialog()).getByTestId("add-to-dataset-expected-preview");

describe("expectedPlanOf (DI-06 d review 2)", () => {
  it("reads the latest expected, not the baseline's", () => {
    expect(latestExpectedOf(savedRows[0])).toBe("Try two of row 18");
    expect(latestExpectedOf(firstRows[0])).toBe("Old expected 18");
    expect(latestExpectedOf({ expected: "plain text" })).toBe("plain text");
  });

  it("splits the picked rows into change / same / none / unsure", () => {
    const first = expectedPlanOf({
      rows: firstRows,
      exampleIds: ["e1", "e2"],
      runId: "r4a",
      trial: 2,
    });
    expect(first.change.map((r) => r.example_id)).toEqual(["e1"]);
    expect(first.same.map((r) => r.example_id)).toEqual(["e2"]);
    expect(first.upTo).toBe(1);
    const again = expectedPlanOf({
      rows: savedRows,
      exampleIds: ["e1", "e2"],
      runId: "r4a",
      trial: 2,
    });
    expect(again.upTo).toBe(0);
    expect(again.same).toHaveLength(2);
    // A try the row does not have is left as is.
    const noTry = expectedPlanOf({
      rows: [{ ...firstRows[0], results: { r4a: { output: "only one" } } }],
      exampleIds: ["e1"],
      runId: "r4a",
      trial: 3,
    });
    expect(noTry.none).toHaveLength(1);
    expect(noTry.upTo).toBe(0);
  });

  it("a cut grid output that the expected starts with, or a row not on the page, is unsure", () => {
    const cut = {
      ...firstRows[0],
      expected: {
        value: { expected_reply: "Long reply that goes on" },
        primary: "expected_reply",
      },
      results: { r4a: { output: "Long reply…" } },
    };
    const plan = expectedPlanOf({
      rows: [cut],
      exampleIds: ["e1", "e9"],
      runId: "r4a",
      trial: 1,
    });
    expect(plan.unsure.map((r) => r.example_id)).toEqual(["e1", "e9"]);
    expect(plan.change).toHaveLength(0);
    expect(plan.upTo).toBe(2);
    // A cut output whose start differs from the expected is a sure change.
    const differs = expectedPlanOf({
      rows: [{ ...cut, results: { r4a: { output: "Other start…" } } }],
      exampleIds: ["e1"],
      runId: "r4a",
    });
    expect(differs.change).toHaveLength(1);
  });
});

describe("Add to dataset dialog · the expected update said as it will happen", () => {
  it("first save: counts and lists only Row 18; Row 20 already holds try 2", () => {
    renderDialog(firstRows);
    useOutput();
    pickTry("2");
    const text = preview().textContent;
    expect(text).toContain("Old expected 18");
    expect(text).toContain("Try two of row 18");
    expect(
      within(preview()).getByTestId("add-to-dataset-expected-unchanged")
        .textContent,
    ).toBe("Row 20 already holds this output: left as is.");
    // Row 20 is not listed as a replacement (no struck copy of its text).
    expect(within(preview()).queryByText("Row 20")).toBeNull();
    // The version note names only the row that changes.
    expect(within(dialog()).getByLabelText("Version description").value).toBe(
      "Updated the expected value of 1 row from #4A",
    );
    const save = within(dialog()).getByRole("button", {
      name: "Replace 1 expected value and save v7",
    });
    expect(save).not.toBeDisabled();
    fireEvent.click(save);
    expect(sent.at(-1)).toMatchObject({ trial: 2, expected_from: "output" });
  });

  it("repeat after the save: the struck text is the latest expected and the save is off with its reason", () => {
    renderDialog(savedRows);
    useOutput();
    pickTry("2");
    expect(preview().textContent).not.toContain("Old expected 18");
    expect(
      within(preview()).getByTestId("add-to-dataset-expected-unchanged")
        .textContent,
    ).toBe("2 rows (Row 18, Row 20) already hold this output: left as is.");
    expect(
      within(dialog()).getByRole("button", { name: "Nothing to replace" }),
    ).toBeDisabled();
    // The note never claims a change that is not there.
    expect(within(dialog()).getByLabelText("Version description").value).toBe(
      "",
    );
    expect(
      within(dialog()).getByTestId("add-to-dataset-reason").textContent,
    ).toBe(
      "Nothing to replace: the expected values already equal #4A's try 2 output.",
    );
    // Another try still differs: the save is back on for that row.
    pickTry("3");
    expect(
      within(dialog()).getByRole("button", {
        name: "Replace 2 expected values and save v7",
      }),
    ).not.toBeDisabled();
  });

  it("a picked row not on the page: the button says up to", () => {
    renderDialog(firstRows, ["e1", "e2", "e9"]);
    useOutput();
    pickTry("2");
    expect(
      within(dialog()).getByRole("button", {
        name: "Replace up to 2 expected values and save v7",
      }),
    ).not.toBeDisabled();
  });
});
