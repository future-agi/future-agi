import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import axios from "src/utils/axios";
import SessionEvalsList from "../SessionEvalsList";

vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: { get: vi.fn() },
}));
vi.mock("src/components/status-badge/StatusBadge", async (importOriginal) => ({
  ...(await importOriginal()),
  default: ({ status, role, label }) =>
    React.createElement(
      "span",
      {
        "data-testid": "status-badge",
        "data-status": status || "",
        "data-role": role || "",
      },
      label,
    ),
}));
vi.mock("src/components/eval/EvalStatusIndicator", async (importOriginal) => ({
  ...(await importOriginal()),
  default: ({ status }) =>
    React.createElement(
      "span",
      { "data-testid": "eval-status", "data-status": status },
      status,
    ),
}));
vi.mock("src/components/content-skeleton", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () =>
    React.createElement("div", { role: "status" }, "Loading evaluations"),
}));

const SESSION = "session-a";
const CREATED = "2026-11-11T12:29:00.000Z";
const EMPTY = "No session-level evaluations yet.";
const PASS_OUTPUT = '{"score": 1, "choice": "Pass"}';
const FAIL_OUTPUT = '{"score": 0, "choice": "Fail"}';
// The first load plus twelve checks: about a minute at the default interval.
const EMPTY_LIST_CALLS = 13;

// One item of GET /tracer/trace-session/{id}/eval_logs/ as the API builds it.
const sessionEval = ({
  id,
  name,
  result = "",
  score = null,
  reason = "",
  status = "success",
  evalStatus = null,
  detail = {},
}) => ({
  id,
  input: "support chat",
  result,
  score,
  reason,
  status,
  eval_status: evalStatus,
  source: "eval_task",
  created_at: CREATED,
  session_id: SESSION,
  eval_id: `config-${id}`,
  eval_name: name,
  model: "judge-model",
  detail: {
    eval_name: name,
    model: "judge-model",
    output_type: "choices",
    target_type: "session",
    session_id: SESSION,
    session_name: "support chat",
    output_bool: null,
    output_float: null,
    output_str: null,
    results_explanation: null,
    error_message: null,
    ...detail,
  },
});

// A choices eval stores its verdict as JSON in output_str; the API copies the
// first 50 characters into `result` and leaves `score` empty.
const passed = sessionEval({
  id: "log-pass",
  name: "Session resolved",
  result: PASS_OUTPUT,
  reason: "The reply answers the shipping question.",
  detail: { output_str: PASS_OUTPUT },
});
const failed = sessionEval({
  id: "log-fail",
  name: "Session polite",
  result: FAIL_OUTPUT,
  reason: "The reply refuses to help.",
  detail: { output_str: FAIL_OUTPUT },
});
// A row the judge has not answered yet: no output, reason or error.
const queued = sessionEval({ id: "log-queued", name: "Session queued" });
const queuedDone = sessionEval({
  id: "log-queued",
  name: "Session queued",
  result: PASS_OUTPUT,
  reason: "The reply answers the shipping question.",
  detail: { output_str: PASS_OUTPUT },
});

const page = (items, total = items.length) => ({
  data: { result: { total, page: 0, page_size: 100, items } },
});
const deferred = () => {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
};
const sleep = (ms) =>
  new Promise((done) => {
    setTimeout(done, ms);
  });
// The eval name is the first cell of its row.
const rowOf = (name) => within(screen.getByText(name).parentElement);

let client;
const ui = (props = {}) => (
  <QueryClientProvider client={client}>
    <SessionEvalsList sessionId={SESSION} {...props} />
  </QueryClientProvider>
);

beforeEach(() => {
  axios.get.mockReset();
  client = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        gcTime: Infinity,
        staleTime: Infinity,
        refetchOnWindowFocus: false,
      },
    },
  });
});

// Unmount first so no check is left scheduled, then drop the cache.
afterEach(() => {
  cleanup();
  client.clear();
});

describe("SessionEvalsList results", () => {
  it("shows the score and choice of a choices eval instead of its raw output", async () => {
    axios.get.mockResolvedValue(page([passed, failed]));
    render(ui());
    await screen.findByText("Session resolved");

    const pass = rowOf("Session resolved");
    expect(pass.getByText("1.00")).toHaveAttribute("data-role", "success");
    expect(pass.getByText("Pass")).toHaveAttribute("data-status", "passed");
    expect(
      pass.getByText("The reply answers the shipping question."),
    ).toBeInTheDocument();

    const fail = rowOf("Session polite");
    expect(fail.getByText("0.00")).toHaveAttribute("data-role", "danger");
    expect(fail.getByText("Fail")).toHaveAttribute("data-status", "failed");

    expect(screen.queryByText(/"score"/)).not.toBeInTheDocument();
    expect(screen.queryByText(/"choice"/)).not.toBeInTheDocument();
  });

  it("reads a single-quoted output when only the result carries it", async () => {
    const legacy = sessionEval({
      id: "log-legacy",
      name: "Session tone",
      result: "{'score': 0.5, 'choice': 'Partly'}",
      reason: "Polite but slow.",
    });
    axios.get.mockResolvedValue(page([legacy]));
    render(ui());
    await screen.findByText("Session tone");

    const row = rowOf("Session tone");
    expect(row.getByText("0.50")).toHaveAttribute("data-role", "warning");
    expect(row.getByText("Partly")).toBeInTheDocument();
    expect(screen.queryByText(/'score'/)).not.toBeInTheDocument();
  });

  it("shows a row the judge has not answered yet as pending", async () => {
    axios.get.mockResolvedValue(page([queued]));
    render(ui({ pollIntervalMs: 60_000 }));
    await screen.findByText("Session queued");

    expect(rowOf("Session queued").getByTestId("eval-status")).toHaveAttribute(
      "data-status",
      "pending",
    );
  });

  it.each(["running", "skipped"])(
    "keeps the %s state the API reports",
    async (evalStatus) => {
      const row = sessionEval({
        id: `log-${evalStatus}`,
        name: `Session ${evalStatus}`,
        evalStatus,
        reason: evalStatus === "skipped" ? "A mapped field is missing." : "",
      });
      axios.get.mockResolvedValue(page([row]));
      render(ui({ pollIntervalMs: 60_000 }));
      await screen.findByText(`Session ${evalStatus}`);

      expect(
        rowOf(`Session ${evalStatus}`).getByTestId("eval-status"),
      ).toHaveAttribute("data-status", evalStatus);
    },
  );
});

describe("SessionEvalsList checks for new results", () => {
  it("replaces the empty state with the pending row once it can be read", async () => {
    axios.get.mockResolvedValueOnce(page([])).mockResolvedValue(page([queued]));
    render(ui({ pollIntervalMs: 50 }));
    await screen.findByText(EMPTY);

    await screen.findByText("Session queued");
    expect(screen.queryByText(EMPTY)).not.toBeInTheDocument();
    expect(rowOf("Session queued").getByTestId("eval-status")).toHaveAttribute(
      "data-status",
      "pending",
    );
  });

  it("shows the result when the pending row finishes, then stops checking", async () => {
    axios.get
      .mockResolvedValueOnce(page([queued]))
      .mockResolvedValue(page([queuedDone]));
    render(ui({ pollIntervalMs: 20 }));
    await screen.findByText("Session queued");

    await waitFor(() =>
      expect(rowOf("Session queued").getByText("1.00")).toBeInTheDocument(),
    );
    expect(rowOf("Session queued").getByText("Pass")).toHaveAttribute(
      "data-status",
      "passed",
    );
    expect(screen.queryByTestId("eval-status")).not.toBeInTheDocument();
    await sleep(200);
    expect(axios.get).toHaveBeenCalledTimes(2);
  });

  it("stops checking an empty list after a fixed number of checks", async () => {
    axios.get.mockResolvedValue(page([]));
    render(ui({ pollIntervalMs: 10 }));
    await screen.findByText(EMPTY);

    await waitFor(
      () => expect(axios.get).toHaveBeenCalledTimes(EMPTY_LIST_CALLS),
      { timeout: 5000 },
    );
    await sleep(200);
    expect(axios.get).toHaveBeenCalledTimes(EMPTY_LIST_CALLS);
    expect(screen.getByText(EMPTY)).toBeInTheDocument();
  });

  it("does not check again when every row has a result", async () => {
    axios.get.mockResolvedValue(page([passed, failed]));
    render(ui({ pollIntervalMs: 10 }));
    await screen.findByText("Session resolved");

    await sleep(200);
    expect(axios.get).toHaveBeenCalledTimes(1);
  });

  it("keeps the rows on screen when a background check fails", async () => {
    axios.get
      .mockResolvedValueOnce(page([queued]))
      .mockRejectedValue(
        new Error("Evaluation logs are temporarily unavailable."),
      );
    render(ui({ pollIntervalMs: 20 }));
    await screen.findByText("Session queued");

    await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(2));
    await sleep(100);
    expect(screen.getByText("Session queued")).toBeInTheDocument();
    expect(
      screen.queryByText("Failed to load session evals"),
    ).not.toBeInTheDocument();
    expect(axios.get).toHaveBeenCalledTimes(2);
  });

  it("leaves Load more enabled while a background check runs", async () => {
    const rows = [
      queued,
      ...Array.from({ length: 99 }, (_, index) =>
        sessionEval({
          id: `log-${index}`,
          name: `Session check ${index}`,
          result: "Passed",
          score: 1,
        }),
      ),
    ];
    const firstPage = page(rows, 101);
    const check = deferred();
    axios.get
      .mockResolvedValue(firstPage)
      .mockResolvedValueOnce(firstPage)
      .mockReturnValueOnce(check.promise);
    render(ui({ pollIntervalMs: 20 }));
    await screen.findByText("Session queued");

    await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(2));
    expect(screen.getByRole("button", { name: "Load more" })).toBeEnabled();
    await act(async () => check.resolve(firstPage));
  });
});
