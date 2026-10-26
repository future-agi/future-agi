import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, screen, within } from "@testing-library/react";
import { renderWithRouter } from "src/utils/test-utils";
import IntegrationsList from "../IntegrationsList";
import {
  connectCodingAgent,
  resetIntegrations,
  setSlackDefaultChannel,
} from "../workspace-tools/mockStore";

const mocks = vi.hoisted(() => ({ connections: [], install: vi.fn() }));

vi.mock("src/api/integrations", () => ({
  integrationKeys: {
    all: ["integrations"],
    connections: () => ["integrations", "connections"],
  },
  useIntegrationConnections: () => ({
    data: mocks.connections,
    isLoading: false,
    isError: false,
  }),
  useStartSlackInstall: () => ({ mutate: mocks.install, isPending: false }),
  useDeleteConnection: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("../AddIntegrationWizard", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => <span /> }));

const renderList = (route = "/dashboard/settings/integrations") => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return renderWithRouter(
    <QueryClientProvider client={client}>
      <IntegrationsList />
    </QueryClientProvider>,
    { route },
  );
};

const card = (name) => screen.getByTestId(`workspace-tool-${name}`);

describe("Integrations list: Code & collaboration", () => {
  beforeEach(() => {
    mocks.connections = [];
    mocks.install.mockReset();
    window.localStorage.clear();
    window.sessionStorage.clear();
    resetIntegrations();
  });

  it("shows GitHub, Slack and Coding agents, all not connected at first", () => {
    renderList();
    expect(screen.getByText("Code & collaboration")).toBeInTheDocument();
    for (const id of ["github", "slack", "coding-agents"]) {
      expect(within(card(id)).getByText("Not connected")).toBeInTheDocument();
    }
  });

  it("shows Slack as connected from a real active Slack connection", () => {
    mocks.connections = [
      {
        id: "c1",
        platform: "slack",
        display_name: "Acme Inc",
        status: "active",
      },
    ];
    renderList();
    expect(within(card("slack")).getByText("Connected")).toBeInTheDocument();
    expect(within(card("slack")).getByText(/Acme Inc/)).toBeInTheDocument();
  });

  it("shows the Slack default channel once one is picked", () => {
    mocks.connections = [
      {
        id: "c1",
        platform: "slack",
        display_name: "Acme Inc",
        status: "active",
      },
    ];
    setSlackDefaultChannel("#eng-alerts");
    renderList();
    expect(
      within(card("slack")).getByText("Acme Inc · #eng-alerts"),
    ).toBeInTheDocument();
  });

  it("does not show a Slack connection in an error state as connected", () => {
    mocks.connections = [
      {
        id: "c1",
        platform: "slack",
        display_name: "Acme Inc",
        status: "error",
      },
    ];
    renderList();
    expect(
      within(card("slack")).getByText("Not connected"),
    ).toBeInTheDocument();
  });

  it("shows Slack as connected when an active connection follows an errored one", () => {
    mocks.connections = [
      { id: "c0", platform: "slack", display_name: "Old Inc", status: "error" },
      {
        id: "c1",
        platform: "slack",
        display_name: "Acme Inc",
        status: "active",
      },
    ];
    renderList();
    expect(within(card("slack")).getByText("Connected")).toBeInTheDocument();
    expect(within(card("slack")).getByText(/Acme Inc/)).toBeInTheDocument();
  });

  it("keeps any extra Slack connection reachable in the connected list", () => {
    mocks.connections = [
      {
        id: "c1",
        platform: "slack",
        display_name: "Acme Inc",
        status: "active",
      },
      {
        id: "c2",
        platform: "slack",
        display_name: "Acme Labs",
        status: "active",
      },
    ];
    renderList();
    const observability = screen.getByTestId("observability-platforms");
    expect(within(observability).getByText("Acme Labs")).toBeInTheDocument();
    expect(
      within(observability).queryByText("Acme Inc"),
    ).not.toBeInTheDocument();
  });

  it("keeps Slack out of the observability lists", () => {
    mocks.connections = [
      {
        id: "c1",
        platform: "slack",
        display_name: "Acme Inc",
        status: "active",
      },
    ];
    renderList();
    const observability = screen.getByTestId("observability-platforms");
    expect(within(observability).queryByText("Slack")).not.toBeInTheDocument();
    expect(
      within(observability).queryByText("Acme Inc"),
    ).not.toBeInTheDocument();
  });

  it("keeps the Coding agents card's default text when your agents are connected", () => {
    connectCodingAgent("claude_code", "plugin");
    connectCodingAgent("codex", "mcp");
    renderList();
    expect(
      within(card("coding-agents")).getByText("Connected"),
    ).toBeInTheDocument();
    expect(
      within(card("coding-agents")).getByText(
        "Let Claude Code, Codex or Cursor fix issues. Future AGI checks every change.",
      ),
    ).toBeInTheDocument();
    expect(
      within(card("coding-agents")).queryByText(/plugin/),
    ).not.toBeInTheDocument();
  });

  it("opens the GitHub page from its card", () => {
    renderList();
    fireEvent.click(within(card("github")).getByRole("button"));
    expect(window.location.pathname).toBe(
      "/dashboard/settings/integrations/github",
    );
  });

  it("opens the workspace-level page from the workspace settings", () => {
    renderList("/dashboard/settings/workspace/w1/integrations");
    fireEvent.click(within(card("coding-agents")).getByRole("button"));
    expect(window.location.pathname).toBe(
      "/dashboard/settings/workspace/w1/integrations/coding-agents",
    );
  });

  it("comes back to the Slack page after Slack OAuth succeeds", () => {
    window.sessionStorage.setItem(
      "slack-integration-return-to",
      "/dashboard/settings/integrations/slack",
    );
    renderList(
      "/dashboard/settings/integrations?slack=connected&connection_id=c1",
    );
    expect(window.location.pathname).toBe(
      "/dashboard/settings/integrations/slack",
    );
    expect(window.location.search).toBe("?slack=connected");
  });

  it("comes back to the Slack page when Slack OAuth fails", () => {
    window.sessionStorage.setItem(
      "slack-integration-return-to",
      "/dashboard/settings/integrations/slack",
    );
    renderList("/dashboard/settings/integrations?slack=error");
    expect(window.location.pathname).toBe(
      "/dashboard/settings/integrations/slack",
    );
    expect(window.location.search).toBe("?slack=error");
    expect(
      window.sessionStorage.getItem("slack-integration-return-to"),
    ).toBeNull();
  });

  it("forgets an Error Feed return path when Slack OAuth fails, and shows the error here", () => {
    window.sessionStorage.setItem(
      "slack-integration-return-to",
      "/dashboard/error-feed/alerts/new",
    );
    renderList("/dashboard/settings/integrations?slack=error");
    expect(window.location.pathname).toBe("/dashboard/settings/integrations");
    expect(
      window.sessionStorage.getItem("slack-integration-return-to"),
    ).toBeNull();
    expect(
      screen.getByText("Could not connect Slack. Please try again."),
    ).toBeInTheDocument();
  });

  it("still starts the Slack install when sent here with platform=slack", () => {
    renderList("/dashboard/settings/integrations?platform=slack");
    expect(mocks.install).toHaveBeenCalledTimes(1);
  });
});
