import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HelmetProvider } from "react-helmet-async";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "src/utils/test-utils";

import noneAdded from "../__fixtures__/catalog-none-added.json";
import IntegrationsSection from "../IntegrationsSection";

// At 1024 to 1440 the Integrations grid is narrower than its labels: the App
// cell's second line, the Auth type and the Category end in an ellipsis.
// jsdom does not lay out, so these pin what keeps cut-off text readable: a
// title with the full text on each line of the App cell, and a tooltip with
// the cell's full text on the Auth and Category columns. Same harness as
// IntegrationsSection.grid-ux.test.jsx.
const { api, org, mcp, grid } = vi.hoisted(() => ({
  api: { catalog: null, get: null },
  org: { level: 8 },
  mcp: { features: null },
  grid: { props: null },
}));

vi.mock("src/utils/axios", () => ({
  default: {
    get: (...args) => api.get(...args),
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
vi.mock("src/sections/gateway/context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "gw-1" }),
}));
vi.mock("src/sections/gateway/mcp/hooks/useMCPConfig", () => ({
  useMCPStatus: () => ({
    data: { enabled: true, features: mcp.features },
    isPending: false,
    isError: false,
  }),
}));
// The table stand-in (ag-grid-mock.jsx), keeping the props the page last
// passed, so a test can read what the stand-in does not draw (tooltips).
vi.mock("ag-grid-react", async () => {
  const { AgGridReact: Mock } = await import("./ag-grid-mock");
  return {
    AgGridReact: function CapturingGrid(props) {
      grid.props = props;
      return <Mock {...props} />;
    },
  };
});
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

async function rowsLoaded() {
  await screen.findAllByTestId(/^row-/);
}

const row = (id) => screen.getByTestId(`row-${id}`);
const appCell = (id) => row(id).querySelector('[data-col="app"]');
const column = (colId) =>
  grid.props.columnDefs.find((col) => col.colId === colId);
const rowData = (id) => grid.props.rowData.find((data) => data.id === id);

beforeEach(() => {
  org.level = 8;
  mcp.features = {
    catalog: true,
    credential_vault: true,
    virtual_servers: true,
    upstream_oauth: true,
    client_oauth: true,
    k2_apps: true,
  };
  grid.props = null;
  window.localStorage.clear();
  api.catalog = clone(noneAdded);
  api.get = vi.fn(async (url, { params } = {}) => {
    expect(url).toBe("/agentcc/connectors/catalog/");
    expect(params).toEqual({ limit: 200, page: 1 });
    return { data: api.catalog };
  });
});

describe("the apps grid stays readable at 1024", () => {
  it("titles the app name and its vendor or description with their full text", async () => {
    renderPage();
    await rowsLoaded();
    // A vendor other than the app's name is the second line.
    const name = within(appCell("bolt-crm")).getByText("Bolt CRM");
    expect(name.getAttribute("title")).toBe("Bolt CRM");
    const vendor = within(appCell("bolt-crm")).getByText("Bolt Labs");
    expect(vendor.getAttribute("title")).toBe("Bolt Labs");
    // Where the vendor is the app itself, the description is.
    const { description } = noneAdded.results.find(
      (app) => app.id === "acme-docs",
    );
    const line = within(appCell("acme-docs")).getByText(description);
    expect(line.getAttribute("title")).toBe(description);
  });

  it("gives the Auth column a tooltip with the cell's full auth type", async () => {
    renderPage();
    await rowsLoaded();
    const auth = column("auth");
    const data = rowData("iris-code");
    const value = auth.valueGetter({ data });
    expect(value).toBe("Personal access token");
    expect(auth.tooltipValueGetter?.({ value, data })).toBe(value);
  });

  it("gives the Category column a tooltip with the cell's full category", async () => {
    renderPage();
    await rowsLoaded();
    const category = column("category");
    const data = rowData("echo-chat");
    const value = category.valueGetter({ data });
    expect(value).toBe("Communication");
    expect(category.tooltipValueGetter?.({ value, data })).toBe(value);
  });
});
