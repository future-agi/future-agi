// results-add-to-dataset (DI-06 d): the copy reads one exact try of the run
// (its output and its scores, never an average), so a run with several tries
// shows the try picker on every target and the request sends the picked try.
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
    ],
    isLoading: false,
    isError: false,
  }),
  useGetDatasetColumns: (id) => ({
    isFetched: Boolean(id),
    isError: false,
    data:
      id === "GS"
        ? [{ id: "s-in", name: "input" }]
        : id === "RP"
          ? [
              { id: "t-in", name: "input" },
              { id: "t-out", name: "output" },
            ]
          : [],
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

const renderDialog = (trials) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <AddResultsToDatasetDialog
        open
        onClose={() => {}}
        datasetId="GS"
        datasetName="Golden"
        exampleIds={["e1", "e2"]}
        rows={[]}
        runs={[
          { run_id: "r3a", label: "#3A", trials, task_kind: "prompt" },
          { run_id: "r4a", label: "#4A", trials, task_kind: "prompt" },
        ]}
        baselineId="r3a"
        pills={[]}
        metrics={[]}
      />
    </QueryClientProvider>,
  );
const dialog = () => screen.getByRole("dialog");
const picker = () => within(dialog()).queryByTestId("add-to-dataset-trial");
const pickOption = (combobox, name) => {
  fireEvent.mouseDown(combobox);
  fireEvent.click(screen.getByRole("option", { name }));
};

beforeEach(() => {
  h.sent.length = 0;
});
afterEach(cleanup);

describe("Add to dataset dialog · the try that is copied", () => {
  it("a run with one try has no picker and sends try 1", () => {
    renderDialog(1);
    expect(picker()).toBeNull();
    fireEvent.change(within(dialog()).getByLabelText("New dataset name"), {
      target: { value: "Golden copy" },
    });
    fireEvent.click(
      within(dialog()).getByRole("button", {
        name: "Add 2 rows to Golden copy",
      }),
    );
    expect(h.sent[0]).toMatchObject({ targetId: "new", trial: 1 });
  });

  it("a new dataset: the picker shows with the row's expected kept and sends the picked try", () => {
    renderDialog(3);
    expect(picker()).not.toBeNull();
    // MUI's Select adds a zero-width space after its value.
    expect(picker().textContent).toMatch(/Copy try[\s\u200b]*1[\s\u200b]*of 3/);
    pickOption(within(picker()).getByRole("combobox", { name: "Try" }), "2");
    fireEvent.change(within(dialog()).getByLabelText("New dataset name"), {
      target: { value: "Try two" },
    });
    fireEvent.click(
      within(dialog()).getByRole("button", { name: "Add 2 rows to Try two" }),
    );
    expect(h.sent[0]).toMatchObject({
      targetId: "new",
      trial: 2,
      expected_from: "keep",
    });
  });

  it("another dataset: the picker shows too and the picked try is sent", () => {
    renderDialog(3);
    fireEvent.click(within(dialog()).getByLabelText("Another dataset"));
    expect(picker()).not.toBeNull();
    pickOption(
      within(dialog()).getByRole("combobox", { name: "Target dataset" }),
      "Refund policy QA",
    );
    pickOption(within(picker()).getByRole("combobox", { name: "Try" }), "3");
    fireEvent.click(
      within(dialog()).getByRole("button", {
        name: "Add 2 rows to Refund policy QA",
      }),
    );
    expect(h.sent[0]).toMatchObject({ targetId: "RP", trial: 3 });
    expect(h.sent[0].column_map).toEqual({ "s-in": "t-in", output: "t-out" });
  });
});
