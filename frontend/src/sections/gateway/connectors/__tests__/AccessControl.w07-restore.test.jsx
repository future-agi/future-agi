import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderMcp } from "../../mcp/testing/renderMcp";
import AccessControl from "../components/AccessControl";

const { status } = vi.hoisted(() => ({ status: { current: {} } }));
vi.mock("../../mcp/hooks/useMCPConfig", () => ({
  useMCPStatus: () => status.current,
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));

const NOT_AVAILABLE = "Not available in this deployment";
const SERVER = {
  server_id: "fixture-app",
  virtual_server: { slug: "fixture-app-vs" },
  tools: [{ name: "read_page", gateway_posture: "allow" }],
};
const show = () =>
  renderMcp(
    <AccessControl
      appName="Fixture"
      provider={null}
      server={SERVER}
      gatewayId="gw"
    />,
  );

// The app drawer's Virtual server row and its
// tool-rule note stay while mcp-status loads, fails or reports an unreachable
// gateway; a virtual-server switch that is off says so in the row.
describe("the app drawer's Virtual server row", () => {
  beforeEach(() => {
    status.current = {};
  });

  it.each([
    ["loading", { isLoading: true, isPending: true }],
    ["a status read error", { isError: true }],
    [
      "an unreachable gateway",
      {
        data: {
          enabled: false,
          reason: "gateway_unreachable",
          features: { virtual_servers: true },
        },
      },
    ],
    [
      "a healthy status",
      { data: { enabled: true, features: { virtual_servers: true } } },
    ],
  ])("is shown during %s", (_state, current) => {
    status.current = current;
    show();
    expect(screen.getByText("Virtual server")).toBeInTheDocument();
    expect(screen.getByText("fixture-app-vs")).toBeInTheDocument();
    expect(
      screen.getByText(/set on the app's virtual server/),
    ).toBeInTheDocument();
  });

  it("says Not available in this deployment when the switch is off", () => {
    status.current = {
      data: { enabled: true, features: { virtual_servers: false } },
    };
    show();
    expect(screen.getByText("Virtual server")).toBeInTheDocument();
    expect(screen.getByText(NOT_AVAILABLE)).toBeInTheDocument();
    expect(screen.queryByText("fixture-app-vs")).toBeNull();
  });
});
