// results-add-to-dataset (DI-06 a): a failed copy is said IN the dialog (the
// toast column hides while a dialog covers it): a refused name under the name
// field, a refusal with the server's reason (a 403 from its `detail`), every
// row of an all-rows failure with its reason, and an answer that never came
// back as unknown — never retried on its own, since that can add rows twice.
// Every error here has the shape the app's client rejects with
// (src/utils/axios): the answer's body spread out plus `statusCode`, with no
// `response`.
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

const h = vi.hoisted(() => ({ sent: [], error: null }));

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
  useAddResultsToDataset: (_id, opts) => ({
    isPending: false,
    mutate: (vars) => {
      h.sent.push(vars);
      opts.onError(h.error, vars);
    },
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
const rows = [
  { example_id: "e1", row_label: "18", row_order: 17 },
  { example_id: "e2", row_label: "20", row_order: 19 },
];
const renderDialog = (onClose = () => {}) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <AddResultsToDatasetDialog
        open
        onClose={onClose}
        datasetId="GS"
        datasetName="Golden"
        exampleIds={["e1", "e2"]}
        rows={rows}
        runs={runs}
        baselineId="r3a"
        pills={[]}
        metrics={[]}
      />
    </QueryClientProvider>,
  );
const dialog = () => screen.getByRole("dialog");
const pickOption = (combobox, name) => {
  fireEvent.mouseDown(combobox);
  fireEvent.click(screen.getByRole("option", { name }));
};
// The app client's rejection for an answer with a status.
const appError = (statusCode, body = {}) => ({
  ...body,
  statusCode,
  transportCode: statusCode >= 500 ? "ERR_BAD_RESPONSE" : "ERR_BAD_REQUEST",
  responseHeaders: { "x-request-id": "req-1" },
});
// The server's refusal envelope (detail, message, error and result agree).
const refusal = (status, detail, extra = {}) =>
  appError(status, {
    status: false,
    type: "client_error",
    code: "error",
    detail,
    message: detail,
    error: detail,
    result: detail,
    ...extra,
  });
// No answer at all: no status, only the client's own words.
const noAnswer = () => ({
  message: "Network Error",
  statusCode: undefined,
  transportCode: "ERR_NETWORK",
  responseHeaders: {},
});
const addNew = (name) => {
  fireEvent.change(within(dialog()).getByLabelText("New dataset name"), {
    target: { value: name },
  });
  fireEvent.click(
    within(dialog()).getByRole("button", { name: `Add 2 rows to ${name}` }),
  );
};
const addToOther = () => {
  fireEvent.click(within(dialog()).getByLabelText("Another dataset"));
  pickOption(
    within(dialog()).getByRole("combobox", { name: "Target dataset" }),
    "Refund policy QA",
  );
  fireEvent.click(
    within(dialog()).getByRole("button", {
      name: "Add 2 rows to Refund policy QA",
    }),
  );
};

beforeEach(() => {
  h.sent.length = 0;
  h.error = null;
});
afterEach(cleanup);

describe("Add to dataset dialog · failures said in the dialog", () => {
  it("a name the server refuses is said under the name field", () => {
    h.error = refusal(
      400,
      "A dataset with this name already exists. Choose another name.",
    );
    renderDialog();
    addNew("Refund policy QA");
    expect(
      within(dialog()).getByTestId("add-to-dataset-name-error").textContent,
    ).toBe("A dataset with this name already exists. Choose another name.");
    expect(
      within(dialog()).queryByTestId("add-to-dataset-server-error"),
    ).toBeNull();
    expect(within(dialog()).queryByTestId("add-to-dataset-unknown")).toBeNull();
  });

  it("a 403 says the server's reason from detail; a new target clears it", () => {
    h.error = refusal(403, "Workspace write access required.", {
      code: "permission_denied",
      result: { unexpected: true },
      message: "",
      error: "",
    });
    renderDialog();
    addToOther();
    expect(
      within(dialog()).getByTestId("add-to-dataset-server-error").textContent,
    ).toContain("Workspace write access required.");
    fireEvent.click(within(dialog()).getByLabelText("New dataset"));
    expect(
      within(dialog()).queryByTestId("add-to-dataset-server-error"),
    ).toBeNull();
  });

  it("a 403 without a reason says so plainly", () => {
    // An answer without a body: the client fills in its own message.
    h.error = appError(403, { message: "Request failed with status code 403" });
    renderDialog();
    addToOther();
    const error = within(dialog()).getByTestId("add-to-dataset-server-error");
    expect(error.textContent).toContain(
      "You do not have permission to add rows to this dataset.",
    );
    expect(error.textContent).not.toContain("status code");
  });

  it("no rows added: every row is listed with its reason and no dataset is offered", () => {
    h.error = refusal(
      422,
      "No rows were added. Review the failed rows before trying again.",
      {
        code: "no_rows_added",
        result: {
          dataset_id: null,
          dataset_name: null,
          dataset_url: null,
          created: false,
          outcome: "failed",
          rows_requested: 2,
          rows_added: 0,
          rows_failed: 2,
          trial: 1,
          column_map: {},
          skipped_columns: [],
          rows: [],
          failures: [
            {
              example_id: "e1",
              code: "result_unavailable",
              message: "This try has no completed result to copy.",
            },
            {
              example_id: "e2",
              code: "output_unavailable",
              message: "This result has no saved output.",
            },
          ],
        },
      },
    );
    const onClose = vi.fn();
    renderDialog(onClose);
    addNew("Misses");
    const failed = within(dialog()).getByTestId("add-to-dataset-failed");
    expect(failed.textContent).toContain(
      "No rows were added and no dataset was created. 2 rows could not be copied.",
    );
    const list = within(failed).getByTestId("add-to-dataset-failures");
    expect(list.textContent).toContain(
      "1 row: This try has no finished result to copy.",
    );
    expect(list.textContent).toContain("Row 18");
    expect(list.textContent).toContain(
      "1 row: This result has no saved output.",
    );
    expect(list.textContent).toContain("Row 20");
    expect(within(dialog()).queryByTestId("add-to-dataset-unknown")).toBeNull();
    expect(
      within(dialog()).queryByRole("button", { name: /^Open/ }),
    ).toBeNull();
    expect(onClose).not.toHaveBeenCalled();
    // The choices stay as they were, ready to change and send again.
    expect(within(dialog()).getByLabelText("New dataset name").value).toBe(
      "Misses",
    );
  });

  it("no answer: the outcome is unknown, nothing is resent on its own, and sending again is a deliberate step", () => {
    h.error = noAnswer();
    renderDialog();
    addToOther();
    expect(h.sent).toHaveLength(1);
    const unknown = within(dialog()).getByTestId("add-to-dataset-unknown");
    expect(unknown.textContent).toContain(
      "We could not confirm whether the rows were added. Check Refund policy QA before you send them again, so they are not added twice.",
    );
    expect(
      within(unknown).getByRole("button", { name: "Check the dataset" }),
    ).toBeInTheDocument();
    expect(unknown.textContent).not.toContain("Network Error");
    const again = within(dialog()).getByRole("button", { name: "Send again" });
    expect(h.sent).toHaveLength(1);
    fireEvent.click(again);
    expect(h.sent).toHaveLength(2);
  });

  it("no answer for a new dataset, then its name is refused: the name may be the dataset that request made", () => {
    h.error = noAnswer();
    renderDialog();
    addNew("Misses");
    expect(
      within(dialog()).getByTestId("add-to-dataset-unknown"),
    ).toBeInTheDocument();
    h.error = refusal(
      400,
      "A dataset with this name already exists. Choose another name.",
    );
    fireEvent.click(
      within(dialog()).getByRole("button", { name: "Send again" }),
    );
    expect(h.sent).toHaveLength(2);
    expect(
      within(dialog()).getByTestId("add-to-dataset-name-error").textContent,
    ).toBe(
      'A dataset named "Misses" may already have been made by the request we could not confirm. Check your datasets before you choose another name.',
    );
    // The warning stays: the rows may be in that dataset already.
    expect(
      within(dialog()).getByTestId("add-to-dataset-unknown"),
    ).toBeInTheDocument();
    // Another name that is taken is the server's plain reason.
    fireEvent.change(within(dialog()).getByLabelText("New dataset name"), {
      target: { value: "Golden" },
    });
    fireEvent.click(
      within(dialog()).getByRole("button", { name: "Send again" }),
    );
    expect(h.sent).toHaveLength(3);
    expect(h.sent[2]).toMatchObject({ new_dataset_name: "Golden" });
    expect(
      within(dialog()).getByTestId("add-to-dataset-name-error").textContent,
    ).toBe("A dataset with this name already exists. Choose another name.");
    // The warning still names the dataset the unconfirmed request went to.
    expect(
      within(dialog()).getByTestId("add-to-dataset-unknown").textContent,
    ).toContain("Check Misses before you send them again");
  });

  it.each([
    [502, { message: "Request failed with status code 502" }, "unknown"],
    [504, { message: "Request failed with status code 504" }, "unknown"],
    [
      503,
      {
        status: false,
        detail: "The rows could not be saved. No rows were added.",
      },
      "refused",
    ],
  ])("a %i answer is read as %s", (status, body, kind) => {
    h.error = appError(status, body);
    renderDialog();
    addToOther();
    if (kind === "unknown")
      expect(
        within(dialog()).getByTestId("add-to-dataset-unknown"),
      ).toBeInTheDocument();
    else
      expect(
        within(dialog()).getByTestId("add-to-dataset-server-error").textContent,
      ).toContain("The rows could not be saved. No rows were added.");
  });
});
