// results-add-to-dataset: what the dialog does with the server's answer.
// 200: every row added — a success toast whose Open goes to the dataset, and
// the selection is cleared. 207: some rows added — the dialog stays open on
// the counts and the rows that were not added (nothing dropped silently),
// offers the dataset and a retry of only those rows into it, and keeps the
// selection on close. Only rows that a second send can add are sent again
// (a try with no finished result yet); the others stay listed.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "src/utils/test-utils";

const h = vi.hoisted(() => ({ sent: [], answers: [], navigate: vi.fn() }));

vi.mock("src/api/develop/develop-detail", async (importOriginal) => ({
  ...(await importOriginal()),
  useDevelopDatasetList: () => ({
    data: [{ datasetId: "GS", name: "Golden" }],
    isLoading: false,
    isError: false,
  }),
  useGetDatasetColumns: (id) => ({
    isFetched: Boolean(id),
    isError: false,
    data: id === "GS" ? [{ id: "s-in", name: "input" }] : [],
  }),
}));
vi.mock("src/api/develop/run-results", async (importOriginal) => ({
  ...(await importOriginal()),
  useAddResultsToDataset: (_id, opts) => ({
    isPending: false,
    mutate: (vars) => {
      h.sent.push(vars);
      const answer = h.answers.shift();
      opts.onSuccess({ status: answer.status, data: answer.data }, vars);
    },
  }),
}));
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
  closeSnackbar: vi.fn(),
}));
vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal()),
  useNavigate: () => h.navigate,
}));

import AddResultsToDatasetDialog from "../AddResultsToDatasetDialog";

const runs = [
  { run_id: "r3a", label: "#3A", trials: 1, task_kind: "prompt" },
  { run_id: "r4a", label: "#4A", trials: 1, task_kind: "prompt" },
];
const metrics = [{ key: "m-g", name: "Groundedness", runs: ["r4a"] }];
const rows = [
  { example_id: "e1", row_label: "18", row_order: 17 },
  { example_id: "e2", row_label: "20", row_order: 19 },
  { example_id: "e3", row_label: "21", row_order: 20 },
];
const resolved = {
  "s-in": "c-in",
  output: "c-out",
  "score:m-g": "c-g",
};
const answer = (status, result) => ({
  status,
  data: {
    status: true,
    result: {
      dataset_id: "NEW1",
      dataset_name: "Regressions",
      dataset_url: "/dashboard/develop/NEW1?tab=data",
      created: true,
      trial: 1,
      column_map: resolved,
      skipped_columns: [],
      rows: [],
      failures: [],
      ...result,
    },
  },
});
const scoreMissing = {
  example_id: "e2",
  code: "score_unavailable",
  message: "A selected score is not available for this try.",
};
const notFinished = (exampleId) => ({
  example_id: exampleId,
  code: "result_unavailable",
  message: "This try has no completed result to copy.",
});

const renderDialog = (onClose, exampleIds = ["e1", "e2"]) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <AddResultsToDatasetDialog
        open
        onClose={onClose}
        datasetId="GS"
        datasetName="Golden"
        exampleIds={exampleIds}
        rows={rows}
        runs={runs}
        baselineId="r3a"
        pills={[]}
        metrics={metrics}
      />
    </QueryClientProvider>,
  );
const dialog = () => screen.getByRole("dialog");
const addRegressions = (count = 2) => {
  fireEvent.change(within(dialog()).getByLabelText("New dataset name"), {
    target: { value: "Regressions" },
  });
  fireEvent.click(within(dialog()).getByLabelText("Copy Groundedness (score)"));
  fireEvent.click(
    within(dialog()).getByRole("button", {
      name: `Add ${count} rows to Regressions`,
    }),
  );
};
const lastToast = () => enqueueSnackbar.mock.calls.at(-1);

beforeEach(() => {
  h.sent.length = 0;
  h.answers.length = 0;
  h.navigate.mockReset();
  enqueueSnackbar.mockReset();
});
afterEach(cleanup);

describe("Add to dataset dialog · the server's answer", () => {
  it("200: a success toast opens the dataset and the selection is cleared", () => {
    h.answers.push(
      answer(200, {
        outcome: "completed",
        rows_requested: 2,
        rows_added: 2,
        rows_failed: 0,
      }),
    );
    const onClose = vi.fn();
    renderDialog(onClose);
    addRegressions();
    expect(onClose).toHaveBeenCalledWith(true);
    const [text, options] = lastToast();
    expect(text).toBe("2 rows added to Regressions");
    expect(options.variant).toBe("success");
    render(options.action("toast-1"));
    fireEvent.click(screen.getByRole("button", { name: "Open" }));
    expect(h.navigate).toHaveBeenCalledWith("/dashboard/develop/NEW1?tab=data");
  });

  it("207: stays open with both counts and every row not added; Done keeps the selection", () => {
    h.answers.push(
      answer(207, {
        outcome: "partial",
        rows_requested: 2,
        rows_added: 1,
        rows_failed: 1,
        failures: [scoreMissing],
      }),
    );
    const onClose = vi.fn();
    renderDialog(onClose);
    addRegressions();
    expect(onClose).not.toHaveBeenCalled();
    expect(enqueueSnackbar).not.toHaveBeenCalled();
    expect(within(dialog()).getByText("Added 1 of 2 rows")).toBeInTheDocument();
    const partial = within(dialog()).getByTestId("add-to-dataset-partial");
    expect(partial.textContent).toContain(
      "Added 1 of 2 rows to Regressions. 1 could not be copied.",
    );
    expect(partial.textContent).toContain(
      "1 row: A chosen score was not given for this try.",
    );
    expect(partial.textContent).toContain("Row 20");
    // A missing score comes back the same on a second send: no retry.
    expect(
      within(dialog()).queryByRole("button", { name: /^Try / }),
    ).toBeNull();
    expect(
      within(dialog()).getByTestId("add-to-dataset-retry-note").textContent,
    ).toBe("Sending these rows again would fail the same way.");
    fireEvent.click(
      within(dialog()).getByRole("button", { name: "Open Regressions" }),
    );
    expect(h.navigate).toHaveBeenCalledWith("/dashboard/develop/NEW1?tab=data");
    expect(onClose).toHaveBeenLastCalledWith(false);
  });

  it("207 then a retry: only the failed rows go to the dataset just made, with its columns", () => {
    h.answers.push(
      answer(207, {
        outcome: "partial",
        rows_requested: 2,
        rows_added: 1,
        rows_failed: 1,
        failures: [notFinished("e2")],
      }),
      answer(200, {
        created: false,
        outcome: "completed",
        rows_requested: 1,
        rows_added: 1,
        rows_failed: 0,
      }),
    );
    const onClose = vi.fn();
    renderDialog(onClose);
    addRegressions();
    fireEvent.click(
      within(dialog()).getByRole("button", {
        name: "Try the failed row again",
      }),
    );
    expect(h.sent).toHaveLength(2);
    const retry = h.sent[1];
    expect(retry).toMatchObject({
      targetId: "NEW1",
      mode: "copy",
      run_id: "r4a",
      example_ids: ["e2"],
      trial: 1,
      score_columns: ["m-g"],
    });
    expect(retry.column_map).toEqual(resolved);
    expect(retry).not.toHaveProperty("new_dataset_name");
    // Both rows are in now: one toast with the total, selection cleared.
    expect(lastToast()[0]).toBe("2 rows added to Regressions");
    expect(onClose).toHaveBeenCalledWith(true);
  });

  it("207 with mixed reasons: only the unfinished try is sent again; the rest stay listed and the dialog stays open", () => {
    h.answers.push(
      answer(207, {
        outcome: "partial",
        rows_requested: 3,
        rows_added: 1,
        rows_failed: 2,
        failures: [scoreMissing, notFinished("e3")],
      }),
      answer(200, {
        created: false,
        outcome: "completed",
        rows_requested: 1,
        rows_added: 1,
        rows_failed: 0,
      }),
    );
    const onClose = vi.fn();
    renderDialog(onClose, ["e1", "e2", "e3"]);
    addRegressions(3);
    expect(
      within(dialog()).getByTestId("add-to-dataset-retry-note").textContent,
    ).toBe(
      "Only rows that a second send can add are sent again. The others would fail the same way.",
    );
    fireEvent.click(
      within(dialog()).getByRole("button", { name: "Try 1 row again" }),
    );
    expect(h.sent).toHaveLength(2);
    expect(h.sent[1]).toMatchObject({
      targetId: "NEW1",
      example_ids: ["e3"],
    });
    // Row 20 was never added: no success toast, the selection is kept.
    expect(enqueueSnackbar).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    expect(within(dialog()).getByText("Added 2 of 3 rows")).toBeInTheDocument();
    const partial = within(dialog()).getByTestId("add-to-dataset-partial");
    expect(partial.textContent).toContain(
      "Added 2 of 3 rows to Regressions. 1 could not be copied.",
    );
    expect(partial.textContent).toContain(
      "1 row: A chosen score was not given for this try.",
    );
    expect(partial.textContent).toContain("Row 20");
    expect(partial.textContent).not.toContain("Row 21");
    expect(
      within(dialog()).queryByRole("button", { name: /^Try / }),
    ).toBeNull();
  });

  it("a 2xx answer that does not say what was added is never read as success", () => {
    h.answers.push({ status: 200, data: { status: true } });
    const onClose = vi.fn();
    renderDialog(onClose);
    addRegressions();
    expect(onClose).not.toHaveBeenCalled();
    expect(enqueueSnackbar).not.toHaveBeenCalled();
    expect(
      within(dialog()).getByTestId("add-to-dataset-unknown"),
    ).toBeInTheDocument();
  });
});
