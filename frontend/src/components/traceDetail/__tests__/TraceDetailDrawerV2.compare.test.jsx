import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "src/utils/test-utils";
import TraceDetailDrawerV2 from "../TraceDetailDrawerV2";

const mocks = vi.hoisted(() => ({
  getTraceDetail: vi.fn(),
  refetch: vi.fn(),
  responses: new Map(),
}));

vi.mock("src/api/project/trace-detail", () => ({
  useGetTraceDetail: (...args) => mocks.getTraceDetail(...args),
}));
vi.mock("src/api/project/saved-views", async (importOriginal) => ({
  ...(await importOriginal()),
  useGetSavedViews: () => ({ data: undefined }),
}));
vi.mock("src/api/shared-links", async (importOriginal) => ({
  ...(await importOriginal()),
  useSharedIssueTrace: () => ({ isLoading: false }),
}));
vi.mock("../DrawerToolbar", () => ({ default: () => null }));

const span = (traceId, id, name, latency, tokens, cost, children = []) => ({
  observation_span: {
    id,
    trace: traceId,
    name,
    observation_type: "chain",
    parent_span_id: id.endsWith("root") ? null : `${traceId}-root`,
    start_time: "2026-11-16T00:00:00Z",
    latency_ms: latency,
    total_tokens: tokens,
    cost,
    input: `${name} input`,
    output: `${name} output`,
  },
  children,
});

const traceA = {
  trace: { id: "trace-a", project: "project-1", tags: [] },
  observation_spans: [
    span("trace-a", "trace-a-root", "Baseline root", 1500, 120, 0.02, [
      span("trace-a", "trace-a-child", "Baseline child", 250, 15, 0.001),
    ]),
  ],
};
const traceB = {
  trace: { id: "trace-b", project: "project-1", tags: [] },
  observation_spans: [
    span("trace-b", "trace-b-root", "Candidate root", 2300, 240, 0.04, [
      span("trace-b", "trace-b-child", "Candidate child", 500, 30, 0.003),
    ]),
  ],
};
const ready = (data) => ({
  data,
  isLoading: false,
  isError: false,
  isFetching: false,
  refetch: mocks.refetch,
});

function renderDrawer(props = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const drawer = (nextProps) => (
    <QueryClientProvider client={client}>
      <TraceDetailDrawerV2
        open
        onClose={vi.fn()}
        traceId="trace-a"
        projectId="project-1"
        {...nextProps}
      />
    </QueryClientProvider>
  );
  const result = render(drawer(props));
  return {
    ...result,
    rerenderDrawer: (nextProps = props) => result.rerender(drawer(nextProps)),
  };
}

function openCompare() {
  fireEvent.click(screen.getByRole("button", { name: "Compare", exact: true }));
}

function chooseTrace(id = "trace-b") {
  fireEvent.change(screen.getByRole("textbox", { name: "Trace ID to compare" }), {
    target: { value: id },
  });
  fireEvent.click(screen.getByRole("button", { name: "Load trace" }));
}

function expectDetails(region, name, duration, tokens, cost) {
  expect(within(region).getByText(`${name} input`)).toBeInTheDocument();
  expect(within(region).getByText(`${name} output`)).toBeInTheDocument();
  expect(within(region).getByText(`Duration : ${duration}`)).toBeInTheDocument();
  expect(within(region).getByText(`Total tokens : ${tokens}`)).toBeInTheDocument();
  expect(within(region).getByText(`Cost : ${cost}`)).toBeInTheDocument();
}

describe("TraceDetailDrawerV2 compare", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Element.prototype.scrollIntoView = vi.fn();
    const storage = new Map([
      ["trace-view-default-project-1", JSON.stringify({ showAgentGraph: false })],
    ]);
    vi.stubGlobal("localStorage", {
      getItem: (key) => storage.get(key) ?? null,
      setItem: (key, value) => storage.set(key, value),
      removeItem: (key) => storage.delete(key),
    });
    mocks.responses = new Map([
      ["trace-a", ready(traceA)],
      ["trace-b", ready(traceB)],
    ]);
    mocks.getTraceDetail.mockImplementation((id) => mocks.responses.get(id) || ready(undefined));
  });
  afterEach(() => vi.unstubAllGlobals());

  it("shows both span trees, selected span inputs/outputs and metrics, then returns to the single view", () => {
    renderDrawer();
    openCompare();
    chooseTrace();

    const left = screen.getByRole("region", { name: "Current trace" });
    const right = screen.getByRole("region", { name: "Comparison trace" });
    expect(within(within(left).getByRole("region", { name: "Trace Tree" })).getByText("Baseline root")).toBeInTheDocument();
    expect(within(within(right).getByRole("region", { name: "Trace Tree" })).getByText("Candidate root")).toBeInTheDocument();
    expectDetails(left, "Baseline root", "1.5s", "120", "$0.02");
    expectDetails(right, "Candidate root", "2.3s", "240", "$0.04");
    expect(screen.getAllByRole("region", { name: "Trace Tree" })).toHaveLength(2);
    expect(screen.getByText("Baseline root input")).toBeInTheDocument();
    expect(screen.getByText("Candidate root input")).toBeInTheDocument();
    expect(mocks.getTraceDetail).toHaveBeenCalledWith("trace-b", { projectId: "project-1" });

    fireEvent.click(screen.getByRole("button", { name: "Close compare" }));
    expect(screen.queryByRole("region", { name: "Comparison trace" })).not.toBeInTheDocument();
    expect(screen.getAllByRole("region", { name: "Trace Tree" })).toHaveLength(1);
    expect(screen.getByText("Duration : 1.5s")).toBeInTheDocument();
  });

  it("selects spans independently on each side without changing the single-view selection", () => {
    renderDrawer();
    openCompare();
    chooseTrace();
    const left = screen.getByRole("region", { name: "Current trace" });
    const right = screen.getByRole("region", { name: "Comparison trace" });

    fireEvent.click(within(right).getByText("Candidate child"));
    expectDetails(right, "Candidate child", "500ms", "30", "$0.0030");
    expectDetails(left, "Baseline root", "1.5s", "120", "$0.02");
    fireEvent.click(within(left).getByText("Baseline child"));
    expectDetails(left, "Baseline child", "250ms", "15", "$0.0010");
    expectDetails(right, "Candidate child", "500ms", "30", "$0.0030");

    fireEvent.click(screen.getByRole("button", { name: "Close compare" }));
    expect(screen.getByText("Duration : 1.5s")).toBeInTheDocument();
  });

  it("rejects empty and current trace IDs and trims IDs before a project-scoped read", () => {
    renderDrawer();
    openCompare();
    expect(screen.getByRole("button", { name: "Load trace" })).toBeDisabled();
    fireEvent.change(screen.getByRole("textbox", { name: "Trace ID to compare" }), {
      target: { value: "  trace-a  " },
    });
    expect(screen.getByRole("button", { name: "Load trace" })).toBeDisabled();
    expect(screen.getByText("Choose a different trace ID.")).toBeInTheDocument();
    expect(mocks.getTraceDetail.mock.calls.every(([id]) => !id || id === "trace-a")).toBe(true);
    chooseTrace("  trace-b  ");
    expect(mocks.getTraceDetail).toHaveBeenCalledWith("trace-b", { projectId: "project-1" });
    expect(screen.getByRole("region", { name: "Comparison trace" })).toHaveTextContent("Candidate root");
  });

  it("keeps the current trace visible during loading and hides stale comparison data on failure", () => {
    mocks.responses.set("trace-b", { ...ready(undefined), isLoading: true });
    const { rerenderDrawer } = renderDrawer();
    openCompare();
    chooseTrace();
    expect(screen.getByRole("status")).toHaveTextContent("Loading comparison trace");
    expect(screen.getByRole("region", { name: "Current trace" })).toHaveTextContent("Baseline root");

    mocks.responses.set("trace-b", {
      ...ready(traceB),
      isError: true,
      error: { statusCode: 404 },
    });
    rerenderDrawer();
    expect(screen.getByRole("alert")).toHaveTextContent("Choose a trace from this project");
    expect(screen.queryByText("Candidate root")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry comparison" }));
    expect(mocks.refetch).toHaveBeenCalledOnce();

    mocks.responses.set("trace-b", ready(traceB));
    rerenderDrawer();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expectDetails(screen.getByRole("region", { name: "Comparison trace" }), "Candidate root", "2.3s", "240", "$0.04");
  });

  it("resets the comparison selection when loading a different trace", () => {
    const traceC = {
      trace: { id: "trace-c", tags: [] },
      observation_spans: [span("trace-c", "trace-c-root", "Replacement root", 700, 0, 0)],
    };
    mocks.responses.set("trace-c", ready(traceC));
    renderDrawer();
    openCompare();
    chooseTrace();
    fireEvent.click(within(screen.getByRole("region", { name: "Comparison trace" })).getByText("Candidate child"));
    chooseTrace("trace-c");
    expectDetails(screen.getByRole("region", { name: "Comparison trace" }), "Replacement root", "700ms", "0", "$0.00");
    expect(screen.queryByText("Candidate child output")).not.toBeInTheDocument();
  });

  it("clears comparison when navigating traces or closing and reopening the drawer", () => {
    const { rerenderDrawer } = renderDrawer();
    openCompare();
    chooseTrace();
    rerenderDrawer({ traceId: "trace-b" });
    expect(screen.queryByRole("region", { name: "Comparison trace" })).not.toBeInTheDocument();
    openCompare();
    chooseTrace("trace-a");
    rerenderDrawer({ traceId: "trace-b", open: false });
    rerenderDrawer({ traceId: "trace-b", open: true });
    expect(screen.queryByRole("region", { name: "Comparison trace" })).not.toBeInTheDocument();
    openCompare();
    expect(screen.getByRole("textbox", { name: "Trace ID to compare" })).toHaveValue("");
  });

  it("leaves comparison when the project changes and does not offer it to shared or unscoped viewers", () => {
    const { rerenderDrawer } = renderDrawer();
    openCompare();
    chooseTrace();
    rerenderDrawer({ projectId: "project-2" });
    expect(screen.queryByRole("region", { name: "Comparison trace" })).not.toBeInTheDocument();
    rerenderDrawer({ readOnly: true, sharedToken: "synthetic-share" });
    expect(screen.queryByRole("button", { name: "Compare", exact: true })).not.toBeInTheDocument();
    rerenderDrawer({ projectId: undefined });
    expect(screen.queryByRole("button", { name: "Compare", exact: true })).not.toBeInTheDocument();
  });
});
