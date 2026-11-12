import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "src/utils/test-utils";
import MyConnections from "./MyConnections";

const { start } = vi.hoisted(() => ({ start: vi.fn() }));
vi.mock("../hooks/useMyConnections", () => ({
  useStartConnect: () => ({ start, pending: null }),
  useRefreshMyConnections: () => vi.fn(),
}));
vi.mock("../hooks/useConnectorCatalog", () => ({
  useDisconnectAccount: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("Microsoft connection gate", () => {
  it.each(["microsoft-outlook", "microsoft-excel", "microsoft-teams"])(
    "%s renders Coming soon without a Connect button",
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
      expect(screen.getByText("Coming soon")).toBeTruthy();
      expect(screen.queryByRole("button", { name: /connect/i })).toBeNull();
      expect(start).not.toHaveBeenCalled();
    },
  );

  // A pilot connector the gateway does not serve yet reads the same reason as
  // the hold.
  it.each(["microsoft-outlook", "gmail"])(
    "%s says in words that a kept account is not served and how to remove it",
    (serverId) => {
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
      expect(
        screen.getByText("Not available yet; Disconnect removes it."),
      ).toBeTruthy();
      expect(screen.queryByText("Coming soon")).toBeNull();
      expect(
        screen.queryByRole("button", { name: /^Connect your account/ }),
      ).toBeNull();
      expect(
        screen.getByRole("button", {
          name: `Disconnect your account on ${serverId}`,
        }),
      ).toBeTruthy();
    },
  );

  // A grant the update stopped (no recorded client, from a Microsoft host):
  // it needs a new sign-in, which the hold also keeps from starting.
  it("says a held account stopped working after an update", () => {
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
        "This connection stopped working after an update and cannot be connected again yet; Disconnect removes it.",
      ),
    ).toBeTruthy();
    expect(
      screen.queryByText("Not available yet; Disconnect removes it."),
    ).toBeNull();
    expect(screen.queryByText("Coming soon")).toBeNull();
    expect(
      screen.queryByRole("button", { name: /^Connect your account/ }),
    ).toBeNull();
    expect(
      screen.getByRole("button", { name: "Disconnect your account on mail" }),
    ).toBeTruthy();
  });
});
