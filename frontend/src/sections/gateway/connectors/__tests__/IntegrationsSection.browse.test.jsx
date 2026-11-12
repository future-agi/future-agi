import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HelmetProvider } from "react-helmet-async";
import { beforeEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen, within } from "src/utils/test-utils";

import noneAdded from "../__fixtures__/catalog-none-added.json";
import mixed from "../__fixtures__/catalog-acme-connected.json";
import IntegrationsSection from "../IntegrationsSection";

// The catalog API's answers are CN9's own responses (see __fixtures__): the
// page is tested against the shapes the server sends.
const { api, org } = vi.hoisted(() => ({
  api: { catalog: null, get: null },
  org: { level: 8 },
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

const rowIds = () =>
  screen
    .queryAllByTestId(/^row-/)
    .map((row) => row.getAttribute("data-testid").replace("row-", ""));

async function rowsLoaded() {
  await screen.findAllByTestId(/^row-/);
}

beforeEach(() => {
  org.level = 8;
  api.catalog = clone(noneAdded);
  api.get = vi.fn(async (url, { params } = {}) => {
    expect(url).toBe("/agentcc/connectors/catalog/");
    expect(params).toEqual({ limit: 200, page: 1 });
    return { data: api.catalog };
  });
});

describe("browse", () => {
  it("renders the five categories and a search box", async () => {
    renderPage();
    await rowsLoaded();
    const categories = screen.getByRole("group", { name: "Category" });
    for (const name of [
      "Productivity",
      "Developer",
      "Communication",
      "Data",
      "Research",
    ]) {
      expect(within(categories).getByRole("button", { name })).toBeTruthy();
    }
    expect(screen.getByRole("textbox", { name: "Search apps" })).toBeTruthy();
    expect(rowIds()).toEqual(noneAdded.results.map((app) => app.id));
  });

  it("search narrows the list on name, description, tags and vendor", async () => {
    renderPage();
    await rowsLoaded();
    await userEvent.type(
      screen.getByRole("textbox", { name: "Search apps" }),
      "wiki",
    );
    expect(rowIds()).toEqual(["acme-docs"]);
    await userEvent.clear(screen.getByRole("textbox", { name: "Search apps" }));
    await userEvent.type(
      screen.getByRole("textbox", { name: "Search apps" }),
      "iris labs",
    );
    expect(rowIds()).toEqual(["iris-code"]);
  });

  it("a category keeps its apps, and choosing it again shows every app", async () => {
    renderPage();
    await rowsLoaded();
    const categories = screen.getByRole("group", { name: "Category" });
    await userEvent.click(
      within(categories).getByRole("button", { name: "Research" }),
    );
    expect(rowIds()).toEqual(["cobalt-search"]);
    await userEvent.click(
      within(categories).getByRole("button", { name: "Research" }),
    );
    expect(rowIds()).toEqual(noneAdded.results.map((app) => app.id));
  });

  it("a search with no match shows the empty state, which clears the filters", async () => {
    renderPage();
    await rowsLoaded();
    await userEvent.type(
      screen.getByRole("textbox", { name: "Search apps" }),
      "no-such-app",
    );
    expect(screen.getByText("No apps match")).toBeTruthy();
    await userEvent.click(
      screen.getByRole("button", { name: "Clear filters" }),
    );
    expect(rowIds()).toHaveLength(noneAdded.results.length);
  });
});

describe("All and Connected", () => {
  beforeEach(() => {
    api.catalog = clone(mixed);
  });

  it("Connected keeps only apps whose catalog_ref server is connected", async () => {
    renderPage();
    await rowsLoaded();
    expect(rowIds()).toEqual(mixed.results.map((app) => app.id));
    expect(
      within(screen.getByTestId("row-iris-code")).getByText("Needs key"),
    ).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: /^Connected/ }));
    expect(rowIds()).toEqual(["acme-docs"]);
    expect(
      within(screen.getByTestId("row-acme-docs")).getByText("Connected"),
    ).toBeTruthy();
  });

  it("an app flagged connected with no catalog_ref server is not Connected", async () => {
    const forged = api.catalog.results.find((app) => app.id === "bolt-crm");
    forged.connected = true;
    forged.servers = [
      {
        ...api.catalog.results[0].servers[0],
        server_id: "bolt-crm",
        catalog_ref: "",
      },
    ];
    renderPage();
    await rowsLoaded();
    await userEvent.click(screen.getByRole("button", { name: /^Connected/ }));
    expect(rowIds()).toEqual(["acme-docs"]);
  });

  it("an empty Connected view says so and goes back to All", async () => {
    api.catalog = clone(noneAdded);
    renderPage();
    await rowsLoaded();
    await userEvent.click(screen.getByRole("button", { name: /^Connected/ }));
    expect(screen.getByText("No connected apps yet")).toBeTruthy();
    await userEvent.click(
      screen.getByRole("button", { name: "Show all apps" }),
    );
    expect(rowIds()).toHaveLength(noneAdded.results.length);
  });
});

describe("badges come only from quality", () => {
  it("show each provider's quality tier", async () => {
    renderPage();
    await rowsLoaded();
    expect(
      within(screen.getByTestId("row-cobalt-search")).getByText("Verified"),
    ).toBeTruthy();
    expect(
      within(screen.getByTestId("row-acme-docs")).getByText("Listed"),
    ).toBeTruthy();
  });

  it("follow quality.tier and nothing else", async () => {
    const acme = api.catalog.results.find((app) => app.id === "acme-docs");
    acme.providers[0].vendor_status = "verified";
    acme.providers[0].publisher = "verified";
    const iris = api.catalog.results.find((app) => app.id === "iris-code");
    iris.providers[0].tier = "optimized";
    const echo = api.catalog.results.find((app) => app.id === "echo-chat");
    echo.providers[0].tier = "gold";
    renderPage();
    await rowsLoaded();
    expect(
      within(screen.getByTestId("row-acme-docs")).getByText("Listed"),
    ).toBeTruthy();
    expect(
      within(screen.getByTestId("row-acme-docs")).queryByText("Verified"),
    ).toBeNull();
    expect(
      within(screen.getByTestId("row-iris-code")).getByText("Optimized"),
    ).toBeTruthy();
    // A tier outside the enum (gold) shows no quality badge at all.
    const echoRow = screen.getByTestId("row-echo-chat");
    expect(echoRow.querySelector('[data-col="tier"]').textContent).toBe("");
    for (const label of ["Listed", "Verified", "Optimized"]) {
      expect(within(echoRow).queryByText(label)).toBeNull();
    }
    expect(within(echoRow).queryByText(/gold/i)).toBeNull();
    expect(echoRow.querySelector('[data-col="app"] .MuiChip-root')).toBeNull();
  });
});

describe("Microsoft coming soon", () => {
  const names = ["Excel", "OneDrive", "Outlook", "SharePoint", "Teams"];
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
  const noConnect = (element) => {
    expect(
      within(element).queryByRole("button", {
        name: /^(connect|add|sign in|save key|configure)/i,
      }),
    ).toBeNull();
    expect(within(element).queryByRole("combobox")).toBeNull();
  };

  it.each(
    names.flatMap((name) =>
      ["new", "per_user", "shared"].map((mode) => [name, mode]),
    ),
  )(
    "%s gate-refusal API payload survives the real catalog/grid/drawer pipeline (%s)",
    async (name, mode) => {
      const app = heldApp(name);
      // The gate packet's actual provider response, deliberately without the
      // render packet's app status: normalization must join the two contracts.
      delete app.status;
      app.default_provider = "futureagi-rest";
      app.providers = [
        {
          ...clone(noneAdded.results[0].providers[0]),
          id: "futureagi-rest",
          credential: { mode: "per_user", kind: "oauth" },
          connect: {
            served: false,
            state: "unavailable",
            reason: "microsoft_coming_soon",
          },
        },
      ];
      if (mode !== "new")
        app.servers = [
          {
            server_id: app.id,
            catalog_ref: `${app.id}/futureagi-rest@1.0.0`,
            provider: "futureagi-rest",
            credential_mode: mode,
            enabled: true,
            connected: false,
            start: { state: "unavailable", reason: "microsoft_coming_soon" },
            start_url: null,
            virtual_server: null,
          },
        ];
      api.catalog.results = [app];
      api.catalog.count = 1;
      renderPage();
      const row = await screen.findByTestId(`row-${app.id}`);
      expect(within(row).getByText("Coming soon")).toBeTruthy();
      expect(within(row).queryByText("Connected")).toBeNull();
      noConnect(row);
      await userEvent.click(row);
      const drawer = await screen.findByRole("dialog");
      expect(within(drawer).getByText("Coming soon")).toBeTruthy();
      expect(
        within(drawer).queryByText(`${app.name} is connected.`),
      ).toBeNull();
      noConnect(drawer);
      if (mode !== "new") {
        expect(
          within(drawer).getByRole("button", { name: "Disconnect" }).disabled,
        ).toBe(false);
      }
      if (mode === "shared") {
        expect(
          within(drawer).getByText(
            "Unavailable: Microsoft connections are coming soon",
            { exact: true },
          ),
        ).toBeTruthy();
      }
    },
  );

  it("keeps all five empty Microsoft cards in Browse", async () => {
    api.catalog.results = names.map(heldApp);
    api.catalog.count = names.length;
    renderPage();
    await rowsLoaded();
    expect(rowIds()).toEqual(api.catalog.results.map((app) => app.id));
    for (const app of api.catalog.results) {
      const row = screen.getByTestId(`row-${app.id}`);
      expect(within(row).getByText("Coming soon")).toBeTruthy();
      noConnect(row);
    }
  });

  it.each(names)(
    "%s has no connect action or provider picker in its drawer",
    async (name) => {
      const app = heldApp(name);
      api.catalog.results = [app];
      api.catalog.count = 1;
      renderPage();
      await userEvent.click(await screen.findByTestId(`row-${app.id}`));
      const drawer = await screen.findByRole("dialog");
      expect(within(drawer).getAllByText("Coming soon").length).toBeGreaterThan(
        0,
      );
      noConnect(drawer);
      expect(
        within(drawer).queryByRole("button", { name: "Disconnect" }),
      ).toBeNull();
    },
  );

  it.each([false, true])(
    "keeps existing server cleanup when connected=%s, without reconnect",
    async (connected) => {
      const app = heldApp("Excel");
      // Even stale provider/start data must not revive a held app's connect UI.
      app.providers = clone(noneAdded.results[0].providers);
      app.servers = [
        {
          server_id: "existing-excel",
          catalog_ref: "microsoft-excel/futureagi-rest@1.0.0",
          provider: "futureagi-rest",
          credential_mode: "per_user",
          start_url: "/agentcc/mcp/connect/existing-excel/start/",
          enabled: true,
          connected,
          virtual_server: null,
        },
      ];
      api.catalog.results = [app];
      api.catalog.count = 1;
      renderPage();
      const row = await screen.findByTestId(`row-${app.id}`);
      expect(within(row).getByText("Coming soon")).toBeTruthy();
      await userEvent.click(row);
      const drawer = await screen.findByRole("dialog");
      noConnect(drawer);
      expect(
        within(drawer).getByRole("button", { name: "Disconnect" }).disabled,
      ).toBe(false);
    },
  );
});

describe("only what works today", () => {
  it("never lists a sign-in app or promises one", async () => {
    const signIn = clone(api.catalog.results[0]);
    signIn.id = "sign-in-only";
    signIn.name = "Sign In Only";
    signIn.providers = signIn.providers.map((provider) => ({
      ...provider,
      credential: { ...provider.credential, mode: "per_user", kind: "oauth2" },
    }));
    api.catalog.results.push(signIn);
    renderPage();
    await rowsLoaded();
    expect(rowIds()).not.toContain("sign-in-only");
    const text = document.body.textContent.toLowerCase();
    for (const phrase of ["oauth", "sign-in lands", "coming soon", "preview"]) {
      expect(text).not.toContain(phrase);
    }
  });

  it("links Custom MCP to the MCP servers page", async () => {
    renderPage();
    await rowsLoaded();
    expect(
      screen
        .getByRole("link", { name: "Custom MCP server" })
        .getAttribute("href"),
    ).toBe("/dashboard/gateway/mcp?tab=servers");
  });

  it("shows the API's error and retries", async () => {
    api.get = vi.fn().mockRejectedValueOnce({
      statusCode: 503,
      code: "connector_catalog_unavailable",
      message: "The connector catalog is unavailable.",
    });
    renderPage();
    expect(await screen.findByText("Apps could not be loaded")).toBeTruthy();
    expect(
      screen.getByText(/The connector catalog is unavailable\./),
    ).toBeTruthy();
    // Nothing to filter: no search box or category that would do nothing.
    expect(screen.queryByRole("textbox", { name: "Search apps" })).toBeNull();
    expect(screen.queryByRole("group", { name: "Category" })).toBeNull();
    api.get.mockResolvedValueOnce({ data: clone(noneAdded) });
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    await rowsLoaded();
    expect(rowIds()).toHaveLength(noneAdded.results.length);
  });

  it("with no app at all, offers Custom MCP from the one empty state", async () => {
    api.catalog = { ...clone(noneAdded), count: 0, results: [] };
    renderPage();
    expect(await screen.findByText("No apps to connect yet")).toBeTruthy();
    expect(screen.queryByTestId("ag-grid-mock")).toBeNull();
    // Nothing to filter: no search box, view switch or category that would
    // do nothing; the header keeps Custom MCP.
    expect(screen.queryByRole("textbox", { name: "Search apps" })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Connected/ })).toBeNull();
    expect(screen.queryByRole("group", { name: "Category" })).toBeNull();
    expect(
      screen.getByRole("link", { name: "Custom MCP server" }),
    ).toBeTruthy();
  });
});
