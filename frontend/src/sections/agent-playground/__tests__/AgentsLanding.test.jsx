import { describe, it, expect, vi } from "vitest";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HelmetProvider } from "react-helmet-async";
import {
  MemoryRouter,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from "react-router-dom";
import AgentsLanding from "../AgentsLanding";
import AgentsIndexRedirect from "../AgentsIndexRedirect";
import ConnectedAgentsTab from "../ConnectedAgentsTab";
import PlaygroundAgentsTab from "../PlaygroundAgentsTab";
import {
  CONNECTED_AGENTS_PATH,
  PLAYGROUND_AGENTS_PATH,
  agentsSidebarPath,
  agentsTabPath,
  resolveAgentsTab,
} from "../agentsTabs";
import AgentListView from "../AgentListView/AgentListView";

vi.mock("../AgentView", () => ({
  default: ({ embedded }) => (
    <div data-testid="playground-list" data-embedded={String(embedded)} />
  ),
}));

vi.mock("../connected/ConnectedAgentsView", () => ({
  default: ({ onConnect }) => (
    <div data-testid="connected-list">
      <button type="button" onClick={onConnect}>
        Connect an agent
      </button>
    </div>
  ),
}));

vi.mock("../components/CreateAgentsButton", () => ({
  default: () => <button type="button">Create Agents</button>,
}));

vi.mock("src/components/tooltip/CustomTooltip", () => ({
  default: ({ title, show, children }) => (
    <div
      data-testid="custom-tooltip"
      data-title={title}
      data-show={String(show)}
    >
      {children}
    </div>
  ),
}));

vi.mock("../hooks/useCanEditAgent", () => ({
  default: () => ({ canCreate: true, canUpdate: true, canDelete: true }),
}));

vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn(() =>
      Promise.resolve({
        data: { result: { graphs: [], metadata: { total_count: 0 } } },
      }),
    ),
  },
  endpoints: { agentPlayground: { listGraphs: "/graphs/" } },
}));

let currentLocation = null;
let goBack = () => {};
const LocationProbe = () => {
  currentLocation = useLocation();
  const navigate = useNavigate();
  goBack = () => navigate(-1);
  return null;
};

// Mirrors the agents block in routes/sections/dashboard.jsx.
const agentsRoutes = (
  <Routes>
    <Route path="/dashboard/agents" element={<AgentsLanding />}>
      <Route index element={<AgentsIndexRedirect />} />
      <Route path="connected" element={<ConnectedAgentsTab />} />
      <Route path="playground" element={<PlaygroundAgentsTab />} />
    </Route>
    <Route path="*" element={<div data-testid="elsewhere" />} />
  </Routes>
);

const renderAt = (url, ui = agentsRoutes) =>
  render(
    <HelmetProvider>
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter initialEntries={[url]}>
          {ui}
          <LocationProbe />
        </MemoryRouter>
      </QueryClientProvider>
    </HelmetProvider>,
  );

describe("resolveAgentsTab", () => {
  it("opens Connected agents unless the Playground is asked for", () => {
    expect(resolveAgentsTab(undefined)).toBe("connected");
    expect(resolveAgentsTab(null)).toBe("connected");
    expect(resolveAgentsTab("")).toBe("connected");
    expect(resolveAgentsTab("garbage")).toBe("connected");
    expect(resolveAgentsTab("connected")).toBe("connected");
    expect(resolveAgentsTab("playground")).toBe("playground");
  });
});

describe("agents tab paths", () => {
  it("gives each tab its own path", () => {
    expect(CONNECTED_AGENTS_PATH).toBe("/dashboard/agents/connected");
    expect(PLAYGROUND_AGENTS_PATH).toBe("/dashboard/agents/playground");
    expect(agentsTabPath("connected")).toBe(CONNECTED_AGENTS_PATH);
    expect(agentsTabPath("playground")).toBe(PLAYGROUND_AGENTS_PATH);
  });
});

describe("agentsSidebarPath", () => {
  it("keeps the tab you are on", () => {
    expect(agentsSidebarPath("/dashboard/agents/playground")).toBe(
      PLAYGROUND_AGENTS_PATH,
    );
    expect(agentsSidebarPath("/dashboard/agents/connected")).toBe(
      CONNECTED_AGENTS_PATH,
    );
  });

  it("returns to the tab a detail page belongs to", () => {
    expect(agentsSidebarPath("/dashboard/agents/playground/abc/build")).toBe(
      PLAYGROUND_AGENTS_PATH,
    );
    expect(agentsSidebarPath("/dashboard/agents/connected/abc")).toBe(
      CONNECTED_AGENTS_PATH,
    );
  });

  it("opens Connected agents from anywhere else", () => {
    expect(agentsSidebarPath("/dashboard/prompt")).toBe(CONNECTED_AGENTS_PATH);
    expect(agentsSidebarPath("/dashboard/agents")).toBe(CONNECTED_AGENTS_PATH);
    expect(agentsSidebarPath("/dashboard/agents-other/playground")).toBe(
      CONNECTED_AGENTS_PATH,
    );
  });
});

describe("AgentsIndexRedirect", () => {
  it("sends the bare Agents page to Connected agents", async () => {
    renderAt("/dashboard/agents");
    await screen.findByTestId("connected-list");
    expect(currentLocation.pathname).toBe("/dashboard/agents/connected");
    expect(currentLocation.search).toBe("");
  });

  it("sends a legacy ?tab=playground link to the Playground path", async () => {
    renderAt("/dashboard/agents?tab=playground");
    await screen.findByTestId("playground-list");
    expect(currentLocation.pathname).toBe("/dashboard/agents/playground");
    expect(currentLocation.search).toBe("");
  });

  it("sends an unknown legacy tab to Connected agents", async () => {
    renderAt("/dashboard/agents?tab=garbage");
    await screen.findByTestId("connected-list");
    expect(currentLocation.pathname).toBe("/dashboard/agents/connected");
  });

  it("keeps other query params when it redirects", async () => {
    renderAt("/dashboard/agents?tab=connected&moved=agent-definitions");
    await screen.findByTestId("connected-list");
    expect(currentLocation.pathname).toBe("/dashboard/agents/connected");
    expect(currentLocation.search).toBe("?moved=agent-definitions");
  });
});

describe("AgentsLanding", () => {
  it("selects the tab named in the path", () => {
    renderAt("/dashboard/agents/playground");
    expect(
      screen.getByRole("tab", { name: "Agent Playground" }),
    ).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("playground-list")).toHaveAttribute(
      "data-embedded",
      "true",
    );
    expect(screen.queryByTestId("connected-list")).toBeNull();
  });

  it("switches tabs by path, and Back returns to the previous tab", async () => {
    renderAt("/dashboard/agents/connected");
    fireEvent.click(screen.getByRole("tab", { name: "Agent Playground" }));

    await screen.findByTestId("playground-list");
    expect(currentLocation.pathname).toBe("/dashboard/agents/playground");
    expect(currentLocation.search).toBe("");

    act(() => goBack());
    await screen.findByTestId("connected-list");
    expect(currentLocation.pathname).toBe("/dashboard/agents/connected");
  });

  it("sends Connect an agent to the environments build page", () => {
    renderAt("/dashboard/agents/connected");
    fireEvent.click(screen.getByRole("button", { name: "Connect an agent" }));
    expect(currentLocation.pathname).toBe("/dashboard/simulate/environments");
    expect(currentLocation.search).toBe("?tab=build");
  });

  it("explains the page in a CustomTooltip on the header info icon", () => {
    renderAt("/dashboard/agents/connected");
    const tip = screen
      .getAllByTestId("custom-tooltip")
      .find((t) =>
        t.dataset.title?.startsWith("Build agents in the Playground"),
      );
    expect(tip).toBeDefined();
    expect(tip).toHaveAttribute("data-show", "true");
    expect(tip.dataset.title).toBe(
      "Build agents in the Playground, or connect agents that already run somewhere else to test them in simulations.",
    );
  });

  it("shows the Agents header and the Create Agents button", () => {
    renderAt("/dashboard/agents/connected");
    expect(screen.getByText("Agents")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Create Agents" }),
    ).toBeInTheDocument();
  });
});

describe("AgentListView embedded", () => {
  it("drops its own Agent Playground header and docs link", async () => {
    renderAt("/dashboard/agents/playground", <AgentListView embedded />);
    await waitFor(() =>
      expect(screen.getByPlaceholderText(/search/i)).toBeInTheDocument(),
    );
    expect(screen.queryByText("Agent Playground")).toBeNull();
    expect(screen.queryByText("View Docs")).toBeNull();
  });
});
