// integration review fix (DI-06 a): a refusal is said IN the dialog — a name the
// server refuses under the name field, any other sentence as an error line —
// because the toast column hides while a dialog covers it.
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
let refusal = null;
vi.mock("src/api/develop/run-results", () => ({
  useAddResultsToDataset: (_id, opts) => ({
    isPending: false,
    mutate: (vars) => {
      sent.push(vars);
      opts.onError({
        response: { status: refusal.status, data: { result: refusal.text } },
      });
    },
  }),
}));
vi.mock("notistack", () => ({
  enqueueSnackbar: () => {},
  closeSnackbar: () => {},
}));
import AddResultsToDatasetDialog from "../AddResultsToDatasetDialog";

const runs = [
  { run_id: "r3a", label: "#3A", trials: 1 },
  { run_id: "r4a", label: "#4A", trials: 1 },
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

describe("Add to dataset dialog · refusals said in the dialog (DI-06 a review)", () => {
  it("a duplicate name is said under the name field", () => {
    refusal = { status: 400, text: "A dataset with this name already exists." };
    renderDialog();
    fireEvent.click(within(dialog()).getByLabelText("New dataset"));
    fireEvent.change(within(dialog()).getByLabelText("New dataset name"), {
      target: { value: "Refund policy QA" },
    });
    fireEvent.click(
      within(dialog()).getByRole("button", { name: /^Add 2 rows to Refund/ }),
    );
    expect(
      within(dialog()).getByTestId("add-to-dataset-name-error").textContent,
    ).toBe("A dataset with this name already exists.");
    expect(
      within(dialog()).queryByTestId("add-to-dataset-server-error"),
    ).toBeNull();
  });

  it("any other refusal is said as the dialog's error line, cleared by a new target", () => {
    refusal = { status: 409, text: "dataset_version_conflict" };
    renderDialog();
    fireEvent.click(within(dialog()).getByText("hard"));
    fireEvent.click(
      within(dialog()).getByRole("button", { name: /^Tag 2 rows as hard/ }),
    );
    expect(
      within(dialog()).getByTestId("add-to-dataset-server-error").textContent,
    ).toContain("dataset_version_conflict");
    fireEvent.click(within(dialog()).getByLabelText("Another dataset"));
    expect(
      within(dialog()).queryByTestId("add-to-dataset-server-error"),
    ).toBeNull();
  });
});
