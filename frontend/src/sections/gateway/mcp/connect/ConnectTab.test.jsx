import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import ConnectTab from "./ConnectTab";

const state = vi.hoisted(() => ({
  config: {},
  orgLevel: 8,
  virtualServers: true,
  location: "",
  gateway: { baseUrl: "https://org-gateway.example.test/v1" },
}));
vi.mock("../../providers/hooks/useGatewayConfig", () => ({
  useGatewayConfig: () => ({ data: state.config }),
}));
vi.mock("../../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gateway: state.gateway }),
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: state.orgLevel }),
}));
vi.mock("../components/McpIcon", () => ({ default: () => null }));
function Location() {
  const location = useLocation();
  state.location = `${location.pathname}${location.search}`;
  return null;
}
const show = () =>
  render(
    <MemoryRouter>
      <ThemeProvider theme={createTheme()}>
        <ConnectTab gatewayId="org-gateway" />
        <Location />
      </ThemeProvider>
    </MemoryRouter>,
  );

beforeEach(() => {
  state.orgLevel = 8;
  state.virtualServers = true;
  state.config = {
    mcp: {
      enabled: false,
      virtual_servers: {
        review: { title: "Review" },
        support: { title: "Support" },
      },
    },
  };
});
describe("Connect client setup", () => {
  it("takes an admin with no virtual server to the real creation tab", () => {
    state.config = { mcp: {} };
    show();
    fireEvent.click(screen.getByRole("button", { name: "Create virtual server" }));
    expect(state.location).toBe("/dashboard/gateway/mcp?tab=virtual");
  });

  it.each([3, 1, null])("guides role %s to an admin instead of a dead creation action", (level) => {
    state.orgLevel = level;
    state.config = { mcp: {} };
    show();
    expect(screen.getByText(/Ask an Owner or Admin to create a virtual server/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Create virtual server" })).toBeNull();
  });

  it.each([false, undefined])("does not offer setup actions with virtual servers unconfirmed (%s)", (capability) => {
    state.config = { mcp: {} };
    state.virtualServers = capability;
    show();
    expect(screen.queryByText("Create a virtual server first")).toBeNull();
    expect(screen.queryByRole("button", { name: "Create virtual server" })).toBeNull();
  });
  it("renders organization-specific setup and create-key link while honestly off", () => {
    show();
    expect(screen.getByLabelText("Client setup").value).toContain(
      "https://org-gateway.example.test/mcp/v/review",
    );
    expect(screen.getByText(/MCP is off/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Create key" })).toHaveAttribute(
      "href",
      "/dashboard/gateway/keys?create=1",
    );
    expect(screen.getByLabelText("Client setup").value).toContain(
      "$FUTUREAGI_KEY",
    );
  });
  it.each([3, 1, null, undefined, "8"])(
    "offers existing-key navigation, not creation, at role level %s",
    (level) => {
      state.orgLevel = level;
      show();
      expect(screen.queryByRole("link", { name: "Create key" })).toBeNull();
      expect(
        screen.getByRole("link", { name: "View existing keys" }),
      ).toHaveAttribute("href", "/dashboard/gateway/keys");
      expect(
        screen.getByText(
          /Key creation and revocation require an Owner or Admin/,
        ),
      ).toBeInTheDocument();
    },
  );
  it.each([8, 15])(
    "allows the supported creation path for role %s",
    (level) => {
      state.orgLevel = level;
      show();
      expect(screen.getByRole("link", { name: "Create key" })).toHaveAttribute(
        "href",
        "/dashboard/gateway/keys?create=1",
      );
    },
  );
  it("switches virtual server instead of offering another organization's URL", () => {
    show();
    fireEvent.mouseDown(
      screen.getByRole("combobox", { name: "Virtual server" }),
    );
    fireEvent.click(screen.getByRole("option", { name: "Support" }));
    expect(screen.getByLabelText("Client setup").value).toContain(
      "/mcp/v/support",
    );
    expect(screen.getByLabelText("Client setup").value).not.toContain(
      "/mcp/v/review",
    );
  });
  it("does not invent an endpoint when no virtual server exists", () => {
    state.config = { mcp: {} };
    show();
    expect(
      screen.getByText("Create a virtual server first"),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("Client setup")).toBeNull();
  });
});

// W07 enabled-scenario fixture; the release-switch specs exercise OFF.
vi.mock("../hooks/useMCPReleaseFeatures", () => ({
  useMCPReleaseFeatures: () => ({
    catalog: true,
    credentialVault: true,
    virtualServers: state.virtualServers,
    catalogConnect: true,
  }),
  useMCPReleaseGuard: () => () => {},
}));
