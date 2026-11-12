import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HelmetProvider } from "react-helmet-async";
import { beforeEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen, within } from "src/utils/test-utils";

import noneAdded from "../__fixtures__/catalog-none-added.json";
import mixed from "../__fixtures__/catalog-acme-connected.json";
import { APP_LOGOS } from "../app-logos.generated";
import IntegrationsSection from "../IntegrationsSection";

// The Integrations grid: each app's logo, a Connect action on every app an
// Owner or Admin can add (Not added for everyone else), connected apps first,
// the columns App, Category, Auth, Tier, Tools allowed and Status (a muted
// dash, named No tools listed, where an app lists no tools), an Auth filter
// that names itself, an access-control block only where there is something
// to control, and a no-match state that points to Custom MCP. Same harness
// as IntegrationsSection.browse.test.jsx.
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
// passed, so a test can read what the stand-in does not draw (the sort).
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
const V1_KEY = "gateway-grid:gateway-integrations-grid";
const V2_KEY = "gateway-grid:gateway-integrations-grid-v2";

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

const rowIds = () =>
  screen
    .queryAllByTestId(/^row-/)
    .map((row) => row.getAttribute("data-testid").replace("row-", ""));

async function rowsLoaded() {
  await screen.findAllByTestId(/^row-/);
}

const row = (id) => screen.getByTestId(`row-${id}`);
const headers = () =>
  screen.getAllByRole("columnheader").map((th) => th.textContent);
const rowConnect = (element) =>
  within(element).queryByRole("button", { name: /^Connect\b/ });
const toolsCell = (id) => row(id).querySelector('[data-col="tools"]');
const ALL_HEADERS = [
  "App",
  "Category",
  "Auth",
  "Tier",
  "Tools allowed",
  "Status",
];

// The rows in the order AG Grid first shows them: each column's initial
// sort, with its comparator, as the grid applies it.
function gridOrder() {
  const { columnDefs, rowData } = grid.props;
  const sorted = columnDefs.filter((column) => column.initialSort);
  const value = (column, data) =>
    column.valueGetter ? column.valueGetter({ data }) : data[column.field];
  return [...rowData]
    .sort((a, b) => {
      for (const column of sorted) {
        const va = value(column, a);
        const vb = value(column, b);
        let order = column.comparator
          ? column.comparator(va, vb)
          : va > vb
            ? 1
            : va < vb
              ? -1
              : 0;
        if (column.initialSort === "desc") order = -order;
        if (order) return order;
      }
      return 0;
    })
    .map((data) => data.id);
}

const heldApp = (name) => ({
  ...clone(noneAdded.results[0]),
  id: `microsoft-${name.toLowerCase()}`,
  name: `Microsoft ${name}`,
  group: "microsoft",
  status: "coming_soon",
  default_provider: null,
  providers: [],
  servers: [],
  connected: false,
});

const withoutTools = (catalog) => {
  for (const app of catalog.results) {
    for (const provider of app.providers) provider.tools = [];
  }
  return catalog;
};

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

describe("logos", () => {
  it("shows an app's bundled logo in its row and its drawer", async () => {
    const app = api.catalog.results[0];
    app.id = "airtable";
    app.name = "Airtable";
    renderPage();
    await rowsLoaded();
    const logo = row("airtable").querySelector('[data-col="app"] img');
    expect(logo.getAttribute("src")).toBe(APP_LOGOS.airtable.light);
    expect(within(row("airtable")).queryByText("AI")).toBeNull();
    await userEvent.click(row("airtable"));
    const drawer = await screen.findByRole("dialog");
    const heading = within(drawer).getByRole("heading", {
      level: 2,
      name: "Airtable",
    });
    expect(heading.querySelector("img").getAttribute("src")).toBe(
      APP_LOGOS.airtable.light,
    );
  });

  it("keeps initials for an app with no bundled logo", async () => {
    renderPage();
    await rowsLoaded();
    expect(row("acme-docs").querySelector("img")).toBeNull();
    expect(within(row("acme-docs")).getByText("AD")).toBeTruthy();
  });
});

describe("Connect on the row", () => {
  it("gives an Owner or Admin a Connect button on every app not added yet", async () => {
    renderPage();
    await rowsLoaded();
    for (const app of noneAdded.results) {
      const button = within(row(app.id)).getByRole("button", {
        name: `Connect ${app.name}`,
      });
      expect(button.textContent).toBe("Connect");
    }
  });

  it("opens the app's drawer once from its Connect button", async () => {
    renderPage();
    await rowsLoaded();
    await userEvent.click(
      within(row("acme-docs")).getByRole("button", {
        name: "Connect Acme Docs",
      }),
    );
    const drawer = await screen.findByRole("dialog");
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
    expect(
      within(drawer).getByRole("heading", { level: 2, name: "Acme Docs" }),
    ).toBeTruthy();
    expect(
      within(drawer).getByRole("button", { name: "Connect" }),
    ).toBeTruthy();
  });

  it("shows Connected and Needs key, with no Connect, on added apps", async () => {
    api.catalog = clone(mixed);
    renderPage();
    await rowsLoaded();
    expect(within(row("acme-docs")).getByText("Connected")).toBeTruthy();
    expect(rowConnect(row("acme-docs"))).toBeNull();
    expect(within(row("iris-code")).getByText("Needs key")).toBeTruthy();
    expect(rowConnect(row("iris-code"))).toBeNull();
    expect(
      within(row("bolt-crm")).getByRole("button", { name: "Connect Bolt CRM" }),
    ).toBeTruthy();
  });

  it("never offers Connect on a coming-soon app", async () => {
    api.catalog.results.push(heldApp("Excel"));
    renderPage();
    await rowsLoaded();
    const held = row("microsoft-excel");
    expect(within(held).getByText("Coming soon")).toBeTruthy();
    expect(within(held).queryByRole("button")).toBeNull();
  });

  it("gives a Member no Connect button; a row click still opens the app", async () => {
    org.level = 3;
    renderPage();
    await rowsLoaded();
    for (const id of rowIds()) expect(rowConnect(row(id))).toBeNull();
    await userEvent.click(row("acme-docs"));
    const drawer = await screen.findByRole("dialog");
    expect(
      within(drawer).getByRole("heading", { level: 2, name: "Acme Docs" }),
    ).toBeTruthy();
  });

  it("gives no Connect button where this deployment cannot connect apps", async () => {
    mcp.features = { ...mcp.features, virtual_servers: false };
    renderPage();
    await rowsLoaded();
    for (const id of rowIds()) expect(rowConnect(row(id))).toBeNull();
  });

  it("keeps the row's Connect quiet: a small text button, the drawer's Connect the contained one", async () => {
    renderPage();
    await rowsLoaded();
    const button = within(row("acme-docs")).getByRole("button", {
      name: "Connect Acme Docs",
    });
    expect(button.textContent).toBe("Connect");
    expect(button.classList.contains("MuiButton-text")).toBe(true);
    expect(button.classList.contains("MuiButton-textPrimary")).toBe(true);
    expect(button.classList.contains("MuiButton-sizeSmall")).toBe(true);
    expect(button.classList.contains("MuiButton-outlined")).toBe(false);
    expect(button.classList.contains("MuiButton-contained")).toBe(false);
    await userEvent.click(button);
    const drawer = await screen.findByRole("dialog");
    const primary = within(drawer).getByRole("button", { name: "Connect" });
    expect(primary.classList.contains("MuiButton-contained")).toBe(true);
  });

  it("tells a Member an app is Not added, with no Connect", async () => {
    org.level = 3;
    renderPage();
    await rowsLoaded();
    for (const app of noneAdded.results) {
      const status = row(app.id).querySelector('[data-col="status"]');
      expect(within(status).getByText("Not added")).toBeTruthy();
      expect(rowConnect(row(app.id))).toBeNull();
    }
  });

  it("tells everyone an app is Not added where this deployment cannot connect apps", async () => {
    mcp.features = { ...mcp.features, virtual_servers: false };
    renderPage();
    await rowsLoaded();
    for (const app of noneAdded.results) {
      const status = row(app.id).querySelector('[data-col="status"]');
      expect(within(status).getByText("Not added")).toBeTruthy();
      expect(rowConnect(row(app.id))).toBeNull();
    }
  });

  it("says Not added only on apps not added yet, and sorts them where Connect sat", async () => {
    org.level = 3;
    api.catalog = clone(mixed);
    api.catalog.results.unshift(heldApp("Teams"));
    renderPage();
    await rowsLoaded();
    expect(within(row("acme-docs")).getByText("Connected")).toBeTruthy();
    expect(within(row("acme-docs")).queryByText("Not added")).toBeNull();
    expect(within(row("iris-code")).getByText("Needs key")).toBeTruthy();
    expect(within(row("iris-code")).queryByText("Not added")).toBeNull();
    const held = row("microsoft-teams");
    expect(within(held).getByText("Coming soon")).toBeTruthy();
    expect(within(held).queryByText("Not added")).toBeNull();
    expect(within(row("bolt-crm")).getByText("Not added")).toBeTruthy();
    // Still the available status underneath: it sorts as Connect did.
    const status = grid.props.columnDefs.find((c) => c.colId === "status");
    const bolt = grid.props.rowData.find((data) => data.id === "bolt-crm");
    expect(status.valueGetter({ data: bolt })).toBe("available");
    const order = gridOrder();
    expect(order.slice(0, 2)).toEqual(["acme-docs", "iris-code"]);
    expect(order[order.length - 1]).toBe("microsoft-teams");
  });

  it("shows an Owner or Admin Connect, not Not added, on an app not added yet", async () => {
    renderPage();
    await rowsLoaded();
    for (const app of noneAdded.results) {
      expect(within(row(app.id)).queryByText("Not added")).toBeNull();
    }
  });

  it("lists connected apps first and coming-soon apps last", async () => {
    api.catalog = clone(mixed);
    api.catalog.results.unshift(heldApp("Teams"));
    renderPage();
    await rowsLoaded();
    const status = grid.props.columnDefs.find((c) => c.colId === "status");
    expect(status.initialSort).toBe("asc");
    const order = gridOrder();
    expect(order.slice(0, 2)).toEqual(["acme-docs", "iris-code"]);
    expect(order[order.length - 1]).toBe("microsoft-teams");
  });
});

describe("columns", () => {
  it("keeps the Tier column, Listed included, with no badge by the name", async () => {
    api.catalog.results.find(
      (app) => app.id === "iris-code",
    ).providers[0].tier = "optimized";
    renderPage();
    await rowsLoaded();
    expect(headers()).toContain("Tier");
    const tier = (id) => row(id).querySelector('[data-col="tier"]');
    expect(within(tier("acme-docs")).getByText("Listed")).toBeTruthy();
    expect(within(tier("cobalt-search")).getByText("Verified")).toBeTruthy();
    expect(within(tier("iris-code")).getByText("Optimized")).toBeTruthy();
    // The Tier column says it once: no second badge in the App cell.
    for (const id of rowIds()) {
      expect(
        row(id).querySelector('[data-col="app"] .MuiChip-root'),
      ).toBeNull();
    }
  });

  it("keeps every column, Tools allowed included, when no app reports tools", async () => {
    api.catalog = withoutTools(clone(noneAdded));
    renderPage();
    await rowsLoaded();
    expect(headers()).toEqual(ALL_HEADERS);
    for (const id of rowIds()) expect(toolsCell(id)).not.toBeNull();
  });

  it("shows a muted dash named No tools listed for an app that lists no tools", async () => {
    api.catalog = withoutTools(clone(noneAdded));
    api.catalog.results.find(
      (app) => app.id === "iris-code",
    ).providers[0].tools = [{ name: "read_repo", gateway_posture: "allow" }];
    renderPage();
    await rowsLoaded();
    const empty = within(toolsCell("acme-docs")).getByRole("img", {
      name: "No tools listed",
    });
    expect(empty.textContent).toBe("—");
    expect(toolsCell("acme-docs").textContent).toBe("—");
    expect(
      within(toolsCell("iris-code")).queryByRole("img", {
        name: "No tools listed",
      }),
    ).toBeNull();
    expect(toolsCell("iris-code").textContent).toBe("1 of 1");
  });

  it("shows Tools allowed when an app reports tools", async () => {
    api.catalog = withoutTools(clone(noneAdded));
    api.catalog.results.find(
      (app) => app.id === "iris-code",
    ).providers[0].tools = [
      { name: "read_repo", gateway_posture: "allow" },
      { name: "list_issues", gateway_posture: "allow" },
      {
        name: "delete_repo",
        gateway_posture: "deny",
        reason: "destructive_tool",
      },
    ];
    renderPage();
    await rowsLoaded();
    expect(headers()).toEqual(ALL_HEADERS);
    expect(
      row("iris-code").querySelector('[data-col="tools"]').textContent,
    ).toBe("2 of 3");
    expect(
      row("acme-docs").querySelector('[data-col="tools"]').textContent,
    ).toBe("—");
  });

  it("carries a layout saved before this change over, Tier and Tools allowed included, with no saved sort", async () => {
    // As AG Grid saves it: every column carries sort and sortIndex.
    window.localStorage.setItem(
      V1_KEY,
      JSON.stringify([
        { colId: "app", width: 320, sort: null, sortIndex: null },
        { colId: "tier", hide: false, sort: null, sortIndex: null },
        { colId: "tools", hide: false, sort: null, sortIndex: null },
        { colId: "category", hide: true, sort: "asc", sortIndex: 1 },
        { colId: "status", sort: "desc", sortIndex: 0 },
      ]),
    );
    renderPage();
    await rowsLoaded();
    expect(window.localStorage.getItem(V1_KEY)).toBeNull();
    const carried = JSON.parse(window.localStorage.getItem(V2_KEY));
    expect(carried).toEqual([
      { colId: "app", width: 320 },
      { colId: "tier", hide: false },
      { colId: "tools", hide: false },
      { colId: "category", hide: true },
      { colId: "status" },
    ]);
    for (const column of carried) {
      expect(column).not.toHaveProperty("sort");
      expect(column).not.toHaveProperty("sortIndex");
    }
    // So the default applies: connected apps first.
    const status = grid.props.columnDefs.find((c) => c.colId === "status");
    expect(status.initialSort).toBe("asc");
  });

  it("keeps Tools allowed while a search narrows to apps that report none", async () => {
    api.catalog = withoutTools(clone(noneAdded));
    api.catalog.results.find(
      (app) => app.id === "iris-code",
    ).providers[0].tools = [{ name: "read_repo", gateway_posture: "allow" }];
    renderPage();
    await rowsLoaded();
    expect(headers()).toContain("Tools allowed");
    await userEvent.type(
      screen.getByRole("textbox", { name: "Search apps" }),
      "wiki",
    );
    expect(rowIds()).toEqual(["acme-docs"]);
    expect(headers()).toEqual(ALL_HEADERS);
    expect(
      row("acme-docs").querySelector('[data-col="tools"]').textContent,
    ).toBe("—");
    await userEvent.clear(screen.getByRole("textbox", { name: "Search apps" }));
    expect(rowIds()).toHaveLength(noneAdded.results.length);
    expect(headers()).toContain("Tools allowed");
  });

  it("keeps Tools allowed while a search narrows a catalog where no app reports tools", async () => {
    api.catalog = withoutTools(clone(noneAdded));
    renderPage();
    await rowsLoaded();
    await userEvent.type(
      screen.getByRole("textbox", { name: "Search apps" }),
      "wiki",
    );
    expect(rowIds()).toEqual(["acme-docs"]);
    expect(headers()).toEqual(ALL_HEADERS);
  });
});

describe("Auth filter", () => {
  it("names itself in the field, filters, and clears with the other filters", async () => {
    renderPage();
    await rowsLoaded();
    expect(screen.queryByText("Auth", { selector: "label" })).toBeNull();
    const auth = screen.getByRole("combobox", { name: "Auth" });
    expect(auth).toHaveTextContent("Auth: Any");
    await userEvent.click(auth);
    await userEvent.click(
      await screen.findByRole("option", { name: "API key" }),
    );
    expect(
      await screen.findByRole("combobox", { name: "Auth" }),
    ).toHaveTextContent("Auth: API key");
    expect(rowIds()).toEqual(["acme-docs", "echo-chat", "fern-files"]);
    await userEvent.type(
      screen.getByRole("textbox", { name: "Search apps" }),
      "no-such-app",
    );
    expect(screen.getByText("No apps match")).toBeTruthy();
    await userEvent.click(
      screen.getByRole("button", { name: "Clear filters" }),
    );
    expect(screen.getByRole("combobox", { name: "Auth" })).toHaveTextContent(
      "Auth: Any",
    );
    expect(rowIds()).toHaveLength(noneAdded.results.length);
  });
});

describe("access control in the drawer", () => {
  it("is left out for a coming-soon app with nothing added", async () => {
    api.catalog.results = [heldApp("Excel")];
    api.catalog.count = 1;
    renderPage();
    await userEvent.click(await screen.findByTestId("row-microsoft-excel"));
    const drawer = await screen.findByRole("dialog");
    expect(
      within(drawer).queryByRole("heading", { name: "Access control" }),
    ).toBeNull();
    expect(
      within(drawer).queryByText("Historical / declared posture — not live"),
    ).toBeNull();
  });

  it("stays for a coming-soon app the organization already added", async () => {
    const app = heldApp("Excel");
    app.providers = clone(noneAdded.results[0].providers);
    app.servers = [
      {
        server_id: "existing-excel",
        catalog_ref: "microsoft-excel/futureagi-rest@1.0.0",
        provider: "futureagi-rest",
        credential_mode: "per_user",
        start_url: "/agentcc/mcp/connect/existing-excel/start/",
        enabled: true,
        connected: false,
        virtual_server: null,
      },
    ];
    api.catalog.results = [app];
    api.catalog.count = 1;
    renderPage();
    await userEvent.click(await screen.findByTestId("row-microsoft-excel"));
    const drawer = await screen.findByRole("dialog");
    expect(
      within(drawer).getByRole("heading", { name: "Access control" }),
    ).toBeTruthy();
    expect(
      within(drawer).getByRole("button", { name: "Disconnect" }),
    ).toBeTruthy();
  });

  it("stays for an app that can be connected", async () => {
    renderPage();
    await rowsLoaded();
    await userEvent.click(row("acme-docs"));
    const drawer = await screen.findByRole("dialog");
    expect(
      within(drawer).getByRole("heading", { name: "Access control" }),
    ).toBeTruthy();
    expect(
      within(drawer).getByText("Historical / declared posture — not live"),
    ).toBeTruthy();
  });
});

describe("no match", () => {
  it("points to adding the app by its MCP server URL", async () => {
    renderPage();
    await rowsLoaded();
    await userEvent.type(
      screen.getByRole("textbox", { name: "Search apps" }),
      "slack",
    );
    expect(screen.getByText("No apps match")).toBeTruthy();
    expect(
      screen.getByText(
        "Try another search, category or auth type, or add the app's MCP server by its URL with Custom MCP server.",
      ),
    ).toBeTruthy();
    expect(
      screen.queryByText("Try another search, category or auth type."),
    ).toBeNull();
    expect(screen.getByRole("button", { name: "Clear filters" })).toBeTruthy();
  });
});
