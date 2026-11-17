import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import {
  QueryClient,
  QueryClientProvider,
  onlineManager,
} from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import axios from "src/utils/axios";
import AgentView from "../AgentView";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn() },
  endpoints: { agentPlayground: { listGraphs: "/agent-playground/graphs/" } },
}));

vi.mock("../AgentListView", () => ({
  default: () => <div data-testid="agent-list" />,
}));

vi.mock("../hooks/useCanEditAgent", () => ({
  default: () => ({ canCreate: true, canUpdate: true, canDelete: true }),
}));

vi.mock("../store", () => ({
  resetAgentListGridStore: vi.fn(),
  useAgentPlaygroundStoreShallow: (select) =>
    select({ setCurrentAgent: vi.fn() }),
}));

vi.mock("src/api/agent-playground/agent-playground", () => ({
  useCreateGraph: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock("src/components/svg-color", () => ({
  default: () => <span data-testid="svg-color" />,
}));

// The envelope the axios interceptor rejects with for a backend 500.
const SERVER_ERROR = {
  status: false,
  result: "Internal server error",
  statusCode: 500,
};

const listOf = (graphs) => ({
  data: { result: { graphs, metadata: { total_count: graphs.length } } },
});
const ONE_AGENT = listOf([{ id: "g1", name: "Support agent" }]);

const renderView = () =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <MemoryRouter>
        <AgentView embedded />
      </MemoryRouter>
    </QueryClientProvider>,
  );

beforeEach(() => {
  axios.get.mockReset();
});

describe("AgentView", () => {
  it("shows a loading skeleton, not a blank area, while the list loads", () => {
    axios.get.mockReturnValue(new Promise(() => {}));
    renderView();
    expect(
      screen.getByRole("progressbar", { name: "Loading agents" }),
    ).toBeInTheDocument();
  });

  it("says the agents couldn't load, not 'No agents yet', when the first load fails", async () => {
    axios.get.mockRejectedValue(SERVER_ERROR);
    renderView();
    expect(await screen.findByText("Couldn't load agents")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Try again. If it keeps failing, check your connection.",
    );
    expect(screen.queryByText("No agents yet")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Create agent" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();
  });

  it("says the agents couldn't load, not 'No agents yet', while offline", () => {
    onlineManager.setOnline(false);
    let view;
    try {
      view = renderView();
      expect(screen.getByText("Couldn't load agents")).toBeInTheDocument();
      expect(screen.queryByText("No agents yet")).not.toBeInTheDocument();
      expect(axios.get).not.toHaveBeenCalled();
    } finally {
      // Unmount first so going back online does not resume this request.
      view?.unmount();
      onlineManager.setOnline(true);
    }
  });

  it("Retry asks for the list again and shows the agents once it loads", async () => {
    axios.get
      .mockRejectedValueOnce(SERVER_ERROR)
      .mockResolvedValueOnce(ONE_AGENT);
    renderView();
    fireEvent.click(await screen.findByRole("button", { name: "Retry" }));
    expect(await screen.findByTestId("agent-list")).toBeInTheDocument();
    expect(axios.get).toHaveBeenCalledTimes(2);
    expect(screen.queryByText("Couldn't load agents")).not.toBeInTheDocument();
    expect(screen.queryByText("No agents yet")).not.toBeInTheDocument();
  });

  it("keeps 'No agents yet' for a list that loaded empty", async () => {
    axios.get.mockResolvedValue(listOf([]));
    renderView();
    expect(await screen.findByText("No agents yet")).toBeInTheDocument();
    expect(screen.queryByText("Couldn't load agents")).not.toBeInTheDocument();
  });

  it("shows the agent list when the first page has agents", async () => {
    axios.get.mockResolvedValue(ONE_AGENT);
    renderView();
    expect(await screen.findByTestId("agent-list")).toBeInTheDocument();
    expect(axios.get).toHaveBeenCalledWith("/agent-playground/graphs/", {
      params: { page_number: 1, page_size: 1 },
    });
  });
});
