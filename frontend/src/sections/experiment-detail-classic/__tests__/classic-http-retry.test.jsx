// SRC-S1b-HTTP-FE-03: Retry cell, Retry failed rows and Add new rows of an
// HTTP experiment (classic experiment Data page). Each is an explicit action
// behind a confirm; a re-send says first that the endpoint may already have
// processed the earlier request. Each action is sent once with its own
// Idempotency-Key and acknowledge_http_side_effects; an unknown outcome is
// recovered only by Check again (same request, same key), never re-sent on
// its own. Authored RED against e0d9585e (no such actions; the server
// refused HTTP row retries).
// r2 (FE-06, FE-07, FE-10, FE-11): closing an unconfirmed action says so in
// words; the "did not finish" line reads the grid's own cell shape; Add new
// rows counts first and states how many requests it sends, notes a selected
// or sampled experiment, and every refusal says nothing was sent.
// r3 (FE-13, FE-15): a 403 refusal shows its reason (carried only in
// `detail`); Add new rows counts new rows by identity through the server's
// read-only preview, states its request_count and submits exactly its
// new_row_ids, even when rows the experiment holds left the dataset.
import React from "react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import process from "node:process";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";

const state = vi.hoisted(() => ({
  rerun: vi.fn(),
  append: vi.fn(),
  preview: vi.fn(),
  readRows: vi.fn(),
  get: vi.fn(),
  toast: vi.fn(),
}));
vi.mock("src/utils/axios", () => ({
  default: { get: (...args) => state.get(...args) },
  endpoints: {
    develop: {
      getDatasetDetail: (id) => `/model-hub/develops/${id}/get-dataset-table/`,
      experiment: {
        experimentDetail: (id) => `/model-hub/experiments/v2/${id}/rows/`,
      },
    },
  },
}));
vi.mock("src/api/develop/experiment-run-actions", () => ({
  rerunCells: (...args) => state.rerun(...args),
}));
vi.mock("src/api/develop/experiment-append", async (original) => ({
  ...(await original()),
  appendExperimentVariant: (...args) => state.append(...args),
  previewHttpAppendRows: (...args) => state.preview(...args),
}));
vi.mock("src/api/develop/experiment-create", () => ({
  splitsFilter: (ids = []) =>
    ids.length
      ? [{ column_id: "system:splits", filter_config: { filter_value: ids } }]
      : [],
}));
vi.mock("src/sections/develop-detail/Experiment/scopeRows", () => ({
  readAllTableRows: (...args) => state.readRows(...args),
}));
vi.mock("notistack", () => ({
  enqueueSnackbar: (...args) => state.toast(...args),
}));

import ClassicHttpActions, {
  ClassicHttpAddRows,
  ClassicHttpRetryFailed,
  useClassicHttpCellRetry,
} from "../ClassicHttpRetry";
import {
  HTTP_UNFINISHED_REASON,
  addRowsPreviewOf,
  cellOutcomeUnknown,
  httpSourceOfColumn,
  httpSourcesOf,
  ranOnSelectedRows,
} from "../httpRetryModel";
import {
  HTTP_RESEND_COPY,
  httpAddRowsCountLine,
  httpRefusalMessage,
} from "src/sections/common/Runs/httpResendCopy";

const EXP = "experiment-a";
const DATASET = "4f1e0c52-1a2b-4c3d-8e9f-0a1b2c3d4e50";
const SOURCE = "4f1e0c52-1a2b-4c3d-8e9f-0a1b2c3d4e51";
const OUTPUT = "4f1e0c52-1a2b-4c3d-8e9f-0a1b2c3d4e52";
const EVAL = "4f1e0c52-1a2b-4c3d-8e9f-0a1b2c3d4e53";
const ROW = "4f1e0c52-1a2b-4c3d-8e9f-0a1b2c3d4e54";
const ROW_2 = "4f1e0c52-1a2b-4c3d-8e9f-0a1b2c3d4e56";
const SPLIT = "4f1e0c52-1a2b-4c3d-8e9f-0a1b2c3d4e55";
const SOURCE_2 = "4f1e0c52-1a2b-4c3d-8e9f-0a1b2c3d4e57";
// Original dataset rows: A was captured, then left the dataset; B and C are
// new live rows.
const ROW_A = "4f1e0c52-1a2b-4c3d-8e9f-0a1b2c3d4e58";
const ROW_B = "4f1e0c52-1a2b-4c3d-8e9f-0a1b2c3d4e59";
const ROW_C = "4f1e0c52-1a2b-4c3d-8e9f-0a1b2c3d4e5a";
const FORBIDDEN = {
  status: false,
  detail: "Workspace write permission is required.",
  statusCode: 403,
};
const httpConfig = {
  id: SOURCE, order: 0, target_type: "http", name: "Orders API",
  http: { url: "https://orders.example/v1/reply", method: "POST" },
  variable_mapping: {},
};
const httpDetail = (overrides = {}) => ({
  id: EXP,
  dataset_id: DATASET,
  status: "Completed",
  experiment_type: "llm",
  prompt_configs: [],
  http_configs: [httpConfig],
  ...overrides,
});
const columns = [
  { id: OUTPUT, origin_type: "experiment", source_id: SOURCE, name: "Orders API" },
  { id: EVAL, origin_type: "evaluation", source_id: `${SOURCE}-sourceid-metric`, name: "Tone" },
];
const PRIOR = "The endpoint may already have processed the earlier request.";
const PRIOR_MANY = "The endpoint may already have processed the earlier requests.";
const KEY = { idempotencyKey: expect.stringMatching(/^fa-http-/) };
const UNKNOWN = { message: "Network Error", transportCode: "ERR_NETWORK" };
// The exact words, so a missing copy key cannot pass as undefined === undefined.
const UNCONFIRMED =
  "Could not confirm whether this request started. The endpoint may already have processed it, so check the results before sending it again.";
const TOO_MANY =
  "Nothing was sent. An HTTP experiment holds at most 10,000 rows, and this dataset has more. To run other rows, create a new experiment and choose dataset splits with 10,000 rows or fewer.";
const NONE = "There are no new dataset rows to add, so nothing was sent.";
const SELECTED =
  "This experiment ran on selected or sampled rows. Add new rows does not keep that selection: it adds every other row, not a new sample.";
// What the server's rows read returns for a cell whose attempt did not
// finish: value_infos keeps only the reason (no error code).
const unfinishedCell = (reason) => ({
  cell_value: reason,
  status: "error",
  metadata: {
    response_time_ms: 0,
    token_count: 0,
    cost: {},
    cell_metadata: { response_time: null },
    reason,
  },
  value_infos: { reason },
});
const SETTLED =
  "The HTTP attempt did not finish. Check whether the endpoint processed it before explicitly rerunning.";
const CLEANED_UP =
  "The HTTP attempt did not finish. A prior HTTP attempt may have had side effects, even if it timed out or failed. Check the endpoint before sending it again.";

let client;
const wrap = (node) => (
  <QueryClientProvider client={client}>{node}</QueryClientProvider>
);
const dialog = () => screen.getByRole("dialog");
const press = (name) =>
  fireEvent.click(within(dialog()).getByRole("button", { name }));
// The experiment's own row total, which r2 subtracted from the dataset rows.
const heldRows = (count) => ({
  data: { status: true, result: { metadata: { total_rows: count } } },
});
// The server's read-only Add new rows preview (PUT preview=true).
const previewOf = (ids, { requests = ids.length, held = 0 } = {}) => ({
  preview: true,
  new_row_ids: ids,
  request_count: requests,
  held_count: held,
  limit: 10000,
});
const toastTexts = () => state.toast.mock.calls.map(([message]) => message);

beforeEach(() => {
  // A permissive default proves each action opts out of automatic retries.
  client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: 3, retryDelay: 0 },
    },
  });
  state.rerun.mockReset().mockResolvedValue({ data: { status: true, result: {} } });
  state.append.mockReset().mockResolvedValue({});
  state.preview.mockReset().mockResolvedValue(previewOf([ROW, ROW_2]));
  state.readRows.mockReset().mockResolvedValue({
    rows: [{ row_id: ROW }, { row_id: ROW_2 }],
    complete: true,
    total: 2,
  });
  state.get.mockReset().mockResolvedValue(heldRows(0));
  state.toast.mockReset();
});
afterEach(() => {
  cleanup();
  client.clear();
});

describe("Retry failed rows (HTTP experiments only)", () => {
  it("does not appear for a prompt experiment", () => {
    render(wrap(<ClassicHttpActions experimentId={EXP} detail={httpDetail({ http_configs: [] })} />));
    expect(screen.queryByRole("button", { name: "Retry failed rows" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Add new rows" })).toBeNull();
  });

  it("asks first, says the endpoint may have processed the earlier requests, and cancels cleanly", () => {
    render(wrap(<ClassicHttpRetryFailed experimentId={EXP} detail={httpDetail()} />));
    fireEvent.click(screen.getByRole("button", { name: "Retry failed rows" }));
    expect(within(dialog()).getByText("Retry failed rows?")).toBeTruthy();
    expect(within(dialog()).getByText(/Rows that succeeded are not sent again\./)).toBeTruthy();
    expect(within(dialog()).getByText(PRIOR_MANY, { exact: false })).toBeTruthy();
    expect(within(dialog()).getByText("Orders API")).toBeTruthy();
    press("Cancel");
    expect(state.rerun).not.toHaveBeenCalled();
  });

  it("re-sends only the failed rows of the HTTP sources, acknowledged and keyed, once", async () => {
    const onSent = vi.fn();
    render(wrap(<ClassicHttpRetryFailed experimentId={EXP} detail={httpDetail()} onSent={onSent} />));
    fireEvent.click(screen.getByRole("button", { name: "Retry failed rows" }));
    press("Retry failed rows");
    await waitFor(() => expect(onSent).toHaveBeenCalledOnce());
    expect(state.rerun).toHaveBeenCalledOnce();
    expect(state.rerun).toHaveBeenCalledWith(EXP, {
      source_ids: [SOURCE],
      failed_only: true,
      acknowledge_http_side_effects: true,
    }, KEY);
    expect(state.toast).toHaveBeenCalledWith(HTTP_RESEND_COPY.failedRowsStarted, {
      variant: "success",
    });
  });

  it("keeps an unknown outcome open; Check again repeats the same request and key", async () => {
    state.rerun.mockRejectedValueOnce(UNKNOWN);
    render(wrap(<ClassicHttpRetryFailed experimentId={EXP} detail={httpDetail()} />));
    fireEvent.click(screen.getByRole("button", { name: "Retry failed rows" }));
    press("Retry failed rows");
    await within(dialog()).findByText(HTTP_RESEND_COPY.checkAgainHelp, { exact: false });
    // Nothing is sent again on its own.
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 30)); });
    expect(state.rerun).toHaveBeenCalledOnce();
    press("Check again");
    await waitFor(() => expect(state.rerun).toHaveBeenCalledTimes(2));
    expect(state.rerun.mock.calls[1]).toEqual(state.rerun.mock.calls[0]);
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("SRC-S1b-HTTP-FE-06 closing an unknown outcome says so in words, refreshes, and a later retry gets a new key", async () => {
    // Fails while the copy key is missing: undefined never matches.
    expect(HTTP_RESEND_COPY.unconfirmed).toBe(UNCONFIRMED);
    state.rerun.mockRejectedValueOnce(UNKNOWN);
    const onSent = vi.fn();
    render(wrap(<ClassicHttpRetryFailed experimentId={EXP} detail={httpDetail()} onSent={onSent} />));
    fireEvent.click(screen.getByRole("button", { name: "Retry failed rows" }));
    press("Retry failed rows");
    await within(dialog()).findByText(HTTP_RESEND_COPY.checkAgainHelp, { exact: false });
    press("Close");
    expect(state.toast).toHaveBeenCalledWith(UNCONFIRMED, { variant: "error" });
    expect(toastTexts().every((text) => typeof text === "string" && text.trim())).toBe(true);
    expect(onSent).toHaveBeenCalledOnce();
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    fireEvent.click(screen.getByRole("button", { name: "Retry failed rows" }));
    press("Retry failed rows");
    await waitFor(() => expect(state.rerun).toHaveBeenCalledTimes(2));
    expect(state.rerun.mock.calls[1][2].idempotencyKey).not.toBe(
      state.rerun.mock.calls[0][2].idempotencyKey,
    );
  });

  it("shows the server's reason for a definite refusal", async () => {
    state.rerun.mockRejectedValue({ statusCode: 409, result: "Stop the running experiment first." });
    render(wrap(<ClassicHttpRetryFailed experimentId={EXP} detail={httpDetail()} />));
    fireEvent.click(screen.getByRole("button", { name: "Retry failed rows" }));
    press("Retry failed rows");
    await waitFor(() =>
      expect(state.toast).toHaveBeenCalledWith("Stop the running experiment first.", { variant: "error" }),
    );
    expect(state.rerun).toHaveBeenCalledOnce();
  });

  it("SRC-S1b-HTTP-FE-13 says why a 403 refused the retry (its reason is only in detail)", async () => {
    state.rerun.mockRejectedValueOnce(FORBIDDEN);
    render(wrap(<ClassicHttpRetryFailed experimentId={EXP} detail={httpDetail()} />));
    fireEvent.click(screen.getByRole("button", { name: "Retry failed rows" }));
    press("Retry failed rows");
    await waitFor(() =>
      expect(state.toast).toHaveBeenCalledWith(
        "Workspace write permission is required.",
        expect.objectContaining({ variant: "error" }),
      ),
    );
    expect(toastTexts()).not.toContain(HTTP_RESEND_COPY.failedRowsFailed);
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(state.rerun).toHaveBeenCalledOnce();
  });

  it("SRC-S1b-HTTP-FE-13 reads a refusal's reason (pure)", () => {
    expect(httpRefusalMessage(FORBIDDEN, "Could not retry the failed rows.")).toBe(
      "Workspace write permission is required.",
    );
    expect(httpRefusalMessage({ statusCode: 403, detail: "  " }, "Fallback.")).toBe("Fallback.");
    expect(httpRefusalMessage({ statusCode: 409, result: "Stop the running experiment first." }, "Fallback."))
      .toBe("Stop the running experiment first.");
    expect(httpRefusalMessage({ statusCode: 400 }, "Fallback.")).toBe("Fallback.");
  });

  it("waits while the experiment is running", () => {
    render(wrap(<ClassicHttpActions experimentId={EXP} detail={httpDetail({ status: "Running" })} />));
    expect(screen.getByRole("button", { name: "Retry failed rows" }).disabled).toBe(true);
    expect(screen.getByRole("button", { name: "Add new rows" }).disabled).toBe(true);
  });
});

describe("Add new rows (HTTP append rows)", () => {
  it("SRC-S1b-HTTP-FE-10 counts the rows first, states how many requests it sends, then appends them, keyed", async () => {
    const detail = httpDetail({ run_scope: { split_ids: [SPLIT] } });
    render(wrap(<ClassicHttpAddRows experimentId={EXP} detail={detail} />));
    fireEvent.click(screen.getByRole("button", { name: "Add new rows" }));
    const confirm = await screen.findByRole("dialog");
    // The rows were read and previewed before the confirm opened.
    expect(state.readRows).toHaveBeenCalledOnce();
    const [url, params, options] = state.readRows.mock.calls[0];
    expect(url).toBe(`/model-hub/develops/${DATASET}/get-dataset-table/`);
    expect(JSON.parse(params.filters)[0].filter_config.filter_value).toEqual([SPLIT]);
    expect(options.limit).toBe(10001);
    expect(state.preview).toHaveBeenCalledOnce();
    expect(state.preview).toHaveBeenCalledWith(EXP, [ROW, ROW_2]);
    // No count by row totals.
    expect(state.get).not.toHaveBeenCalled();
    expect(within(confirm).getByText(/in this experiment's splits that the experiment does not include\./)).toBeTruthy();
    expect(within(confirm).getByText("This sends 2 requests, one for each of the 2 new rows.")).toBeTruthy();
    expect(within(confirm).getByText(/Rows already in the experiment are not sent again/)).toBeTruthy();
    expect(within(confirm).queryByText(SELECTED)).toBeNull();
    expect(state.append).not.toHaveBeenCalled();
    press("Add rows");
    await waitFor(() => expect(state.append).toHaveBeenCalledOnce());
    expect(state.append).toHaveBeenCalledWith(EXP, {
      append_only: true,
      row_ids: [ROW, ROW_2],
    }, KEY);
    expect(state.toast).toHaveBeenCalledWith(HTTP_RESEND_COPY.addRowsStarted, { variant: "success" });
  });

  it("SRC-S1b-HTTP-FE-10 counts only rows the experiment does not hold, for each HTTP run", async () => {
    const live = [1, 2, 3, 4, 5].map((n) => `${ROW.slice(0, -2)}0${n}`);
    state.readRows.mockResolvedValue({
      rows: live.map((id) => ({ row_id: id })),
      complete: true,
      total: 5,
    });
    state.preview.mockResolvedValue(previewOf(live.slice(2), { requests: 6, held: 2 }));
    const detail = httpDetail({
      http_configs: [httpConfig, { ...httpConfig, id: SOURCE_2, order: 1, name: "Orders API v2" }],
    });
    render(wrap(<ClassicHttpAddRows experimentId={EXP} detail={detail} />));
    fireEvent.click(screen.getByRole("button", { name: "Add new rows" }));
    const confirm = await screen.findByRole("dialog");
    expect(state.preview).toHaveBeenCalledWith(EXP, live);
    expect(within(confirm).getByText(/^Each of the 2 HTTP runs sends a request for every dataset row that/)).toBeTruthy();
    expect(within(confirm).getByText("This sends 6 requests, 3 to each HTTP run, one for each of the 3 new rows.")).toBeTruthy();
    press("Add rows");
    await waitFor(() => expect(state.append).toHaveBeenCalledOnce());
    expect(state.append).toHaveBeenCalledWith(EXP, { append_only: true, row_ids: live.slice(2) }, KEY);
  });

  it("SRC-S1b-HTTP-FE-10 says when the experiment ran on selected or sampled rows", async () => {
    expect(HTTP_RESEND_COPY.addRowsSelected).toBe(SELECTED);
    render(wrap(<ClassicHttpAddRows experimentId={EXP} detail={httpDetail({ sample: { size: 1, seed: 7 } })} />));
    fireEvent.click(screen.getByRole("button", { name: "Add new rows" }));
    const confirm = await screen.findByRole("dialog");
    expect(within(confirm).getByText(SELECTED)).toBeTruthy();
    expect(within(confirm).getByText("This sends 2 requests, one for each of the 2 new rows.")).toBeTruthy();
  });

  it("SRC-S1b-HTTP-FE-10 sends nothing and opens nothing when there are no new rows", async () => {
    state.preview.mockResolvedValue(previewOf([], { requests: 0, held: 2 }));
    render(wrap(<ClassicHttpAddRows experimentId={EXP} detail={httpDetail()} />));
    fireEvent.click(screen.getByRole("button", { name: "Add new rows" }));
    await waitFor(() => expect(state.toast).toHaveBeenCalledWith(NONE, { variant: "info" }));
    expect(HTTP_RESEND_COPY.addRowsNone).toBe(NONE);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(state.append).not.toHaveBeenCalled();
  });

  it("SRC-S1b-HTTP-FE-15 adds a new row when a held row left the dataset: captured [A], live [B]", async () => {
    // r2 subtracted totals: 1 live row - 1 held row = 0, "no new rows".
    state.readRows.mockResolvedValue({ rows: [{ row_id: ROW_B }], complete: true, total: 1 });
    state.get.mockResolvedValue(heldRows(1));
    state.preview.mockResolvedValue(previewOf([ROW_B], { requests: 1, held: 1 }));
    render(wrap(<ClassicHttpAddRows experimentId={EXP} detail={httpDetail()} />));
    fireEvent.click(screen.getByRole("button", { name: "Add new rows" }));
    const confirm = await screen.findByRole("dialog");
    expect(state.preview).toHaveBeenCalledWith(EXP, [ROW_B]);
    expect(within(confirm).getByText("This sends 1 request, for the one new row.")).toBeTruthy();
    expect(toastTexts()).not.toContain(NONE);
    press("Add rows");
    await waitFor(() => expect(state.append).toHaveBeenCalledOnce());
    expect(state.append).toHaveBeenCalledWith(EXP, { append_only: true, row_ids: [ROW_B] }, KEY);
    expect(state.append.mock.calls[0][1].row_ids).not.toContain(ROW_A);
  });

  it("SRC-S1b-HTTP-FE-15 states every request it sends: captured [A], live [B, C]", async () => {
    // r2 subtracted totals: 2 live rows - 1 held row = 1, one request too few.
    state.readRows.mockResolvedValue({
      rows: [{ row_id: ROW_B }, { row_id: ROW_C }],
      complete: true,
      total: 2,
    });
    state.get.mockResolvedValue(heldRows(1));
    state.preview.mockResolvedValue(previewOf([ROW_B, ROW_C], { requests: 2, held: 1 }));
    render(wrap(<ClassicHttpAddRows experimentId={EXP} detail={httpDetail()} />));
    fireEvent.click(screen.getByRole("button", { name: "Add new rows" }));
    const confirm = await screen.findByRole("dialog");
    expect(within(confirm).getByText("This sends 2 requests, one for each of the 2 new rows.")).toBeTruthy();
    expect(within(confirm).queryByText("This sends 1 request, for the one new row.")).toBeNull();
    press("Add rows");
    await waitFor(() => expect(state.append).toHaveBeenCalledOnce());
    expect(state.append).toHaveBeenCalledWith(EXP, { append_only: true, row_ids: [ROW_B, ROW_C] }, KEY);
  });

  it("SRC-S1b-HTTP-FE-15 sends nothing when the new rows would take the experiment past its limit", async () => {
    state.readRows.mockResolvedValue({ rows: [{ row_id: ROW_B }], complete: true, total: 1 });
    state.preview.mockResolvedValue(previewOf([ROW_B], { requests: 1, held: 10000 }));
    render(wrap(<ClassicHttpAddRows experimentId={EXP} detail={httpDetail()} />));
    fireEvent.click(screen.getByRole("button", { name: "Add new rows" }));
    await waitFor(() => expect(state.toast).toHaveBeenCalledWith(TOO_MANY, { variant: "error" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(state.append).not.toHaveBeenCalled();
  });

  it("SRC-S1b-HTTP-FE-15 sends nothing when the answer is not a preview", async () => {
    state.preview.mockResolvedValue({ id: EXP, name: "Orders", http_configs: [] });
    render(wrap(<ClassicHttpAddRows experimentId={EXP} detail={httpDetail()} />));
    fireEvent.click(screen.getByRole("button", { name: "Add new rows" }));
    await waitFor(() =>
      expect(state.toast).toHaveBeenCalledWith(
        "Could not count the dataset rows. Nothing was sent.",
        { variant: "error" },
      ),
    );
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(state.append).not.toHaveBeenCalled();
  });

  it("SRC-S1b-HTTP-FE-13 says why a 403 refused the count of new rows", async () => {
    state.preview.mockRejectedValueOnce(FORBIDDEN);
    render(wrap(<ClassicHttpAddRows experimentId={EXP} detail={httpDetail()} />));
    fireEvent.click(screen.getByRole("button", { name: "Add new rows" }));
    await waitFor(() =>
      expect(state.toast).toHaveBeenCalledWith(
        "Workspace write permission is required. Nothing was sent.",
        { variant: "error" },
      ),
    );
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(state.append).not.toHaveBeenCalled();
  });

  it("SRC-S1b-HTTP-FE-11 does not send more rows than an experiment holds, and says nothing was sent and what to do", async () => {
    expect(HTTP_RESEND_COPY.addRowsTooMany).toBe(TOO_MANY);
    state.readRows.mockResolvedValue({ rows: [{ row_id: ROW }], complete: false, total: 12000 });
    render(wrap(<ClassicHttpAddRows experimentId={EXP} detail={httpDetail()} />));
    fireEvent.click(screen.getByRole("button", { name: "Add new rows" }));
    await waitFor(() =>
      expect(state.toast).toHaveBeenCalledWith(TOO_MANY, { variant: "error" }),
    );
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(state.append).not.toHaveBeenCalled();
  });

  it("SRC-S1b-HTTP-FE-11 says nothing was sent when the rows cannot be counted", async () => {
    state.readRows.mockRejectedValueOnce(UNKNOWN);
    const view = render(wrap(<ClassicHttpAddRows experimentId={EXP} detail={httpDetail()} />));
    fireEvent.click(screen.getByRole("button", { name: "Add new rows" }));
    await waitFor(() =>
      expect(state.toast).toHaveBeenCalledWith(
        "Could not count the dataset rows. Nothing was sent.",
        { variant: "error" },
      ),
    );
    view.unmount();
    state.toast.mockReset();
    state.preview.mockRejectedValueOnce({ statusCode: 404, result: "Experiment not found" });
    render(wrap(<ClassicHttpAddRows experimentId={EXP} detail={httpDetail()} />));
    fireEvent.click(screen.getByRole("button", { name: "Add new rows" }));
    await waitFor(() =>
      expect(state.toast).toHaveBeenCalledWith(
        "Experiment not found. Nothing was sent.",
        { variant: "error" },
      ),
    );
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(state.append).not.toHaveBeenCalled();
  });

  it("recovers an unknown outcome with the same rows and key", async () => {
    state.append.mockRejectedValueOnce({ statusCode: 503, code: "experiment_admission_unconfirmed" });
    render(wrap(<ClassicHttpAddRows experimentId={EXP} detail={httpDetail()} />));
    fireEvent.click(screen.getByRole("button", { name: "Add new rows" }));
    await screen.findByRole("dialog");
    press("Add rows");
    await within(dialog()).findByText(HTTP_RESEND_COPY.checkAgainHelp, { exact: false });
    press("Check again");
    await waitFor(() => expect(state.append).toHaveBeenCalledTimes(2));
    expect(state.append.mock.calls[1]).toEqual(state.append.mock.calls[0]);
    expect(state.readRows).toHaveBeenCalledOnce();
    expect(state.preview).toHaveBeenCalledOnce();
  });

  it("SRC-S1b-HTTP-FE-06 closing an unknown outcome of Add new rows says so in words", async () => {
    state.append.mockRejectedValueOnce(UNKNOWN);
    render(wrap(<ClassicHttpAddRows experimentId={EXP} detail={httpDetail()} />));
    fireEvent.click(screen.getByRole("button", { name: "Add new rows" }));
    await screen.findByRole("dialog");
    press("Add rows");
    await within(dialog()).findByText(HTTP_RESEND_COPY.checkAgainHelp, { exact: false });
    press("Close");
    expect(state.toast).toHaveBeenCalledWith(UNCONFIRMED, { variant: "error" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(state.append).toHaveBeenCalledOnce();
  });

  it("states the count in plain words (pure)", () => {
    expect(httpAddRowsCountLine(4800)).toBe(
      "This sends 4,800 requests, one for each of the 4,800 new rows.",
    );
    expect(httpAddRowsCountLine(4800, 2)).toBe(
      "This sends 9,600 requests, 4,800 to each HTTP run, one for each of the 4,800 new rows.",
    );
    expect(httpAddRowsCountLine(1)).toBe("This sends 1 request, for the one new row.");
    // The server's request_count is the number stated.
    expect(httpAddRowsCountLine(3, 2, 6)).toBe(
      "This sends 6 requests, 3 to each HTTP run, one for each of the 3 new rows.",
    );
    expect(httpAddRowsCountLine(2, 2, 2)).toBe(
      "This sends 2 requests, one for each of the 2 new rows.",
    );
    expect(ranOnSelectedRows(httpDetail())).toBe(false);
    expect(ranOnSelectedRows(httpDetail({ row_ids: [ROW] }))).toBe(true);
    expect(ranOnSelectedRows(httpDetail({ run_scope: { split_ids: [SPLIT], sample: { size: 2, seed: 1 } } }))).toBe(true);
    expect(ranOnSelectedRows(httpDetail({ run_scope: { split_ids: [SPLIT] } }))).toBe(false);
  });

  it("SRC-S1b-HTTP-FE-15 reads the preview's exact new rows, request count and limit (pure)", () => {
    expect(addRowsPreviewOf(previewOf([ROW_B, ROW_C], { requests: 4, held: 1 }))).toEqual({
      rowIds: [ROW_B, ROW_C],
      requests: 4,
      overLimit: false,
    });
    expect(addRowsPreviewOf(previewOf([], { requests: 0, held: 3 }))).toEqual({
      rowIds: [],
      requests: 0,
      overLimit: false,
    });
    expect(addRowsPreviewOf(previewOf([ROW_B, ROW_C], { held: 9999 })).overLimit).toBe(true);
    expect(addRowsPreviewOf({ ...previewOf([ROW_B]), preview: undefined })).toBeNull();
    expect(addRowsPreviewOf({ ...previewOf([ROW_B]), request_count: "1" })).toBeNull();
    expect(addRowsPreviewOf({ ...previewOf([ROW_B]), new_row_ids: [null] })).toBeNull();
    expect(addRowsPreviewOf(undefined)).toBeNull();
  });
});

/* eslint-disable react/prop-types */
function CellHarness({ detail, cells = {}, onClassic }) {
  const retry = useClassicHttpCellRetry({
    experimentId: EXP,
    sources: httpSourcesOf(detail),
    columns,
    readCell: ({ columnId, rowId }) => cells[`${rowId}:${columnId}`],
  });
  return (
    <>
      <button type="button" onClick={() => {
        if (!retry.request({ columnId: OUTPUT, rowId: ROW })) onClassic("output");
      }}>Retry output cell</button>
      <button type="button" onClick={() => {
        if (!retry.request({ columnId: EVAL, rowId: ROW })) onClassic("eval");
      }}>Retry eval cell</button>
      {retry.dialog}
    </>
  );
}
/* eslint-enable react/prop-types */

describe("Retry cell (HTTP output cells)", () => {
  it("names the HTTP source of an output column and nothing else", () => {
    const sources = httpSourcesOf(httpDetail());
    expect(httpSourceOfColumn(OUTPUT, columns, sources)).toMatchObject({ id: SOURCE });
    expect(httpSourceOfColumn(EVAL, columns, sources)).toBeNull();
    expect(httpSourceOfColumn(OUTPUT, columns, [])).toBeNull();
  });

  it("asks before sending one row again, then sends only that cell", async () => {
    const onClassic = vi.fn();
    render(wrap(<CellHarness detail={httpDetail()} onClassic={onClassic} />));
    fireEvent.click(screen.getByRole("button", { name: "Retry output cell" }));
    expect(within(dialog()).getByText("Send this row again?")).toBeTruthy();
    expect(within(dialog()).getByText(/This sends a new request to orders\.example for this row\./)).toBeTruthy();
    expect(within(dialog()).getByText(PRIOR, { exact: false })).toBeTruthy();
    expect(within(dialog()).queryByText(HTTP_RESEND_COPY.unknownOutcome)).toBeNull();
    expect(state.rerun).not.toHaveBeenCalled();
    press("Send again");
    await waitFor(() => expect(state.rerun).toHaveBeenCalledOnce());
    expect(state.rerun).toHaveBeenCalledWith(EXP, {
      cells: [{ column_id: OUTPUT, row_id: ROW }],
      acknowledge_http_side_effects: true,
    }, KEY);
    expect(onClassic).not.toHaveBeenCalled();
  });

  it.each([
    ["settled", SETTLED],
    ["cleaned up", CLEANED_UP],
  ])("SRC-S1b-HTTP-FE-07 says when the earlier request did not finish (%s), from the grid's own cell", (_label, reason) => {
    expect(reason.startsWith(HTTP_UNFINISHED_REASON)).toBe(true);
    const cells = { [`${ROW}:${OUTPUT}`]: unfinishedCell(reason) };
    render(wrap(<CellHarness detail={httpDetail()} cells={cells} onClassic={vi.fn()} />));
    fireEvent.click(screen.getByRole("button", { name: "Retry output cell" }));
    expect(within(dialog()).getByText(HTTP_RESEND_COPY.unknownOutcome)).toBeTruthy();
  });

  it("SRC-S1b-HTTP-FE-07 reads both the grid cell and a stored error code (pure)", () => {
    expect(cellOutcomeUnknown(unfinishedCell(SETTLED))).toBe(true);
    expect(cellOutcomeUnknown({ value_infos: { reason: CLEANED_UP } })).toBe(true);
    expect(cellOutcomeUnknown({ metadata: { reason: SETTLED } })).toBe(true);
    expect(cellOutcomeUnknown({
      value_infos: JSON.stringify({ error: { code: "unknown_outcome", message: "Did not finish" } }),
    })).toBe(true);
    expect(cellOutcomeUnknown(unfinishedCell("The endpoint answered 500."))).toBe(false);
    expect(cellOutcomeUnknown({ cell_value: "ok", status: "pass", value_infos: {} })).toBe(false);
    expect(cellOutcomeUnknown(undefined)).toBe(false);
  });

  it("does not add the line for an ordinary failed cell", () => {
    const cells = { [`${ROW}:${OUTPUT}`]: unfinishedCell("The endpoint answered 500.") };
    render(wrap(<CellHarness detail={httpDetail()} cells={cells} onClassic={vi.fn()} />));
    fireEvent.click(screen.getByRole("button", { name: "Retry output cell" }));
    expect(within(dialog()).getByText(PRIOR, { exact: false })).toBeTruthy();
    expect(within(dialog()).queryByText(HTTP_RESEND_COPY.unknownOutcome)).toBeNull();
  });

  it("keeps the classic retry for evaluation cells and prompt experiments", () => {
    const onClassic = vi.fn();
    const view = render(wrap(<CellHarness detail={httpDetail()} onClassic={onClassic} />));
    fireEvent.click(screen.getByRole("button", { name: "Retry eval cell" }));
    expect(onClassic).toHaveBeenCalledWith("eval");
    expect(screen.queryByRole("dialog")).toBeNull();
    view.unmount();
    render(wrap(<CellHarness detail={httpDetail({ http_configs: [] })} onClassic={onClassic} />));
    fireEvent.click(screen.getByRole("button", { name: "Retry output cell" }));
    expect(onClassic).toHaveBeenCalledWith("output");
    expect(state.rerun).not.toHaveBeenCalled();
  });
});

describe("classic Data page wiring", () => {
  const view = readFileSync(
    resolve(
      process.cwd(),
      "src/sections/experiment-detail-classic/ExperimentData/ExperimentDataView.jsx",
    ),
    "utf8",
  );

  it("routes every cell retry through the HTTP confirm first", () => {
    expect(view).toMatch(/if \(!httpCellRetry\.request\(cell\)\) onRerun\(cell\);/);
    expect(view).toMatch(/updateWaveSurferInstance,\s*handleRerunCell,/);
    expect(view).toContain("{httpCellRetry.dialog}");
  });

  it("reads each cell from the grid's own row data", () => {
    expect(view).toMatch(
      /readCell: \(\{ columnId, rowId \}\) =>\s*gridApiRef\.current\?\.api\?\.getRowNode\?\.\(rowId\)\?\.data\?\.\[columnId\]/,
    );
  });

  it("offers the HTTP actions from the grid's own detail read", () => {
    expect(view).toMatch(
      /<ClassicHttpActions\s+experimentId=\{experimentId\}\s+detail=\{experimentData\}/,
    );
  });
});
