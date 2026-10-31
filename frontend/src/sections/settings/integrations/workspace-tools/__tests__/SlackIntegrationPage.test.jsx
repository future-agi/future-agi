import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, screen } from "@testing-library/react";
import { renderWithRouter } from "src/utils/test-utils";
import SlackIntegrationPage from "../slack/SlackIntegrationPage";
import { getIntegrations, resetIntegrations } from "../mockStore";

const mocks = vi.hoisted(() => ({
  connections: [],
  connectionsState: { isLoading: false, isError: false },
  channels: [],
  channelsState: { isLoading: false, isError: false },
  channelPaging: {
    hasNextPage: false,
    isFetchingNextPage: false,
    fetchNextPage: vi.fn(),
  },
  install: vi.fn(),
  remove: vi.fn(),
  assign: vi.fn(),
}));

vi.mock("src/api/integrations", () => ({
  useIntegrationConnections: () => ({
    data: mocks.connections,
    ...mocks.connectionsState,
    refetch: vi.fn(),
  }),
  useStartSlackInstall: () => ({ mutate: mocks.install, isPending: false }),
  useSlackChannels: () => ({
    data: mocks.channelsState.isLoading
      ? undefined
      : { channels: mocks.channels },
    ...mocks.channelsState,
    ...mocks.channelPaging,
  }),
  useDeleteConnection: () => ({ mutate: mocks.remove, isPending: false }),
}));
vi.mock("src/components/svg-color", () => ({ default: () => <span /> }));
vi.mock("../slackConnection", async (importOriginal) => ({
  ...(await importOriginal()),
  openExternal: mocks.assign,
}));

const PAGE = "/dashboard/settings/integrations/slack";
const renderPage = (route = PAGE) =>
  renderWithRouter(<SlackIntegrationPage />, { route });
const active = {
  id: "c1",
  platform: "slack",
  display_name: "Acme Inc",
  status: "active",
};

describe("Slack integration page", () => {
  beforeEach(() => {
    mocks.connections = [];
    mocks.connectionsState = { isLoading: false, isError: false };
    mocks.channels = [];
    mocks.channelsState = { isLoading: false, isError: false };
    mocks.channelPaging = {
      hasNextPage: false,
      isFetchingNextPage: false,
      fetchNextPage: vi.fn(),
    };
    mocks.install.mockReset();
    mocks.remove.mockReset();
    window.sessionStorage.clear();
    window.localStorage.clear();
    mocks.assign.mockReset();
    act(() => resetIntegrations());
  });

  it("starts the real Slack install and comes back to this page", () => {
    mocks.install.mockImplementation((_, { onSuccess }) =>
      onSuccess({
        data: {
          result: {
            authorization_url: "https://slack.com/oauth/v2/authorize?x=1",
          },
        },
      }),
    );
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "Add to Slack" }));
    expect(mocks.install).toHaveBeenCalledTimes(1);
    expect(window.sessionStorage.getItem("slack-integration-return-to")).toBe(
      PAGE,
    );
    expect(mocks.assign).toHaveBeenCalledWith(
      "https://slack.com/oauth/v2/authorize?x=1",
    );
  });

  it("shows the backend error when the install can't start, and lets you try again", () => {
    mocks.install.mockImplementation((_, { onError }) =>
      onError({ response: { data: { message: "Slack is not configured" } } }),
    );
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "Add to Slack" }));
    expect(screen.getByText("Slack is not configured")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add to Slack" })).toBeEnabled();
  });

  it("shows the connected workspace, every notification and the real channels", () => {
    mocks.connections = [active];
    mocks.channels = [
      { id: "C1", name: "support-team" },
      { id: "C2", name: "ops-room" },
    ];
    renderPage();
    expect(screen.getByText("Connected")).toBeInTheDocument();
    expect(
      screen.getByText(/Added to Acme Inc · default channel #support-team/),
    ).toBeInTheDocument();
    for (const label of [
      "Simulation finished",
      "Must-pass scenario failing",
      "Alert rule triggered",
    ]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    expect(screen.getByText("Recent messages")).toBeInTheDocument();
  });

  it("treats a Slack connection in an error state as needing reconnecting", () => {
    mocks.connections = [
      { ...active, status: "error", status_message: "Token revoked in Slack" },
    ];
    renderPage();
    expect(
      screen.getByRole("button", { name: "Add to Slack" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Token revoked in Slack")).toBeInTheDocument();
  });

  it("reconnects a connected workspace to renew Slack access, as dev's manage dialog did", () => {
    mocks.connections = [active];
    mocks.install.mockImplementation((_, { onSuccess }) =>
      onSuccess({
        data: {
          result: {
            authorization_url: "https://slack.com/oauth/v2/authorize?x=2",
          },
        },
      }),
    );
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "More actions" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Reconnect Slack" }));
    // The same connection is renewed, not a second one installed.
    expect(mocks.install).toHaveBeenCalledWith("c1", expect.anything());
    expect(window.sessionStorage.getItem("slack-integration-return-to")).toBe(
      PAGE,
    );
    expect(mocks.assign).toHaveBeenCalledWith(
      "https://slack.com/oauth/v2/authorize?x=2",
    );
  });

  it("disconnects the real connection only after a confirm that names the Error Feed impact", () => {
    mocks.connections = [active];
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "More actions" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Disconnect" }));
    expect(
      screen.getByText(/Error Feed alerts that post to this workspace stop/),
    ).toBeInTheDocument();
    expect(mocks.remove).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Disconnect" }));
    expect(mocks.remove).toHaveBeenCalledWith("c1", expect.anything());
  });

  it("says Slack is connected after coming back from Slack", () => {
    mocks.connections = [active];
    renderPage(`${PAGE}?slack=connected`);
    expect(screen.getByText("Slack connected.")).toBeInTheDocument();
  });

  it("says Slack could not connect after coming back from Slack", () => {
    renderPage(`${PAGE}?slack=error`);
    expect(
      screen.getByText("Could not connect Slack. Please try again."),
    ).toBeInTheDocument();
  });

  it("loads every page of the workspace's channels", () => {
    mocks.connections = [active];
    mocks.channels = [{ id: "C1", name: "support-team" }];
    mocks.channelPaging = {
      hasNextPage: true,
      isFetchingNextPage: false,
      fetchNextPage: vi.fn(),
    };
    renderPage();
    expect(mocks.channelPaging.fetchNextPage).toHaveBeenCalled();
  });

  it("remembers the first real channel as the default so the card can show it", () => {
    mocks.connections = [active];
    mocks.channels = [
      { id: "C1", name: "support-team" },
      { id: "C2", name: "ops-room" },
    ];
    renderPage();
    expect(getIntegrations().slackDefaultChannel).toBe("#support-team");
  });

  it("waits for the connections instead of offering Add to Slack", () => {
    mocks.connectionsState = { isLoading: true, isError: false };
    renderPage();
    expect(
      screen.queryByRole("button", { name: "Add to Slack" }),
    ).not.toBeInTheDocument();
  });

  it("says when the connections can't load instead of offering Add to Slack", () => {
    mocks.connectionsState = { isLoading: false, isError: true };
    renderPage();
    expect(
      screen.getByText("Couldn't load your Slack connection."),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Add to Slack" }),
    ).not.toBeInTheDocument();
  });

  it("never shows sample channel names for a real workspace while its channels load", () => {
    mocks.connections = [active];
    mocks.channelsState = { isLoading: true, isError: false };
    renderPage();
    expect(screen.queryByText(/#agent-quality/)).not.toBeInTheDocument();
    expect(screen.getByText(/Loading channels/)).toBeInTheDocument();
  });

  it("uses the active connection when another Slack connection is in an error state", () => {
    mocks.connections = [
      { ...active, id: "c0", display_name: "Old Inc", status: "error" },
      active,
    ];
    renderPage();
    expect(screen.getByText("Connected")).toBeInTheDocument();
    expect(screen.getByText(/Added to Acme Inc/)).toBeInTheDocument();
  });
});
