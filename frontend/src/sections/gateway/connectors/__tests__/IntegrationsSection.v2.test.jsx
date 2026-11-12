import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HelmetProvider } from "react-helmet-async";
import { beforeEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen, waitFor, within } from "src/utils/test-utils";

import noneAdded from "../__fixtures__/catalog-none-added.json";
import mixed from "../__fixtures__/catalog-acme-connected.json";
import IntegrationsSection from "../IntegrationsSection";

// The Integrations page on the AI Gateway console's own frame (console v2):
// the page frame, filter bar, grid, detail drawer and status chips every
// gateway page is built from, and the console's rule for Members: a write
// control shows disabled and says "Owner or Admin only".
const { api, org } = vi.hoisted(() => ({
  api: { catalog: null },
  org: { level: 8 },
}));

vi.mock("src/utils/axios", () => ({
  default: {
    get: async () => ({ data: api.catalog }),
    post: vi.fn(),
    delete: vi.fn(),
  },
  endpoints: {
    gateway: {
      connectorCatalog: "/agentcc/connectors/catalog/",
      connectorAdd: (appId) => `/agentcc/connectors/apps/${appId}/add/`,
      mcpCredentials: "/agentcc/mcp/credentials/",
    },
  },
}));
// The page reads the gateway's MCP status: MCP is on here
// (IntegrationsSection.mcp-off.test.jsx is the page with MCP off).
vi.mock("src/sections/gateway/context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "gw-1" }),
}));
vi.mock("src/sections/gateway/mcp/hooks/useMCPConfig", () => ({
  useMCPStatus: () => ({
    data: {
      enabled: true,
      features: {
        catalog: true,
        credential_vault: true,
        virtual_servers: true,
        upstream_oauth: true,
        client_oauth: true,
        k2_apps: true,
      },
    },
    isPending: false,
    isError: false,
  }),
}));
vi.mock("ag-grid-react", () => import("./ag-grid-mock"));
vi.mock("src/hooks/use-ag-theme", () => ({ useAgThemeWith: () => ({}) }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: org.level }),
}));

const clone = (value) => JSON.parse(JSON.stringify(value));

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

async function openApp(appId) {
  await userEvent.click(await screen.findByTestId(`row-${appId}`));
  return screen.findByRole("dialog");
}

beforeEach(() => {
  org.level = 8;
  api.catalog = clone(noneAdded);
});

describe("the Integrations page frame", () => {
  it("is a gateway page: its title, the browser tab's title and the room above the floating button", async () => {
    renderPage();
    const page = screen.getByTestId("integrations-page");
    expect(
      within(page).getByRole("heading", { level: 1, name: "Integrations" }),
    ).toBeTruthy();
    expect(
      page.querySelector('[data-clearance="floating-button"]'),
    ).not.toBeNull();
    await waitFor(() => expect(document.title).toBe("Integrations"));
  });

  it("offers Custom MCP at the native button size, on the MCP page's Servers tab", async () => {
    renderPage();
    const link = screen.getByRole("link", { name: "Custom MCP server" });
    expect(link.getAttribute("href")).toBe(
      "/dashboard/gateway/mcp?tab=servers",
    );
    expect(link.className).not.toMatch(/MuiButton-sizeSmall/);
  });

  it("lists the apps in the gateway grid", async () => {
    renderPage();
    await screen.findByTestId("row-acme-docs");
    const grid = screen.getByTestId("integrations-grid");
    expect(within(grid).getByTestId("gateway-grid")).toBeTruthy();
  });

  it("searches from the gateway filter bar", async () => {
    renderPage();
    await screen.findByTestId("row-acme-docs");
    const bar = screen.getByRole("search");
    expect(
      within(bar).getByRole("textbox", { name: "Search apps" }),
    ).toBeTruthy();
    expect(
      within(bar).getByRole("button", { name: /^Connected/ }),
    ).toBeTruthy();
  });
});

describe("an app's drawer", () => {
  it("is the gateway detail drawer: a close button, the vendor under the name, and the app's facts in rows", async () => {
    renderPage();
    const drawer = await openApp("bolt-crm");
    const close = within(drawer).getByRole("button", { name: "Close" });
    expect(close.className).toMatch(/MuiIconButton-root/);
    expect(
      within(drawer).getByRole("heading", { level: 2, name: "Bolt CRM" }),
    ).toBeTruthy();
    expect(within(drawer).getByText("Bolt Labs")).toBeTruthy();
    expect(
      within(drawer).getByRole("heading", { level: 3, name: "Details" }),
    ).toBeTruthy();
    expect(within(drawer).getByText("Catalog entry")).toBeTruthy();
    await userEvent.click(close);
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("puts Disconnect in the drawer's header for an added app", async () => {
    api.catalog = clone(mixed);
    renderPage();
    const drawer = await openApp("acme-docs");
    const disconnect = within(drawer).getByRole("button", {
      name: "Disconnect",
    });
    expect(disconnect).toBeEnabled();
    // The drawer's header: the title's row, with the close button.
    const header = within(drawer).getByRole("heading", {
      level: 2,
      name: "Acme Docs",
    }).parentElement.parentElement;
    expect(header.contains(disconnect)).toBe(true);
    expect(
      header.contains(within(drawer).getByRole("button", { name: "Close" })),
    ).toBe(true);
  });
});

describe("a Member, on the console's Owner or Admin rule", () => {
  it("sees Connect disabled with Owner or Admin only, and no key field", async () => {
    org.level = 3;
    renderPage();
    const drawer = await openApp("acme-docs");
    const connect = within(drawer).getByRole("button", { name: "Connect" });
    expect(connect).toBeDisabled();
    expect(
      within(drawer).getByLabelText("Owner or Admin only").contains(connect),
    ).toBe(true);
    expect(within(drawer).queryByLabelText("API key")).toBeNull();
  });

  it("sees Disconnect disabled with Owner or Admin only on an added app", async () => {
    org.level = 1;
    api.catalog = clone(mixed);
    renderPage();
    const drawer = await openApp("acme-docs");
    const disconnect = within(drawer).getByRole("button", {
      name: "Disconnect",
    });
    expect(disconnect).toBeDisabled();
    expect(
      within(drawer)
        .getAllByLabelText("Owner or Admin only")
        .some((wrapper) => wrapper.contains(disconnect)),
    ).toBe(true);
  });
});

describe("what the catalog shows on this build", () => {
  // Only providers an org can add here: one shared API key or token, or no
  // key. A sign-in provider is left out of its app, and an app with nothing
  // else is not listed, so no card says it needs a sign-in.
  it("drops an app's sign-in provider and offers only its key", async () => {
    const catalog = clone(noneAdded);
    const fern = catalog.results.find((app) => app.id === "fern-files");
    fern.providers[1].credential = {
      mode: "per_user",
      kind: "oauth2",
      label: "Sign in with Fern",
    };
    const echo = catalog.results.find((app) => app.id === "echo-chat");
    echo.providers[0].credential = { mode: "per_user", kind: "mcp_oauth" };
    api.catalog = catalog;
    renderPage();
    await screen.findByTestId("row-fern-files");
    expect(screen.queryByTestId("row-echo-chat")).toBeNull();
    const drawer = await openApp("fern-files");
    expect(within(drawer).queryByRole("radiogroup")).toBeNull();
    expect(within(drawer).getByLabelText("API key")).toBeTruthy();
    const shown = `${screen.getByTestId("integrations-page").textContent} ${drawer.textContent}`;
    expect(shown).not.toMatch(/oauth|sign.in|coming soon|preview/i);
  });
});
