import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HelmetProvider } from "react-helmet-async";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";

import noneAdded from "../__fixtures__/catalog-none-added.json";
import IntegrationsSection from "../IntegrationsSection";

// The Integrations page on a deployment that runs with MCP off (review fix
// round 1). Every app connects through the gateway's MCP, and with MCP off
// the gateway has no MCP route, so an add could only fail: the page shows
// the MCP page's off state instead of apps to connect, with no grid, no
// Connect and no Custom MCP action. The status is read through the MCP
// page's own hook, from Django's mcp-status answer.
const CATALOG = "/agentcc/connectors/catalog/";
const STATUS = "/agentcc/gateways/gw-1/mcp-status/";

const { api, org } = vi.hoisted(() => ({
  api: { catalog: null, status: null, gets: [] },
  org: { level: 8 },
}));

// MCP is on for the organization here, so connecting never turns it on
// (IntegrationsSection.mcp-turn-on.test.jsx covers MCP off).
vi.mock(
  "src/sections/gateway/providers/hooks/useGatewayConfig",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useGatewayConfig: () => ({
      data: { mcp: { enabled: true } },
      isError: false,
      isPending: false,
    }),
  }),
);
vi.mock("src/utils/axios", () => ({
  default: {
    get: async (url) => {
      api.gets.push(url);
      if (url === STATUS) {
        if (api.status instanceof Error) throw api.status;
        return { data: { status: true, result: api.status } };
      }
      return { data: api.catalog };
    },
    post: vi.fn(),
    delete: vi.fn(),
  },
  endpoints: {
    gateway: {
      connectorCatalog: "/agentcc/connectors/catalog/",
      connectorAdd: (appId) => `/agentcc/connectors/apps/${appId}/add/`,
      mcpCredentials: "/agentcc/mcp/credentials/",
      mcpStatus: (id) => `/agentcc/gateways/${id}/mcp-status/`,
    },
  },
}));
vi.mock("src/sections/gateway/context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "gw-1" }),
}));
vi.mock("ag-grid-react", () => import("./ag-grid-mock"));
vi.mock("src/hooks/use-ag-theme", () => ({ useAgThemeWith: () => ({}) }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: org.level }),
}));

const clone = (value) => JSON.parse(JSON.stringify(value));

// Django's mcp-status for a gateway that runs with MCP off (mcp_admin
// org_status: disabled, reason gateway_mcp_disabled).
const MCP_OFF = {
  enabled: false,
  sessions: 0,
  tools: 0,
  resources: 0,
  prompts: 0,
  servers: [],
  reason: "gateway_mcp_disabled",
};

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <HelmetProvider>
      <QueryClientProvider client={client}>
        <IntegrationsSection />
      </QueryClientProvider>
    </HelmetProvider>,
  );
}

beforeEach(() => {
  org.level = 8;
  api.catalog = clone(noneAdded);
  api.status = {
    ...MCP_OFF,
    enabled: true,
    reason: undefined,
    features: { catalog: true, credential_vault: true, virtual_servers: true },
  };
  api.gets = [];
});

describe("the Integrations page with MCP off for the deployment", () => {
  it("shows the off state, with no app to connect and no Custom MCP action", async () => {
    api.status = clone(MCP_OFF);
    renderPage();
    expect(
      await screen.findByText("MCP is turned off for this deployment"),
    ).toBeTruthy();
    expect(
      screen.getByText(
        "For a self-hosted gateway, ask your operator to configure AGENTCC_MCP_STATE_KEYS, set mcp.enabled: true and restart the gateway.",
      ),
    ).toBeTruthy();
    expect(
      screen.getByRole("link", { name: "Self-hosted MCP setup guide" }),
    ).toHaveAttribute(
      "href",
      "https://github.com/future-agi/future-agi/blob/55cb111a056ddf520e89ffb5a576ce1a7ff449ce/docs/gateway/mcp.md#run-it-on-your-own-gateway",
    );
    expect(screen.queryByRole("button", { name: "Turn on MCP" })).toBeNull();
    expect(
      (await import("src/utils/axios")).default.post,
    ).not.toHaveBeenCalled();
    const page = screen.getByTestId("integrations-page");
    expect(page).toBeTruthy();
    expect(screen.queryAllByTestId(/^row-/)).toHaveLength(0);
    expect(screen.queryByTestId("integrations-grid")).toBeNull();
    expect(screen.queryByRole("button", { name: /connect/i })).toBeNull();
    expect(
      screen.queryByRole("link", { name: "Custom MCP server" }),
    ).toBeNull();
    expect(screen.queryByRole("textbox", { name: "Search apps" })).toBeNull();
    expect(screen.queryByText("No apps to connect yet")).toBeNull();
    expect(api.gets).toContain(STATUS);
  });

  // Django adds the deployment's switches to the runtime-off status too: the
  // catalog switch on must still give the MCP-off state, not the line.
  it("shows the off state when MCP is off at runtime with the catalog switch on", async () => {
    api.status = {
      ...clone(MCP_OFF),
      features: {
        catalog: true,
        credential_vault: true,
        virtual_servers: true,
      },
    };
    renderPage();
    expect(
      await screen.findByText("MCP is turned off for this deployment"),
    ).toBeTruthy();
    expect(
      screen.getByText(
        "For a self-hosted gateway, ask your operator to configure AGENTCC_MCP_STATE_KEYS, set mcp.enabled: true and restart the gateway.",
      ),
    ).toBeTruthy();
    expect(
      screen.getByRole("link", { name: "Self-hosted MCP setup guide" }),
    ).toHaveAttribute(
      "href",
      "https://github.com/future-agi/future-agi/blob/55cb111a056ddf520e89ffb5a576ce1a7ff449ce/docs/gateway/mcp.md#run-it-on-your-own-gateway",
    );
    expect(screen.queryByRole("button", { name: "Turn on MCP" })).toBeNull();
    expect(
      (await import("src/utils/axios")).default.post,
    ).not.toHaveBeenCalled();
    expect(screen.queryByText("Not available in this deployment")).toBeNull();
    expect(screen.queryByTestId("integrations-grid")).toBeNull();
    expect(screen.queryByRole("textbox", { name: "Search apps" })).toBeNull();
  });

  it("lists the apps when the gateway serves MCP", async () => {
    renderPage();
    expect(await screen.findByTestId("row-acme-docs")).toBeTruthy();
    expect(
      screen.queryByText("MCP is turned off for this deployment"),
    ).toBeNull();
    expect(
      screen.getByRole("link", { name: "Custom MCP server" }),
    ).toBeTruthy();
    expect(api.gets).toEqual(expect.arrayContaining([STATUS, CATALOG]));
    expect(
      screen.queryByRole("link", { name: "Self-hosted MCP setup guide" }),
    ).toBeNull();
    expect(screen.queryByText(/ask your operator to configure/)).toBeNull();
  });

  // A status read error is an outage, not MCP off: the catalog is still
  // read and its apps listed, with Custom MCP beside them.
  it("status failure still lists the catalog apps and keeps Custom MCP", async () => {
    api.status = new Error("network down");
    renderPage();
    expect(await screen.findByTestId("row-acme-docs")).toBeTruthy();
    expect(api.gets).toEqual(expect.arrayContaining([STATUS, CATALOG]));
    expect(
      screen.queryByRole("link", { name: "Self-hosted MCP setup guide" }),
    ).toBeNull();
    expect(screen.queryByText(/ask your operator to configure/)).toBeNull();
    expect(
      screen.getByRole("link", { name: "Custom MCP server" }),
    ).toBeTruthy();
    expect(
      screen.queryByText("MCP is turned off for this deployment"),
    ).toBeNull();
  });

  it("shows no empty state and no off state while the status is still unknown", async () => {
    /** @type {() => void} */
    let release = () => {
      throw new Error("status barrier not initialized");
    };
    api.status = clone(MCP_OFF);
    const pending = new Promise((resolve) => {
      release = () => resolve(undefined);
    });
    const { default: axios } = await import("src/utils/axios");
    const get = axios.get;
    axios.get = async (url) => {
      if (url === STATUS) await pending;
      return get(url);
    };
    try {
      renderPage();
      // The catalog read may start while the status is pending; neither the
      // empty state nor the MCP-off text shows before the status resolves.
      expect(await screen.findByTestId("integrations-page")).toBeTruthy();
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(screen.queryByText("No apps to connect yet")).toBeNull();
      expect(
        screen.queryByRole("link", { name: "Self-hosted MCP setup guide" }),
      ).toBeNull();
      expect(screen.queryByText(/ask your operator to configure/)).toBeNull();
      expect(
        screen.queryByText("MCP is turned off for this deployment"),
      ).toBeNull();
      release();
      expect(
        await screen.findByText("MCP is turned off for this deployment"),
      ).toBeTruthy();
      expect(
        screen.getByText(
          "For a self-hosted gateway, ask your operator to configure AGENTCC_MCP_STATE_KEYS, set mcp.enabled: true and restart the gateway.",
        ),
      ).toBeTruthy();
      expect(
        screen.getByRole("link", { name: "Self-hosted MCP setup guide" }),
      ).toHaveAttribute(
        "href",
        "https://github.com/future-agi/future-agi/blob/55cb111a056ddf520e89ffb5a576ce1a7ff449ce/docs/gateway/mcp.md#run-it-on-your-own-gateway",
      );
      expect(screen.queryByRole("button", { name: "Turn on MCP" })).toBeNull();
      expect(
        (await import("src/utils/axios")).default.post,
      ).not.toHaveBeenCalled();
    } finally {
      axios.get = get;
    }
  });
});
