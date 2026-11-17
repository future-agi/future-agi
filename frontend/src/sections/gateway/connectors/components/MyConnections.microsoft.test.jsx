import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";
import { NOT_IN_RELEASE } from "src/components/NotInRelease/notInRelease";
import MyConnections from "./MyConnections";

const { start, disconnectAccount } = vi.hoisted(() => ({
  start: vi.fn(),
  disconnectAccount: vi.fn(),
}));
vi.mock("../hooks/useMyConnections", () => ({
  useStartConnect: () => ({ start, pending: null }),
  useRefreshMyConnections: () => vi.fn(),
}));
vi.mock("../hooks/useConnectorCatalog", () => ({
  useDisconnectAccount: () => ({
    mutateAsync: disconnectAccount,
    isPending: false,
  }),
}));
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

// Disconnect on the row, confirmed in its dialog: the row's own disconnect
// address is asked to remove the account.
async function disconnectsAt(serverId) {
  disconnectAccount.mockResolvedValueOnce({
    disconnected: true,
    gateway_synced: true,
  });
  fireEvent.click(
    screen.getByRole("button", {
      name: `Disconnect your account on ${serverId}`,
    }),
  );
  fireEvent.click(
    within(screen.getByRole("dialog")).getByRole("button", {
      name: "Disconnect",
    }),
  );
  await waitFor(() => expect(disconnectAccount).toHaveBeenCalledTimes(1));
  expect(disconnectAccount.mock.calls[0][0].url).toMatch(
    new RegExp(`/agentcc/mcp/connect/${serverId}/$`),
  );
}

describe("Microsoft connection gate", () => {
  it.each(["microsoft-outlook", "microsoft-excel", "microsoft-teams"])(
    "%s says it is not available in this release, on the shared chip, without a Connect button",
    (serverId) => {
      render(
        <MyConnections
          connections={[{
            server_id: serverId,
            connected: false,
            status: null,
            start: { state: "unavailable", reason: "microsoft_coming_soon" },
            start_url: null,
            disconnect_url: null,
          }]}
          onBindingSpent={vi.fn()}
        />,
      );
      const line = screen.getByText(NOT_IN_RELEASE);
      expect(line.closest("[data-not-in-release]")).not.toBeNull();
      expect(screen.queryByText("Coming soon")).toBeNull();
      expect(screen.getByText("Not connected")).toBeTruthy();
      expect(screen.queryByRole("button", { name: /connect/i })).toBeNull();
      expect(start).not.toHaveBeenCalled();
    },
  );

  // A pilot connector the gateway does not serve reads the same reason as the
  // hold.
  it.each(["microsoft-outlook", "gmail"])(
    "%s shows a kept account the shared chip and how to remove it, and Disconnect removes it",
    async (serverId) => {
      render(
        <MyConnections
          connections={[
            {
              server_id: serverId,
              connected: false,
              status: "active",
              start: { state: "unavailable", reason: "microsoft_coming_soon" },
              start_url: null,
              disconnect_url: `/agentcc/mcp/connect/${serverId}/`,
            },
          ]}
          onBindingSpent={vi.fn()}
        />,
      );
      const line = screen.getByText(NOT_IN_RELEASE);
      expect(line.closest("[data-not-in-release]")).not.toBeNull();
      expect(screen.getByText("Disconnect removes it.")).toBeTruthy();
      expect(screen.queryByText(/\byet\b/)).toBeNull();
      expect(screen.queryByText(/coming soon/i)).toBeNull();
      expect(
        screen.queryByRole("button", { name: /^Connect your account/ }),
      ).toBeNull();
      await disconnectsAt(serverId);
    },
  );

  // A grant the update stopped (no recorded client, from a Microsoft host):
  // it needs a new sign-in, which the hold also keeps from starting.
  it("says a held account stopped working after an update, and Disconnect removes it", async () => {
    render(
      <MyConnections
        connections={[
          {
            server_id: "mail",
            connected: false,
            status: "needs_reauth",
            start: { state: "unavailable", reason: "microsoft_coming_soon" },
            start_url: null,
            disconnect_url: "/agentcc/mcp/connect/mail/",
          },
        ]}
        onBindingSpent={vi.fn()}
      />,
    );
    expect(
      screen.getByText(
        "This connection stopped working after an update and cannot be connected again in this release; Disconnect removes it.",
      ),
    ).toBeTruthy();
    expect(screen.queryByText("Disconnect removes it.")).toBeNull();
    expect(screen.queryByText(NOT_IN_RELEASE)).toBeNull();
    expect(screen.queryByText(/\byet\b/)).toBeNull();
    expect(screen.queryByText(/coming soon/i)).toBeNull();
    expect(
      screen.queryByRole("button", { name: /^Connect your account/ }),
    ).toBeNull();
    await disconnectsAt("mail");
  });

  // A start the hold refuses says so in a sentence: the one the control
  // plane uses for a connector this release does not connect, true of a
  // Microsoft app and of a pilot app, which share the reason.
  it("says in a sentence that a refused start is not available in this release", async () => {
    start.mockRejectedValueOnce({
      code: "microsoft_coming_soon",
      message: "Held.",
    });
    render(
      <MyConnections
        connections={[
          {
            server_id: "microsoft-outlook",
            connected: false,
            status: null,
            start: { state: "ready" },
            start_url: "/agentcc/mcp/connect/microsoft-outlook/start/",
            disconnect_url: null,
          },
        ]}
        onBindingSpent={vi.fn()}
      />,
    );
    fireEvent.click(
      screen.getByRole("button", {
        name: "Connect your account on microsoft-outlook",
      }),
    );
    expect((await screen.findByRole("alert")).textContent).toBe(
      "Connections to this app are not available in this release.",
    );
    expect(screen.queryByText("Coming soon")).toBeNull();
  });
});
