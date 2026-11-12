import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import Executions from "../Executions";

const executionsState = { current: {} };

vi.mock("src/api/agent-playground/agent-playground", () => ({
  useGetExecutions: (...args) => {
    executionsState.args = args;
    return executionsState.current;
  },
}));

vi.mock("../ExecutionsList", () => ({
  default: ({ executions }) => (
    <ul data-testid="executions-list">
      {executions.map((e) => (
        <li key={e.id}>{e.id}</li>
      ))}
    </ul>
  ),
}));

vi.mock("../ExecutionDetailView", () => ({
  default: () => null,
}));

const RUN = {
  id: "ex-1",
  status: "SUCCESS",
  started_at: "2026-11-01T10:00:00Z",
  completed_at: "2026-11-01T10:00:05Z",
};

const page = (executions) => ({
  data: { result: { executions, metadata: { next_page: null } } },
});

// The shape useInfiniteQuery returns; each test overrides what it needs.
const state = (overrides = {}) => ({
  data: undefined,
  isLoading: false,
  isError: false,
  isPaused: false,
  isFetching: false,
  refetch: vi.fn(),
  isFetchingNextPage: false,
  fetchNextPage: vi.fn(),
  hasNextPage: false,
  ...overrides,
});

const renderTab = () =>
  render(
    <MemoryRouter initialEntries={["/agents/g1/executions"]}>
      <Routes>
        <Route path="/agents/:agentId/executions" element={<Executions />} />
      </Routes>
    </MemoryRouter>,
  );

beforeEach(() => {
  executionsState.current = state();
  executionsState.args = undefined;
});

describe("Executions", () => {
  it("asks for this agent's executions", () => {
    renderTab();
    expect(executionsState.args).toEqual(["g1"]);
  });

  it("shows a loading skeleton while the first page loads", () => {
    executionsState.current = state({ isLoading: true, isFetching: true });
    renderTab();
    expect(screen.getByRole("progressbar")).toBeInTheDocument();
    expect(screen.queryByText("No executions yet")).not.toBeInTheDocument();
  });

  it("says the executions couldn't load, not 'No executions yet', when the first load fails", () => {
    executionsState.current = state({
      isError: true,
      error: { status: false, result: "Internal server error" },
    });
    renderTab();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Couldn't load executions",
    );
    expect(screen.queryByText("No executions yet")).not.toBeInTheDocument();
    expect(
      screen.queryByText(
        "Run your workflow from the Agent Builder to see results here",
      ),
    ).not.toBeInTheDocument();
  });

  it("says the executions couldn't load, not 'No executions yet', while offline", () => {
    // useInfiniteQuery while offline: still pending, waiting for a connection.
    executionsState.current = state({ isPaused: true });
    renderTab();
    expect(screen.getByText("Couldn't load executions")).toBeInTheDocument();
    expect(screen.queryByText("No executions yet")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();
  });

  it("Retry asks for the executions again", () => {
    const refetch = vi.fn();
    executionsState.current = state({ isError: true, refetch });
    renderTab();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it("disables Retry while it is asking again", () => {
    executionsState.current = state({ isError: true, isFetching: true });
    renderTab();
    expect(screen.getByRole("button", { name: /^Retrying/ })).toBeDisabled();
    expect(screen.queryByText("No executions yet")).not.toBeInTheDocument();
  });

  it("keeps 'No executions yet' for an agent with no runs", () => {
    executionsState.current = state({ data: { pages: [page([])] } });
    renderTab();
    expect(screen.getByText("No executions yet")).toBeInTheDocument();
    expect(
      screen.queryByText("Couldn't load executions"),
    ).not.toBeInTheDocument();
  });

  it("keeps the loaded executions on screen when a later page fails", () => {
    executionsState.current = state({
      isError: true,
      data: { pages: [page([RUN])] },
    });
    renderTab();
    expect(screen.getByTestId("executions-list")).toHaveTextContent("ex-1");
    expect(
      screen.queryByText("Couldn't load executions"),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("No executions yet")).not.toBeInTheDocument();
  });
});
