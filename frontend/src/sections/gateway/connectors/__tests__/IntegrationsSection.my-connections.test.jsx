import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HelmetProvider } from "react-helmet-async";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { act, render, screen, waitFor, within } from "src/utils/test-utils";

import { HOST_API } from "src/config-global";

import noneAdded from "../__fixtures__/catalog-none-added.json";
import {
  AUTHORIZE_URL,
  ISSUES,
  TICKETS,
  connected,
  connection,
  disconnectUrl,
  listOf,
  notStartable,
  revoked,
  started,
} from "../__fixtures__/my-connections";
import { authorizeTarget } from "../connect-flow";
import IntegrationsSection from "../IntegrationsSection";
import {
  useAddConnector,
  useStoreConnectorKey,
} from "../hooks/useConnectorCatalog";
import { MY_CONNECTIONS_QUERY_KEY } from "../hooks/useMyConnections";

// "Your accounts": the reader's own account on each of the organization's
// per-person MCP servers (GET /agentcc/mcp/connect/). The list answers are
// hand-written to the view's serializers (see __fixtures__/my-connections.js);
// the catalog beside them is CN9's own answer.
const LIST = "/agentcc/mcp/connect/";
const BINDING = "test-sealed-binding-not-real";

const { api, org, mcp, snackbars, browser } = vi.hoisted(() => ({
  api: { catalog: null, list: null, post: null, remove: null, gets: [] },
  org: { level: 3 },
  mcp: { enabled: true, upstreamOAuth: true },
  snackbars: [],
  browser: { opened: [] },
}));

// Where the page sends the browser to sign in, recorded instead of followed.
vi.mock("../connect-flow", async (importOriginal) => ({
  ...(await importOriginal()),
  openSignIn: (url) => browser.opened.push(url),
}));

vi.mock("src/utils/axios", () => ({
  default: {
    get: async (url) => {
      api.gets.push(url);
      if (url === "/agentcc/mcp/connect/") {
        if (typeof api.list === "function") return api.list();
        return { data: api.list };
      }
      return { data: api.catalog };
    },
    post: (...args) => api.post(...args),
    delete: (...args) => api.remove(...args),
  },
  endpoints: {
    gateway: {
      connectorCatalog: "/agentcc/connectors/catalog/",
      connectorAdd: (appId) => `/agentcc/connectors/apps/${appId}/add/`,
      mcpCredentials: "/agentcc/mcp/credentials/",
      mcpConnections: "/agentcc/mcp/connect/",
    },
  },
}));
vi.mock("src/sections/gateway/context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "gw-1" }),
}));
vi.mock("src/sections/gateway/mcp/hooks/useMCPConfig", () => ({
  // Django's mcp-status, as the MCP page's own hook reads it.
  useMCPStatus: () => ({
    data: mcp.enabled
      ? {
          enabled: true,
          features: {
            catalog: true,
            credential_vault: true,
            virtual_servers: true,
            upstream_oauth: mcp.upstreamOAuth,
            client_oauth: true,
            k2_apps: true,
          },
        }
      : { enabled: false, reason: "gateway_mcp_disabled" },
    isPending: false,
    isError: false,
  }),
}));
vi.mock("ag-grid-react", () => import("./ag-grid-mock"));
vi.mock("src/hooks/use-ag-theme", () => ({ useAgThemeWith: () => ({}) }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("notistack", () => ({
  enqueueSnackbar: (message, options) => snackbars.push({ message, options }),
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: org.level }),
}));

const clone = (value) => JSON.parse(JSON.stringify(value));
const absolute = (path) => new URL(path, HOST_API).href;

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <HelmetProvider>
      <QueryClientProvider client={client}>
        <IntegrationsSection />
      </QueryClientProvider>
    </HelmetProvider>,
  );
  return client;
}

async function panel() {
  const section = await screen.findByRole("region", { name: "Your accounts" });
  await waitFor(() =>
    expect(section.getAttribute("aria-busy")).not.toBe("true"),
  );
  return section;
}

beforeEach(() => {
  org.level = 3; // a Member: no gateway admin rights
  mcp.enabled = true;
  mcp.upstreamOAuth = true;
  snackbars.length = 0;
  browser.opened.length = 0;
  api.gets.length = 0;
  api.catalog = clone(noneAdded);
  api.list = listOf([]);
  api.post = vi.fn(async (url) => {
    throw new Error(`unexpected POST ${url}`);
  });
  api.remove = vi.fn(async (url) => {
    throw new Error(`unexpected DELETE ${url}`);
  });
});

afterEach(() => window.history.replaceState({}, "", "/"));

describe("a member's own accounts", () => {
  it("lists them for a Member and starts a sign-in only when Connect is chosen", async () => {
    api.list = listOf([
      connection(ISSUES),
      notStartable(TICKETS, "unknown", "client_not_confirmed"),
    ]);
    api.post = vi.fn(async () => ({ data: started() }));
    renderPage();

    const section = await panel();
    const issues = within(section).getByTestId(`my-connection-${ISSUES}`);
    const tickets = within(section).getByTestId(`my-connection-${TICKETS}`);
    expect(within(issues).getByText("Not connected")).toBeTruthy();
    // Readiness is unknown without the organization's own app: no Connect,
    // and no claim that an app must be added.
    expect(within(tickets).queryByRole("button", { name: /connect/i })).toBe(
      null,
    );
    expect(tickets.textContent).toContain(
      "Future AGI cannot confirm from here that this server accepts a sign-in",
    );
    expect(api.post).not.toHaveBeenCalled();

    await userEvent.click(
      within(issues).getByRole("button", {
        name: `Connect your account on ${ISSUES}`,
      }),
    );

    await waitFor(() => expect(browser.opened).toEqual([AUTHORIZE_URL]));
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(api.post).toHaveBeenCalledWith(
      absolute(`/agentcc/mcp/connect/${ISSUES}/start/`),
      {},
      { withCredentials: true },
    );
  });

  it("is not drawn when the reader has no account and followed no link", async () => {
    renderPage();
    await screen.findByTestId("row-acme-docs");
    await waitFor(() => expect(api.gets).toContain(LIST));
    expect(screen.queryByRole("region", { name: "Your accounts" })).toBe(null);
  });

  it("is not drawn, nor read, while MCP is off", async () => {
    mcp.enabled = false;
    api.list = listOf([connection(ISSUES)]);
    renderPage();
    await screen.findByText("MCP is turned off for this deployment");
    expect(screen.queryByRole("region", { name: "Your accounts" })).toBe(null);
    expect(api.gets).not.toContain(LIST);
  });

  it("offers no Connect where a sign-in cannot start, and keeps the reader's Disconnect", async () => {
    api.list = listOf([
      notStartable("broken-app", "needs_admin", "oauth_client_unreadable", {
        status: "needs_reauth",
        disconnect_url: disconnectUrl("broken-app"),
      }),
      notStartable("moved-away", "unavailable", "server_changed", {
        status: "active",
        connected_at: "2026-10-26T10:00:00Z",
        disconnect_url: disconnectUrl("moved-away"),
      }),
    ]);
    renderPage();
    const section = await panel();

    expect(within(section).queryByRole("button", { name: /^connect/i })).toBe(
      null,
    );
    expect(section.textContent).toContain(
      "Your organization's OAuth app for this server cannot be read.",
    );
    expect(section.textContent).toContain(
      "This server no longer connects each person's own account.",
    );
    expect(
      within(section).getAllByRole("button", { name: /^Disconnect/ }),
    ).toHaveLength(2);
    // Still stored as active, but the gateway no longer uses it.
    const moved = within(section).getByTestId("my-connection-moved-away");
    expect(within(moved).getByText("Not in use")).toBeTruthy();
    expect(within(moved).queryByText("Connected")).toBe(null);
  });

  it("keeps an account connected without the organization's app manageable, and claims no app is missing", async () => {
    api.list = listOf([
      connected(TICKETS, {
        start: { state: "unknown", reason: "client_not_confirmed" },
        start_url: null,
      }),
    ]);
    renderPage();
    const section = await panel();
    const row = within(section).getByTestId(`my-connection-${TICKETS}`);

    expect(within(row).getByText("Connected")).toBeTruthy();
    expect(within(row).queryByRole("button", { name: /^connect/i })).toBe(null);
    expect(
      within(row).getByRole("button", {
        name: `Disconnect your account on ${TICKETS}`,
      }),
    ).toBeTruthy();
    expect(row.textContent).not.toContain("cannot confirm");
    expect(row.textContent).not.toContain("OAuth app");
  });

  it("disconnects the reader's own account after they confirm", async () => {
    api.list = listOf([connected(ISSUES)]);
    api.remove = vi.fn(async () => {
      api.list = listOf([revoked(ISSUES)]);
      return {
        data: {
          status: true,
          result: {
            server_id: ISSUES,
            disconnected: true,
            gateway_synced: true,
          },
        },
      };
    });
    renderPage();
    const section = await panel();
    expect(within(section).getByText("Connected")).toBeTruthy();
    expect(within(section).queryByRole("button", { name: /^connect/i })).toBe(
      null,
    );

    await userEvent.click(
      within(section).getByRole("button", {
        name: `Disconnect your account on ${ISSUES}`,
      }),
    );
    expect(api.remove).not.toHaveBeenCalled();
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Disconnect" }),
    );

    await waitFor(() =>
      expect(api.remove).toHaveBeenCalledWith(absolute(disconnectUrl(ISSUES))),
    );
    expect(snackbars.map((s) => s.message)).toContain(
      `Your account on ${ISSUES} is disconnected.`,
    );
    // The list is read again: the row offers Connect, not Disconnect. (The
    // dialog hides the page from the accessibility tree until it closes.)
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    const row = await screen.findByTestId(`my-connection-${ISSUES}`);
    await waitFor(() =>
      expect(within(row).getByText("Disconnected")).toBeTruthy(),
    );
    expect(within(row).getAllByRole("button")).toHaveLength(1);
    expect(within(row).queryByRole("button", { name: /^Disconnect/ })).toBe(
      null,
    );
    expect(
      within(row).getByRole("button", {
        name: `Connect your account on ${ISSUES}`,
      }),
    ).toBeTruthy();
  });

  it("names a refused start's reason and follows no page", async () => {
    api.list = listOf([connection(ISSUES)]);
    api.post = vi.fn(async () => {
      throw { code: "discovery_failed", message: "metadata unreadable" };
    });
    renderPage();
    const section = await panel();

    await userEvent.click(
      within(section).getByRole("button", {
        name: `Connect your account on ${ISSUES}`,
      }),
    );

    expect((await within(section).findByRole("alert")).textContent).toBe(
      "Future AGI could not read this server's sign-in settings. Try again later.",
    );
    expect(browser.opened).toEqual([]);
  });

  it("never follows a sign-in page the console cannot vouch for", async () => {
    api.list = listOf([connection(ISSUES)]);
    api.post = vi.fn(async () => ({
      // eslint-disable-next-line no-script-url
      data: started("javascript:alert(1)"),
    }));
    renderPage();
    const section = await panel();

    await userEvent.click(
      within(section).getByRole("button", {
        name: `Connect your account on ${ISSUES}`,
      }),
    );

    expect((await within(section).findByRole("alert")).textContent).toBe(
      "The sign-in did not start: The server's sign-in page cannot be opened from this console.",
    );
    expect(browser.opened).toEqual([]);
  });
});

describe("R3-H6-002 catalog removal and account freshness", () => {
  it.each(["active", "needs_reauth"])(
    "removing an app refreshes its %s account before reporting success",
    async (status) => {
      org.level = 8;
      const serverId = "acme-docs-user";
      const app = api.catalog.results.find((entry) => entry.id === "acme-docs");
      const provider = app.providers[0];
      provider.credential = { mode: "per_user", kind: "oauth" };
      provider.account_modes = ["user"];
      provider.connect = { state: "ready", served: true };
      app.servers = [
        {
          server_id: serverId,
          provider: provider.id,
          catalog_ref: provider.catalog_ref,
          credential_mode: "per_user",
          connected: true,
        },
      ];
      api.list = listOf([
        connected(serverId, { status, connected: status === "active" }),
        connected(TICKETS),
      ]);
      let finishRead;
      const pendingRead = new Promise((resolve) => {
        finishRead = resolve;
      });
      api.remove = vi.fn(async () => {
        api.catalog = {
          ...api.catalog,
          results: api.catalog.results.map((entry) =>
            entry.id === app.id
              ? { ...entry, servers: [], connected: false }
              : entry,
          ),
        };
        api.list = () => pendingRead;
        return { data: { result: { gateway_synced: true } } };
      });
      renderPage();
      const section = await panel();
      const row = within(section).getByTestId(`my-connection-${serverId}`);
      expect(
        within(row).getByText(
          status === "active" ? "Connected" : "Needs a new sign-in",
        ),
      ).toBeTruthy();
      await userEvent.click(await screen.findByTestId("row-acme-docs"));
      const drawer = await screen.findByRole("dialog");
      await userEvent.click(
        within(drawer).getByRole("button", { name: "Disconnect" }),
      );
      const confirmation = await screen.findByRole("dialog", {
        name: "Disconnect Acme Docs?",
      });
      await userEvent.click(
        within(confirmation).getByRole("button", { name: "Disconnect" }),
      );

      await waitFor(() => {
        expect(api.gets.filter((url) => url === LIST)).toHaveLength(2);
        expect(section).toHaveAttribute("aria-busy", "true");
        expect(within(row).queryByText("Connected")).toBeNull();
        expect(within(row).getByText("Updating...")).toBeTruthy();
        // The drawer hides the underlying page from accessibility queries.
        for (const button of within(row).getAllByRole("button", {
          hidden: true,
        })) {
          expect(button).toBeDisabled();
        }
      });
      expect(api.remove).toHaveBeenCalledWith(
        "/agentcc/connectors/apps/acme-docs/add/",
      );
      expect(snackbars.map((entry) => entry.message)).not.toContain(
        "Acme Docs is disconnected.",
      );
      await act(async () => {
        finishRead({ data: listOf([connected(TICKETS)]) });
        await pendingRead;
      });
      await waitFor(() => {
        expect(screen.queryByTestId(`my-connection-${serverId}`)).toBeNull();
        expect(snackbars.map((entry) => entry.message)).toContain(
          "Acme Docs is disconnected.",
        );
      });
      const remaining = within(section).getByTestId(`my-connection-${TICKETS}`);
      expect(within(remaining).getByText("Connected")).toBeTruthy();
      expect(
        within(remaining).getByRole("button", { hidden: true }),
      ).toBeEnabled();
      expect(api.remove).toHaveBeenCalledTimes(1);
      expect(api.post).not.toHaveBeenCalled();
    },
  );

  it("dismisses an old confirmation and keeps stale actions unavailable after a failed refresh", async () => {
    api.list = listOf([connected(ISSUES)]);
    const client = renderPage();
    const section = await panel();
    const row = within(section).getByTestId(`my-connection-${ISSUES}`);
    await userEvent.click(
      within(row).getByRole("button", {
        name: `Disconnect your account on ${ISSUES}`,
      }),
    );
    await screen.findByRole("dialog");
    let failRead;
    const pendingRead = new Promise((_resolve, reject) => {
      failRead = reject;
    });
    api.list = () => pendingRead;
    let refresh;
    act(() => {
      refresh = client.invalidateQueries({
        queryKey: MY_CONNECTIONS_QUERY_KEY,
      });
    });
    await waitFor(() => {
      expect(section).toHaveAttribute("aria-busy", "true");
      expect(screen.queryByRole("dialog")).toBeNull();
    });
    expect(within(row).getByRole("button")).toBeDisabled();
    await act(async () => {
      failRead(new Error("Accounts unavailable"));
      await refresh;
    });
    await within(section).findByRole("alert");
    expect(within(row).queryByText("Connected")).toBeNull();
    expect(within(row).getByText("Status unavailable")).toBeTruthy();
    expect(within(row).getByRole("button")).toBeDisabled();
    expect(api.remove).not.toHaveBeenCalled();
    api.list = listOf([connected(ISSUES)]);
    await userEvent.click(
      within(section).getByRole("button", { name: "Retry your accounts" }),
    );
    await waitFor(() => {
      expect(within(row).getByText("Connected")).toBeTruthy();
      expect(within(row).getByRole("button")).toBeEnabled();
    });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(api.remove).not.toHaveBeenCalled();
  });
});

describe("R3-H6-002 callback inventory refresh", () => {
  it.each([
    [
      "add app",
      useAddConnector,
      (operation) => operation.add("acme-docs", { provider: "docs-mcp" }),
    ],
    [
      "store shared key",
      useStoreConnectorKey,
      (operation) =>
        operation.store(
          "acme-docs",
          { kind: "bearer" },
          "synthetic-test-value",
        ),
    ],
  ])(
    "%s refreshes the separately rendered accounts panel before completing",
    async (_name, useOperation, run) => {
      api.list = listOf([
        connected(ISSUES),
        connected(TICKETS, { status: "needs_reauth", connected: false }),
      ]);
      const client = renderPage();
      const section = await panel();
      const activeRow = within(section).getByTestId(`my-connection-${ISSUES}`);
      const reauthRow = within(section).getByTestId(`my-connection-${TICKETS}`);
      expect(within(activeRow).getByText("Connected")).toBeTruthy();
      expect(within(reauthRow).getByText("Needs a new sign-in")).toBeTruthy();

      // A separate React root prevents the operation's pending-state render
      // from accidentally refreshing the accounts observer in this page.
      const wrapper = ({ children }) => (
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
      );
      const operation = renderHook(() => useOperation("gw-1"), { wrapper });
      let finishRead;
      const pendingRead = new Promise((resolve) => {
        finishRead = resolve;
      });
      api.list = () => pendingRead;
      api.post = vi.fn(async () => ({ data: { result: { changed: true } } }));
      let finished = false;
      let completion;
      act(() => {
        completion = run(operation.result.current).then(() => {
          finished = true;
        });
      });

      await waitFor(() => {
        expect(api.gets.filter((url) => url === LIST)).toHaveLength(2);
        expect(
          api.gets.filter((url) => url === "/agentcc/connectors/catalog/"),
        ).toHaveLength(2);
        expect(
          client.getQueryState([...MY_CONNECTIONS_QUERY_KEY, true]).fetchStatus,
        ).toBe("fetching");
        expect(operation.result.current.isPending).toBe(true);
        expect(section).toHaveAttribute("aria-busy", "true");
        for (const row of [activeRow, reauthRow]) {
          expect(within(row).queryByText("Connected")).toBeNull();
          expect(within(row).getByText("Updating...")).toBeTruthy();
          for (const button of within(row).getAllByRole("button")) {
            expect(button).toBeDisabled();
          }
        }
      });
      expect(finished).toBe(false);
      expect(client.getMutationCache().getAll()).toHaveLength(0);
      // The rows' buttons are disabled (asserted above), so they carry
      // pointer-events: none and user-event's default check refuses to click
      // them. Click anyway to prove the stale actions do nothing.
      const forcedUser = userEvent.setup({ pointerEventsCheck: 0 });
      await forcedUser.click(
        within(reauthRow).getByRole("button", {
          name: `Connect your account on ${TICKETS} again`,
        }),
      );
      await forcedUser.click(
        within(activeRow).getByRole("button", {
          name: `Disconnect your account on ${ISSUES}`,
        }),
      );
      expect(api.post).toHaveBeenCalledTimes(1);
      expect(api.remove).not.toHaveBeenCalled();
      expect(browser.opened).toEqual([]);
      expect(screen.queryByRole("dialog")).toBeNull();
      expect(finished).toBe(false);

      await act(async () => {
        finishRead({ data: listOf([connected(TICKETS)]) });
        await completion;
      });
      await waitFor(() => {
        expect(operation.result.current.isPending).toBe(false);
        expect(section).toHaveAttribute("aria-busy", "false");
        expect(screen.queryByTestId(`my-connection-${ISSUES}`)).toBeNull();
        expect(within(reauthRow).getByText("Connected")).toBeTruthy();
        expect(within(reauthRow).getByRole("button")).toBeEnabled();
      });
      expect(finished).toBe(true);
      expect(api.gets.filter((url) => url === LIST)).toHaveLength(2);
      expect(client.getMutationCache().getAll()).toHaveLength(0);
    },
  );
});

// A gateway connect link lands on ?connect=<server>&binding=<sealed>.
describe("a gateway connect link", () => {
  it("marks its server's row, drops its binding from the address and starts nothing", async () => {
    window.history.replaceState(
      {},
      "",
      `/integrations?connect=${ISSUES}&binding=${BINDING}`,
    );
    api.list = listOf([connection(ISSUES), connection(TICKETS)]);
    api.post = vi.fn(async () => ({ data: started() }));
    renderPage();
    const section = await panel();

    await waitFor(() =>
      expect(window.location.search).toBe(`?connect=${ISSUES}`),
    );
    const row = within(section).getByTestId(`my-connection-${ISSUES}`);
    expect(row.getAttribute("aria-current")).toBe("true");
    expect(
      within(section)
        .getByTestId(`my-connection-${TICKETS}`)
        .getAttribute("aria-current"),
    ).toBe(null);
    expect(api.post).not.toHaveBeenCalled();
    expect(browser.opened).toEqual([]);

    await userEvent.click(
      within(row).getByRole("button", {
        name: `Connect your account on ${ISSUES}`,
      }),
    );

    await waitFor(() => expect(browser.opened).toEqual([AUTHORIZE_URL]));
    expect(api.post).toHaveBeenCalledWith(
      absolute(`/agentcc/mcp/connect/${ISSUES}/start/`),
      { binding: BINDING },
      { withCredentials: true },
    );
  });

  it("is spent once refused, so the next Connect starts the reader's own account", async () => {
    window.history.replaceState(
      {},
      "",
      `/integrations?connect=${ISSUES}&binding=${BINDING}`,
    );
    api.list = listOf([connection(ISSUES)]);
    api.post = vi
      .fn()
      .mockRejectedValueOnce({
        code: "binding_mismatch",
        message: "the connect link was made for someone else",
      })
      .mockResolvedValueOnce({ data: started() });
    renderPage();
    const row = await screen.findByTestId(`my-connection-${ISSUES}`);
    const connect = () =>
      userEvent.click(
        within(row).getByRole("button", {
          name: `Connect your account on ${ISSUES}`,
        }),
      );

    await connect();
    expect((await within(row).findByRole("alert")).textContent).toBe(
      "The link you followed was made for someone else. Connect starts your own account instead.",
    );
    expect(browser.opened).toEqual([]);
    await connect();

    await waitFor(() => expect(browser.opened).toEqual([AUTHORIZE_URL]));
    expect(api.post.mock.calls.map((call) => call[1])).toEqual([
      { binding: BINDING },
      {},
    ]);
  });

  it("says so when it names a server with nothing for the reader to connect", async () => {
    window.history.replaceState({}, "", "/integrations?connect=elsewhere");
    renderPage();
    const section = await panel();
    expect(section.textContent).toContain(
      "The link you followed names an MCP server with no account for you to connect here.",
    );
    expect(api.post).not.toHaveBeenCalled();
  });
});

describe("account readiness failures", () => {
  it("keeps a connect landing visible through loading, failure and retry", async () => {
    window.history.replaceState(
      {},
      "",
      `/integrations?connect=${ISSUES}&binding=${BINDING}`,
    );
    let rejectRead;
    api.list = () =>
      new Promise((resolve, reject) => {
        rejectRead = reject;
      });
    renderPage();
    const section = await screen.findByRole("region", {
      name: "Your accounts",
    });
    expect(within(section).getByRole("status").textContent).toContain(
      "Loading your accounts",
    );
    expect(section.textContent).not.toContain("no account for you");
    await waitFor(() => expect(rejectRead).toBeTypeOf("function"));
    rejectRead(new Error("unavailable"));
    expect((await within(section).findByRole("alert")).textContent).toContain(
      "Your accounts could not be loaded",
    );
    expect(window.location.search).toBe(`?connect=${ISSUES}`);
    api.list = listOf([connection(ISSUES)]);
    await userEvent.click(
      within(section).getByRole("button", { name: "Retry your accounts" }),
    );
    const row = await within(section).findByTestId(`my-connection-${ISSUES}`);
    expect(row.getAttribute("aria-current")).toBe("true");
    expect(api.post).not.toHaveBeenCalled();
  });

  it("does not describe every client refusal as a missing app", async () => {
    api.list = listOf([connection(ISSUES)]);
    api.post = vi.fn(async () => {
      throw {
        code: "oauth_client_unavailable",
        message: "the server takes no client secret",
      };
    });
    renderPage();
    const section = await panel();
    await userEvent.click(
      within(section).getByRole("button", {
        name: `Connect your account on ${ISSUES}`,
      }),
    );
    const alert = await within(section).findByRole("alert");
    expect(alert.textContent).not.toContain("can add one");
    expect(alert.textContent).toContain("OAuth client");
  });

  it("shows persistent cleanup and retries instead of promising immediate eviction", async () => {
    api.list = listOf([connected(ISSUES)]);
    api.remove = vi.fn(async () => {
      const pending = api.remove.mock.calls.length === 1;
      api.list = listOf([
        revoked(ISSUES, {
          cleanup_pending: pending,
          disconnect_url: pending ? disconnectUrl(ISSUES) : null,
        }),
      ]);
      return {
        data: {
          status: true,
          result: {
            server_id: ISSUES,
            disconnected: true,
            gateway_synced: !pending,
            cleanup_pending: pending,
          },
        },
      };
    });
    renderPage();
    const section = await panel();
    await userEvent.click(
      within(section).getByRole("button", {
        name: `Disconnect your account on ${ISSUES}`,
      }),
    );
    const dialog = await screen.findByRole("dialog");
    expect(dialog.textContent).not.toContain("at once");
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Disconnect" }),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(snackbars.some((s) => s.options.variant === "success")).toBe(false);
    expect((await within(section).findByRole("alert")).textContent).toContain(
      "cleanup",
    );
    const retry = await within(section).findByRole("button", {
      name: `Retry disconnect on ${ISSUES}`,
    });
    await userEvent.click(retry);
    const retryDialog = await screen.findByRole("dialog");
    await userEvent.click(
      within(retryDialog).getByRole("button", { name: "Disconnect" }),
    );
    await waitFor(() => expect(api.remove).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(within(section).queryByRole("alert")).toBeNull(),
    );
  });

  it("drops the old cleanup warning after a replacement account is fetched", async () => {
    api.list = listOf([connected(ISSUES)]);
    api.remove = vi.fn(async () => {
      api.list = listOf([
        revoked(ISSUES, {
          cleanup_pending: true,
          upstream_revocation: "pending",
          disconnect_url: disconnectUrl(ISSUES),
        }),
      ]);
      return {
        data: {
          status: true,
          result: {
            disconnected: true,
            gateway_synced: true,
            cleanup_pending: true,
            upstream_revocation: "pending",
          },
        },
      };
    });
    const client = renderPage();
    const section = await panel();
    await userEvent.click(
      within(section).getByRole("button", {
        name: `Disconnect your account on ${ISSUES}`,
      }),
    );
    await userEvent.click(
      within(await screen.findByRole("dialog")).getByRole("button", {
        name: "Disconnect",
      }),
    );
    await within(section).findByRole("button", {
      name: `Retry disconnect on ${ISSUES}`,
    });
    api.list = listOf([
      connected(ISSUES, { connected_at: "2026-10-27T12:00:00Z" }),
    ]);
    await client.invalidateQueries();
    await waitFor(() =>
      expect(within(section).queryByRole("alert")).toBeNull(),
    );
    expect(within(section).getByText("Connected")).toBeTruthy();
    expect(within(section).queryByText("Cleanup pending")).toBeNull();
  });

  it("never hides Connect while only the vendor's revocation of the old account is unconfirmed", async () => {
    api.list = listOf([
      revoked(ISSUES, {
        cleanup_pending: true,
        upstream_revocation: "pending",
        disconnect_url: disconnectUrl(ISSUES),
      }),
    ]);
    renderPage();
    const section = await panel();
    const row = within(section).getByTestId(`my-connection-${ISSUES}`);

    expect(
      within(row).getByRole("button", {
        name: `Connect your account on ${ISSUES}`,
      }),
    ).toBeTruthy();
    expect(
      within(row).getByRole("button", {
        name: `Retry disconnect on ${ISSUES}`,
      }),
    ).toBeTruthy();
    expect(row.textContent).toContain("has not confirmed");
    expect(within(row).queryByText("Cleanup pending")).toBe(null);
  });

  it("says when the vendor never confirmed a revocation and Future AGI stopped trying", async () => {
    api.list = listOf([
      revoked(ISSUES, { upstream_revocation: "unconfirmed" }),
    ]);
    renderPage();
    const section = await panel();
    const row = within(section).getByTestId(`my-connection-${ISSUES}`);

    expect(row.textContent).toContain("did not confirm");
    expect(row.textContent).toContain("vendor account settings");
    expect(row.textContent).not.toContain("no longer holds");
    expect(
      within(row).getByRole("button", {
        name: `Connect your account on ${ISSUES}`,
      }),
    ).toBeTruthy();
    expect(within(row).queryByRole("button", { name: /disconnect/i })).toBe(
      null,
    );
  });

  it("keeps Connect hidden until the gateway drops the old account", async () => {
    api.list = listOf([
      revoked(ISSUES, {
        cleanup_pending: true,
        start: { state: "unavailable", reason: "cleanup_pending" },
        start_url: null,
        disconnect_url: disconnectUrl(ISSUES),
      }),
    ]);
    renderPage();
    const section = await panel();
    const row = within(section).getByTestId(`my-connection-${ISSUES}`);

    expect(within(row).queryByRole("button", { name: /^connect/i })).toBe(null);
    expect(within(row).getByText("Cleanup pending")).toBeTruthy();
    expect(
      within(row).getByRole("button", {
        name: `Retry disconnect on ${ISSUES}`,
      }),
    ).toBeTruthy();
    expect(row.textContent).toContain("gateway");
  });

  it("never reports a disconnect the vendor did not confirm as a clean one", async () => {
    api.list = listOf([connected(ISSUES)]);
    api.remove = vi.fn(async () => {
      api.list = listOf([
        revoked(ISSUES, { upstream_revocation: "unconfirmed" }),
      ]);
      return {
        data: {
          status: true,
          result: {
            server_id: ISSUES,
            disconnected: true,
            gateway_synced: true,
            cleanup_pending: false,
            upstream_revocation: "unconfirmed",
          },
        },
      };
    });
    renderPage();
    const section = await panel();
    await userEvent.click(
      within(section).getByRole("button", {
        name: `Disconnect your account on ${ISSUES}`,
      }),
    );
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Disconnect" }),
    );

    await waitFor(() => expect(snackbars.length).toBeGreaterThan(0));
    expect(snackbars.some((s) => s.options.variant === "success")).toBe(false);
    expect(snackbars.map((s) => s.message).join(" ")).toContain(
      "did not confirm",
    );
  });
});

// The one rule for the vendor sign-in page the console follows.
describe("authorizeTarget", () => {
  it("takes an absolute https page on any deployment", () => {
    expect(
      authorizeTarget(
        "https://auth.vendor.example/a?b=1",
        "https://api.example",
      ),
    ).toBe("https://auth.vendor.example/a?b=1");
  });

  it("takes plain http only where the API itself is plain http", () => {
    const page = "http://vendor.local.test:8080/authorize";
    expect(authorizeTarget(page, "http://localhost:8100")).toBe(page);
    expect(authorizeTarget(page, "https://api.example")).toBe(null);
  });

  it("refuses a relative page, credentials in the URL, and any other scheme", () => {
    for (const url of [
      "/authorize",
      "//auth.vendor.example/authorize",
      "https://user:pass@auth.vendor.example/authorize",
      // eslint-disable-next-line no-script-url
      "javascript:alert(1)",
      "data:text/html,hi",
      "",
      null,
    ]) {
      expect(authorizeTarget(url, "http://localhost:8100")).toBe(null);
    }
  });
});

it("hides Your accounts and never reads connect routes with upstream OAuth OFF, even on a deep link", async () => {
  mcp.upstreamOAuth = false;
  api.list = listOf([connection(ISSUES)]);
  window.history.replaceState({}, "", "/?connect=issues&binding=synthetic");
  renderPage();
  await screen.findByTestId("row-acme-docs");
  expect(screen.queryByRole("region", { name: "Your accounts" })).toBeNull();
  expect(api.gets).not.toContain(LIST);
  expect(api.post).not.toHaveBeenCalled();
});
