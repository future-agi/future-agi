import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HelmetProvider } from "react-helmet-async";
import { beforeEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen, waitFor, within } from "src/utils/test-utils";

import kiteAdded from "../__fixtures__/catalog-kite-added.json";
import addKite from "../__fixtures__/add-kite-files.json";
import IntegrationsSection from "../IntegrationsSection";

// Kite Files is a catalog app whose provider declares no tools. The add
// lists them (echo, get_weather and the destructive delete_file) and stores
// each one's access on the app's own virtual server; the catalog's server
// card carries that stored access (catalog-kite-added.json), so the drawer
// labels delete_file "Needs approval" right after the add and after a
// reload.
const KITE = "kite-files";
const KEY = "test-drawer-typed-key-0001";
const add = (appId) => `/agentcc/connectors/apps/${appId}/add/`;

const { api, org } = vi.hoisted(() => ({
  api: { catalog: null, post: null },
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
    get: async () => ({ data: api.catalog }),
    post: (...args) => api.post(...args),
    delete: async () => ({ data: {} }),
  },
  endpoints: {
    gateway: {
      connectorCatalog: "/agentcc/connectors/catalog/",
      connectorAdd: (appId) => `/agentcc/connectors/apps/${appId}/add/`,
      mcpCredentials: "/agentcc/mcp/credentials/",
    },
  },
}));
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
vi.mock("notistack", () => ({ enqueueSnackbar: () => {} }));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: org.level }),
}));

const clone = (value) => JSON.parse(JSON.stringify(value));

// The catalog before the add: the same app, with no server.
function notAdded() {
  const catalog = clone(kiteAdded);
  const app = catalog.results.find((entry) => entry.id === KITE);
  app.connected = false;
  app.servers = [];
  return catalog;
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
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

function accessControl(drawer) {
  return within(drawer).getByRole("region", { name: "Access control" });
}

function rowText(region, label) {
  return within(region).getByText(label).parentElement.textContent;
}

// The drawer's tool rows, by tool name, in the order shown.
function toolRows(region) {
  return Object.fromEntries(
    within(region)
      .queryAllByRole("listitem")
      .map((item) => [item.querySelector("p, span")?.textContent, item]),
  );
}

function expectStoredAccess(drawer) {
  const region = accessControl(drawer);
  const rows = toolRows(region);
  expect(Object.keys(rows)).toEqual(["delete_file", "echo", "get_weather"]);
  expect(rows.delete_file.textContent).toContain("Needs approval");
  expect(rows.delete_file.textContent).not.toContain("Blocked");
  expect(rows.echo.textContent).toContain("Allowed");
  expect(rows.get_weather.textContent).toContain("Allowed");
  expect(rowText(region, "Tools")).toContain("3 of 3 tools allowed");
  expect(region.textContent).not.toContain("does not declare its tools");
  // The grid's count agrees with the drawer's.
  expect(
    screen.getByTestId(`row-${KITE}`).querySelector('[data-col="tools"]')
      .textContent,
  ).toBe("3 of 3");
}

beforeEach(() => {
  org.level = 8;
  api.catalog = notAdded();
  api.post = vi.fn(async (url) => {
    if (url === add(KITE)) {
      api.catalog = clone(kiteAdded);
      return { data: clone(addKite) };
    }
    throw new Error(`unexpected POST ${url}`);
  });
});

describe("an added app whose provider declares no tools", () => {
  it("shows Needs approval for its write tool right after the add", async () => {
    renderPage();
    const drawer = await openApp(KITE);
    // Before the add the card has no tools at all, so the drawer says the
    // provider does not declare them.
    expect(rowText(accessControl(drawer), "Tools")).toContain(
      "This provider does not declare its tools in the catalog.",
    );

    await userEvent.type(within(drawer).getByLabelText("API key"), KEY);
    await userEvent.click(
      within(drawer).getByRole("button", { name: "Connect" }),
    );
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
    expect(api.post.mock.calls[0][0]).toBe(add(KITE));
    expect(await screen.findByText("Kite Files is connected.")).toBeTruthy();

    await waitFor(() =>
      expect(
        Object.keys(toolRows(accessControl(screen.getByRole("dialog")))),
      ).toHaveLength(3),
    );
    expectStoredAccess(screen.getByRole("dialog"));
  });

  it("shows Needs approval for its write tool after a reload", async () => {
    api.catalog = clone(kiteAdded);
    renderPage();
    const drawer = await openApp(KITE);
    expect(screen.getByText("Kite Files is connected.")).toBeTruthy();
    expectStoredAccess(drawer);
    expect(api.post).not.toHaveBeenCalled();
  });
});
