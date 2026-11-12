import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, fireEvent } from "src/utils/test-utils";
import { runTitle } from "src/api/develop/experiment-run-traces";
import ExperimentRunTracesPanel from "../ExperimentRunTracesPanel";

vi.mock("src/generated/api-contracts/api", () => ({
  experimentRunRead: vi.fn(),
  experimentRunTracesList: vi.fn(),
}));

const { experimentRunRead, experimentRunTracesList } = await import(
  "src/generated/api-contracts/api"
);

const RUN_A = "11111111-1111-4111-8111-111111111111";
const RUN_B = "22222222-2222-4222-8222-222222222222";
const PROJECT = "33333333-3333-4333-8333-333333333333";

// The generated client returns the axios response: { data: { status, result } }.
const ok = (result) => ({ data: { status: true, result }, status: 200 });

const header = (id, extra = {}) =>
  ok({
    id,
    status: "completed",
    metadata: { name: `run ${id.slice(0, 4)}` },
    ordinal: null,
    label: null,
    app_url: null,
    ...extra,
  });

const trace = (traceId, runId, extra = {}) => ({
  trace_id: traceId,
  root_span_id: `root-${traceId}`,
  example_id: "44444444-4444-4444-8444-444444444444",
  trial_number: 1,
  item_status: "completed",
  trace_run_id: runId,
  project_id: PROJECT,
  root_span_name: "experiment.trial",
  start_time: null,
  span_count: 3,
  trace_example_id: null,
  trace_trial: 1,
  ...extra,
});

const page = (runId, traces, notJoined = [], extra = {}) =>
  ok({
    run_id: runId,
    traces,
    not_joined: notJoined,
    items_with_trace: traces.length + notJoined.length,
    limit: 50,
    offset: 0,
    has_more: false,
    ...extra,
  });

const renderPanel = (runId) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ExperimentRunTracesPanel runId={runId} />
    </QueryClientProvider>,
  );
};

const listedTraceIds = () =>
  screen
    .queryAllByTestId("experiment-run-trace-row")
    .map((row) => row.getAttribute("data-trace-id"));

describe("ExperimentRunTracesPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("lists the run's traces from the run-id join, with a link to each trace", async () => {
    experimentRunRead.mockResolvedValue(header(RUN_A, { label: "#2" }));
    experimentRunTracesList.mockResolvedValue(
      page(RUN_A, [trace("trace-a1", RUN_A), trace("trace-a2", RUN_A)]),
    );

    renderPanel(RUN_A);

    await waitFor(() =>
      expect(listedTraceIds()).toEqual(["trace-a1", "trace-a2"]),
    );
    expect(experimentRunRead).toHaveBeenCalledWith(RUN_A, expect.anything());
    expect(experimentRunTracesList).toHaveBeenCalledWith(
      RUN_A,
      { limit: 50, offset: 0 },
      expect.anything(),
    );
    expect(screen.getByTestId("experiment-run-traces-panel")).toHaveAttribute(
      "data-run-id",
      RUN_A,
    );
    expect(screen.getByText(/SDK run #2 run 1111/)).toBeInTheDocument();
    const link = screen.getByRole("link", { name: "trace-a1" });
    expect(link).toHaveAttribute(
      "href",
      `/dashboard/observe/${PROJECT}/trace/trace-a1`,
    );
    expect(screen.getByTestId("experiment-run-traces-count")).toHaveTextContent(
      "2 items with a trace",
    );
  });

  it("shows another run's traces only on that run's panel", async () => {
    experimentRunRead.mockImplementation(async (id) => header(id));
    experimentRunTracesList.mockImplementation(async (id) =>
      id === RUN_A
        ? page(RUN_A, [trace("trace-a1", RUN_A)])
        : page(RUN_B, [trace("trace-b1", RUN_B)]),
    );

    const first = renderPanel(RUN_A);
    await waitFor(() => expect(listedTraceIds()).toEqual(["trace-a1"]));
    first.unmount();

    renderPanel(RUN_B);
    await waitFor(() => expect(listedTraceIds()).toEqual(["trace-b1"]));
    expect(screen.queryByText(/trace-a1/)).not.toBeInTheDocument();
  });

  it("lists what the server did not join, with its reason, and never as a row", async () => {
    experimentRunRead.mockResolvedValue(header(RUN_A));
    experimentRunTracesList.mockResolvedValue(
      page(
        RUN_A,
        [trace("trace-a1", RUN_A)],
        [
          { ...trace("trace-x", RUN_B), reason: "other_run" },
          { ...trace("trace-y", null), reason: "not_found", project_id: null },
        ],
      ),
    );

    renderPanel(RUN_A);

    await waitFor(() => expect(listedTraceIds()).toEqual(["trace-a1"]));
    const notJoined = screen.getAllByTestId("experiment-run-trace-not-joined");
    expect(
      notJoined.map((row) => [
        row.getAttribute("data-trace-id"),
        row.getAttribute("data-reason"),
      ]),
    ).toEqual([
      ["trace-x", "other_run"],
      ["trace-y", "not_found"],
    ]);
    expect(screen.getByText(/2 recorded traces are not listed/)).toBeVisible();
    expect(screen.getByText(/its root span names another run/)).toBeVisible();
  });

  it("accepts a run header whose nullable fields are null and says when no trace is stored yet", async () => {
    experimentRunRead.mockResolvedValue(
      header(RUN_A, { metadata: {}, ordinal: null, label: null }),
    );
    experimentRunTracesList.mockResolvedValue(page(RUN_A, []));

    renderPanel(RUN_A);

    await waitFor(() =>
      expect(
        screen.getByText("No trace of this run is in the trace store yet."),
      ).toBeVisible(),
    );
    expect(screen.getByText(`SDK run ${RUN_A}`)).toBeInTheDocument();
    expect(
      runTitle({ id: RUN_A, ordinal: 3, label: null, metadata: null }),
    ).toBe("#3");
  });

  it("pages with Next when the server has more", async () => {
    experimentRunRead.mockResolvedValue(header(RUN_A));
    experimentRunTracesList.mockImplementation(async (id, { offset }) =>
      offset === 0
        ? page(RUN_A, [trace("trace-a1", RUN_A)], [], {
            has_more: true,
            items_with_trace: 51,
          })
        : page(RUN_A, [trace("trace-a51", RUN_A)], [], {
            offset: 50,
            items_with_trace: 51,
          }),
    );

    renderPanel(RUN_A);
    await waitFor(() => expect(listedTraceIds()).toEqual(["trace-a1"]));
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => expect(listedTraceIds()).toEqual(["trace-a51"]));
    expect(experimentRunTracesList).toHaveBeenLastCalledWith(
      RUN_A,
      { limit: 50, offset: 50 },
      expect.anything(),
    );
  });

  it("starts another run at its first page when the run changes in place", async () => {
    experimentRunRead.mockImplementation(async (id) => header(id));
    experimentRunTracesList.mockImplementation(async (id, { offset }) => {
      if (id === RUN_B) return page(RUN_B, [trace("trace-b1", RUN_B)]);
      return offset === 0
        ? page(RUN_A, [trace("trace-a1", RUN_A)], [], {
            has_more: true,
            items_with_trace: 51,
          })
        : page(RUN_A, [trace("trace-a51", RUN_A)], [], {
            offset: 50,
            items_with_trace: 51,
          });
    });
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const view = render(
      <QueryClientProvider client={client}>
        <ExperimentRunTracesPanel runId={RUN_A} />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(listedTraceIds()).toEqual(["trace-a1"]));
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => expect(listedTraceIds()).toEqual(["trace-a51"]));

    // the same panel instance, a new run id (no unmount in between)
    view.rerender(
      <QueryClientProvider client={client}>
        <ExperimentRunTracesPanel runId={RUN_B} />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(listedTraceIds()).toEqual(["trace-b1"]));
    expect(experimentRunTracesList).toHaveBeenLastCalledWith(
      RUN_B,
      { limit: 50, offset: 0 },
      expect.anything(),
    );
    expect(experimentRunTracesList).not.toHaveBeenCalledWith(
      RUN_B,
      { limit: 50, offset: 50 },
      expect.anything(),
    );
  });

  it("says so when the run cannot be read", async () => {
    experimentRunRead.mockRejectedValue(new Error("404"));
    experimentRunTracesList.mockRejectedValue(new Error("404"));

    renderPanel(RUN_A);

    await waitFor(() =>
      expect(screen.getByTestId("experiment-run-traces-error")).toBeVisible(),
    );
    expect(listedTraceIds()).toEqual([]);
  });
});
