// W6: the experiment Traces tab read
// GET /model-hub/experiments/v2/<exp>/runs/<run>/traces/, which the backend
// does not have (an HTML 404), and showed "Couldn't load this run's traces."
// The tab now reads the backend route GET
// /model-hub/experiment-runs/<run>/traces/ (limit and offset only) and lists
// what it returns; a run with no traces shows one calm line.
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const http = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: http,
}));
vi.mock("src/components/traceDetail/TraceDetailDrawerV2", () => ({
  default: () => null,
}));

import TracesView from "../TracesView";
import { runTracesView } from "../tracesModel";

const RUN = "11111111-1111-4111-8111-111111111111";
const EXPERIMENT = "22222222-2222-4222-8222-222222222222";
const PROJECT = "33333333-3333-4333-8333-333333333333";
const EXAMPLE_A = "44444444-4444-4444-8444-444444444444";
const EXAMPLE_B = "55555555-5555-4555-8555-555555555555";
const TRACE_A = "66666666-6666-4666-8666-666666666666";
const TRACE_B = "77777777-7777-4777-8777-777777777777";
const ROUTE = `/model-hub/experiment-runs/${RUN}/traces/`;

const trace = (traceId, extra = {}) => ({
  trace_id: traceId,
  root_span_id: `root-${traceId}`,
  example_id: EXAMPLE_A,
  trial_number: 1,
  item_status: "completed",
  trace_run_id: RUN,
  project_id: PROJECT,
  root_span_name: "experiment.trial",
  start_time: null,
  span_count: 3,
  trace_example_id: EXAMPLE_A,
  trace_trial: 1,
  ...extra,
});

// The backend envelope as the shared client returns it.
const page = (traces, extra = {}) => ({
  data: {
    status: true,
    result: {
      run_id: RUN,
      traces,
      not_joined: [],
      items_with_trace: traces.length,
      limit: 100,
      offset: 0,
      has_more: false,
      ...extra,
    },
  },
  status: 200,
  headers: {},
});

const renderTraces = () =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <TracesView experimentId={EXPERIMENT} runId={RUN} />
    </QueryClientProvider>,
  );

const requested = () =>
  http.get.mock.calls.map(([url]) => new URL(String(url), "http://app.test"));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("W6 Traces tab reads the backend's run traces route", () => {
  it("calls experiment-runs/<run>/traces/ and lists the returned traces", async () => {
    http.get.mockResolvedValue(
      page([
        trace(TRACE_A),
        trace(TRACE_B, {
          example_id: EXAMPLE_B,
          trial_number: 2,
          item_status: "failed",
        }),
      ]),
    );
    renderTraces();

    const rows = await screen.findAllByTestId("run-trace-row");
    expect(rows.map((row) => row.getAttribute("data-trace-id"))).toEqual([
      TRACE_A,
      TRACE_B,
    ]);
    // The route the backend has, with only the keys it accepts.
    const urls = requested();
    expect(urls.length).toBeGreaterThan(0);
    urls.forEach((url) => {
      expect(url.pathname).toBe(ROUTE);
      expect([...url.searchParams.keys()].sort()).toEqual(["limit", "offset"]);
      expect(url.searchParams.get("offset")).toBe("0");
    });
    expect(
      http.get.mock.calls.some(([url]) =>
        String(url).includes("/experiments/v2/"),
      ),
    ).toBe(false);

    expect(screen.getByTestId("run-traces-summary")).toHaveTextContent(
      "2 traces · 1 error",
    );
    expect(within(rows[0]).getByText("ok")).toBeVisible();
    expect(within(rows[1]).getByText("error")).toBeVisible();
    expect(within(rows[1]).getByText("2")).toBeVisible();
    expect(screen.queryByText(/Couldn.t load this run.s traces/)).toBeNull();
  });

  it("shows one calm line when the run has no traces", async () => {
    http.get.mockResolvedValue(page([]));
    renderTraces();

    expect(
      await screen.findByText("This run has no traces yet."),
    ).toBeVisible();
    expect(screen.queryAllByTestId("run-trace-row")).toEqual([]);
    expect(screen.queryByText(/Couldn.t load this run.s traces/)).toBeNull();
    expect(requested().map((url) => url.pathname)).toEqual([ROUTE]);
  });

  it("pages through a run with more traces than one read returns", async () => {
    http.get.mockImplementation(async (url) => {
      const offset = new URL(String(url), "http://app.test").searchParams.get(
        "offset",
      );
      return offset === "0"
        ? page([trace(TRACE_A)], { has_more: true, items_with_trace: 101 })
        : page([trace(TRACE_B, { example_id: EXAMPLE_B })], {
            offset: 100,
            has_more: false,
            items_with_trace: 101,
          });
    });
    renderTraces();

    await screen.findAllByTestId("run-trace-row");
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() =>
      expect(
        requested().map((url) => url.searchParams.get("offset")),
      ).toContain("100"),
    );
    await waitFor(() =>
      expect(
        screen
          .getAllByTestId("run-trace-row")
          .map((row) => row.getAttribute("data-trace-id")),
      ).toEqual([TRACE_B]),
    );
    expect(screen.getByRole("button", { name: "Previous" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
  });

  it("says what failed and how to fix it when the read fails", async () => {
    http.get.mockRejectedValue(
      Object.assign(new Error("Traces are temporarily unavailable."), {
        statusCode: 503,
      }),
    );
    renderTraces();

    expect(
      await screen.findByText(
        "Couldn’t load this run’s traces. Retry, or reload the page.",
      ),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Retry" })).toBeVisible();
    expect(document.body.textContent).not.toContain("temporarily unavailable");
  });
});

describe("runTracesView", () => {
  const raw = {
    run_id: RUN,
    traces: [
      trace(TRACE_A),
      trace(TRACE_B, { example_id: EXAMPLE_B, item_status: "failed" }),
      trace("t-running", { item_status: "running" }),
    ],
    not_joined: [],
    items_with_trace: 3,
    limit: 100,
    offset: 0,
    has_more: false,
  };

  it("maps the route's traces onto the tab's rows", () => {
    const view = runTracesView(raw);
    expect(view.results.map((t) => [t.trace_id, t.status, t.trial])).toEqual([
      [TRACE_A, "ok", 1],
      [TRACE_B, "error", 1],
      ["t-running", "running", 1],
    ]);
    expect(view.results[0]).toMatchObject({
      project_id: PROJECT,
      row_label: "44444444…",
      duration_ms: null,
      tokens: null,
      cost_usd: null,
      failed_evals: [],
      first_error: null,
    });
    expect(view.summary).toMatchObject({ traces: 3, errors: 1 });
    expect(view.failed_evals).toEqual([]);
    expect(view).toMatchObject({ count: 3, offset: 0, has_more: false });
  });

  it("filters the page by status", () => {
    const view = runTracesView(raw, { status: "error" });
    expect(view.results.map((t) => t.trace_id)).toEqual([TRACE_B]);
    expect(view.count).toBe(1);
    expect(view.summary.traces).toBe(3);
  });

  it("keeps an absent page absent", () => {
    expect(runTracesView(undefined)).toBeUndefined();
    expect(runTracesView(null)).toBeNull();
  });
});
