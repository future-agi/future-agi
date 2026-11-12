import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import axios from "src/utils/axios";
import ConnectedAgentsView from "../ConnectedAgentsView";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn() },
  endpoints: {
    agentDefinitions: { list: "/simulate/agent-definitions/" },
  },
}));

// A new workspace: no connected agents, so the empty state shows.
beforeEach(() => {
  axios.get.mockReset();
  axios.get.mockResolvedValue({ data: { count: 0, results: [] } });
});

const renderEmpty = (onConnect) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/dashboard/agents/connected"]}>
        <ConnectedAgentsView onConnect={onConnect} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
};

// The button leaves Agents for Simulate > Environment, so its label says so.
describe("ConnectedAgentsView connect button", () => {
  it("names the place it opens", async () => {
    const onConnect = vi.fn();
    renderEmpty(onConnect);

    const button = await screen.findByRole("button", {
      name: "Connect in Simulate > Environment",
    });
    expect(
      screen.queryByRole("button", { name: "Connect an agent" }),
    ).toBeNull();

    fireEvent.click(button);
    expect(onConnect).toHaveBeenCalledTimes(1);
  });
});

// Build environment offers a hosted platform, a source repository or a code
// upload, and hosted agents are simulated through their own platform. The
// empty state promises neither an endpoint nor a copy.
describe("ConnectedAgentsView empty state", () => {
  it("names only the ways in that exist", async () => {
    renderEmpty(vi.fn());

    expect(
      await screen.findByText(
        "Connect your agent from a hosted platform, a repository or an upload.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/an endpoint/i)).toBeNull();
    expect(screen.queryByText(/copy of it/i)).toBeNull();
  });
});
