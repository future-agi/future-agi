import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HelmetProvider } from "react-helmet-async";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen, waitFor, within } from "src/utils/test-utils";

import { HOST_API } from "src/config-global";

import noneAdded from "../__fixtures__/catalog-none-added.json";
import removedIris from "../__fixtures__/remove-iris-code.json";
import {
  SLATE,
  SLATE_NAME,
  SLATE_DISCONNECT,
  SLATE_SIGN_IN,
  catalogWith,
  slateAccount,
  slateAdded,
  slateApp,
  slateProvider,
  slateServer,
} from "../__fixtures__/sign-in";
import IntegrationsSection from "../IntegrationsSection";

// Apps whose account is a sign-in (OAuth) rather than a key. The catalog
// answers here are hand-written to the contract in connect-flow.js (see
// __fixtures__/sign-in.js); the key apps beside them are CN9's own answers.
const add = (appId) => `/agentcc/connectors/apps/${appId}/add/`;

const { api, org, snackbars, browser } = vi.hoisted(() => ({
  api: { catalog: null, post: null, remove: null },
  org: { level: 8 },
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
    get: async () => ({ data: api.catalog }),
    post: (...args) => api.post(...args),
    delete: (...args) => api.remove(...args),
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
      features: { upstream_oauth: true, client_oauth: true, k2_apps: true },
    },
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
// Words that would promise a connect this deployment cannot make.
const PROMISES =
  /coming soon|add it again|connect it again|needs key|save key|sign.in|oauth/i;

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
}

async function openApp(appId) {
  await userEvent.click(await screen.findByTestId(`row-${appId}`));
  return screen.findByRole("dialog");
}

beforeEach(() => {
  org.level = 8;
  snackbars.length = 0;
  browser.opened.length = 0;
  api.catalog = clone(noneAdded);
  api.post = vi.fn(async (url) => {
    throw new Error(`unexpected POST ${url}`);
  });
  api.remove = vi.fn(async (url) => {
    throw new Error(`unexpected DELETE ${url}`);
  });
});

// The org added a sign-in app (through its gateway config, or on a build
// that served its sign-in), and this deployment does not serve that sign-in:
// the API lists the app with its server and none of its providers.
describe("an added sign-in app this deployment cannot connect", () => {
  function notServed() {
    return catalogWith(slateApp({ providers: [], servers: [slateServer()] }));
  }

  it("says agents cannot call it and offers only to remove it, never a key or a sign-in", async () => {
    api.catalog = notServed();
    api.remove = vi.fn(async () => {
      api.catalog = clone(noneAdded);
      return { data: clone(removedIris) };
    });
    renderPage();
    const row = await screen.findByTestId(`row-${SLATE}`);
    expect(row.textContent).toContain("Not connected");
    const drawer = await openApp(SLATE);
    expect(
      within(drawer).getByText(
        `${SLATE_NAME} is added, but this deployment cannot connect its account, so agents cannot call it. Disconnect it to remove it.`,
      ),
    ).toBeTruthy();
    expect(
      within(drawer).queryByRole("button", { name: "Connect" }),
    ).toBeNull();
    expect(drawer.querySelector('input[type="password"]')).toBeNull();
    expect(drawer.textContent).not.toMatch(PROMISES);
    expect(row.textContent).not.toMatch(PROMISES);

    await userEvent.click(
      within(drawer).getByRole("button", { name: "Disconnect" }),
    );
    const confirm = (
      await screen.findByText(`Disconnect ${SLATE_NAME}?`)
    ).closest('[role="dialog"]');
    expect(confirm.textContent).not.toMatch(PROMISES);
    // A sign-in app has accounts, not a stored key.
    expect(confirm.textContent).toContain(
      "This removes the app's server and virtual server from the gateway and revokes the gateway's access to every account signed in to it. Agents lose its tools at once.",
    );
    expect(confirm.textContent).not.toMatch(/stored key/i);
    await userEvent.click(
      within(confirm).getByRole("button", { name: "Disconnect" }),
    );
    await waitFor(() => expect(api.remove).toHaveBeenCalledWith(add(SLATE)));
    await waitFor(() =>
      expect(screen.queryByTestId(`row-${SLATE}`)).toBeNull(),
    );
  });

  it("tells a member the same fact, with Disconnect disabled", async () => {
    org.level = 1;
    api.catalog = notServed();
    renderPage();
    const drawer = await openApp(SLATE);
    expect(
      within(drawer).getByText(
        `${SLATE_NAME} is added, but this deployment cannot connect its account, so agents cannot call it.`,
      ),
    ).toBeTruthy();
    expect(
      within(drawer).getByRole("button", { name: "Disconnect" }).disabled,
    ).toBe(true);
    expect(drawer.textContent).not.toMatch(PROMISES);
  });
});

// A sign-in app whose sign-in this deployment serves (connect.served on its
// card): the admin adds it with no key and is sent to its sign-in, and each
// person then signs in to their own account.
describe("connecting a sign-in app", () => {
  const SIGN_IN_BUTTON = `Sign in to ${SLATE_NAME}`;

  function shared() {
    const provider = slateProvider();
    provider.account_modes = ["agent"];
    provider.credential = { ...provider.credential, mode: "shared" };
    return provider;
  }

  it("an admin adds it with no key and is sent to the API's sign-in for it", async () => {
    api.catalog = catalogWith(slateApp());
    api.post = vi.fn(async (url) => {
      if (url !== add(SLATE)) throw new Error(`unexpected POST ${url}`);
      api.catalog = catalogWith(slateApp({ servers: [slateServer()] }));
      return { data: slateAdded() };
    });
    renderPage();
    const drawer = await openApp(SLATE);
    expect(drawer.querySelector('input[type="password"]')).toBeNull();
    expect(
      within(drawer).getAllByText("Each person signs in with their own account")
        .length,
    ).toBeGreaterThan(0);
    await userEvent.click(
      within(drawer).getByRole("button", { name: "Connect" }),
    );
    await waitFor(() => expect(browser.opened).toHaveLength(1));
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(api.post).toHaveBeenCalledWith(add(SLATE), {
      provider: "slate-oauth",
      mode: "user",
      url_variables: {},
    });
    expect(browser.opened[0]).toBe(
      `${new URL(HOST_API).origin}${SLATE_SIGN_IN}`,
    );
  });

  it("an add whose answer names no sign-in on the API's own path sends the browser nowhere, and the app's own sign-in follows", async () => {
    api.catalog = catalogWith(slateApp());
    api.post = vi.fn(async () => {
      api.catalog = catalogWith(
        slateApp({ servers: [slateServer({ connect_url: SLATE_SIGN_IN })] }),
      );
      return {
        data: slateAdded({
          connectUrl: "https://other.example.test/agentcc/mcp/connect/x/",
        }),
      };
    });
    renderPage();
    const drawer = await openApp(SLATE);
    await userEvent.click(
      within(drawer).getByRole("button", { name: "Connect" }),
    );
    const signIn = await within(drawer).findByRole("button", {
      name: SIGN_IN_BUTTON,
    });
    expect(browser.opened).toEqual([]);
    expect(snackbars.at(-1)).toMatchObject({
      message: `${SLATE_NAME} is added.`,
      options: { variant: "success" },
    });
    await userEvent.click(signIn);
    expect(browser.opened).toEqual([
      `${new URL(HOST_API).origin}${SLATE_SIGN_IN}`,
    ]);
  });

  it("each person, a member too, signs in to their own account of an added app", async () => {
    org.level = 1;
    api.catalog = catalogWith(
      slateApp({ servers: [slateServer({ connect_url: SLATE_SIGN_IN })] }),
    );
    renderPage();
    const row = await screen.findByTestId(`row-${SLATE}`);
    expect(row.textContent).toContain("Not connected");
    const drawer = await openApp(SLATE);
    expect(
      within(drawer).getByText(
        `${SLATE_NAME} is added. Sign in to your own ${SLATE_NAME} account so agents can call it for you.`,
      ),
    ).toBeTruthy();
    expect(drawer.querySelector('input[type="password"]')).toBeNull();
    const signIn = within(drawer).getByRole("button", { name: SIGN_IN_BUTTON });
    expect(signIn.disabled).toBe(false);
    await userEvent.click(signIn);
    expect(browser.opened).toEqual([
      `${new URL(HOST_API).origin}${SLATE_SIGN_IN}`,
    ]);
    expect(api.post).not.toHaveBeenCalled();
  });

  it("the organization's one shared account is signed in by an Owner or Admin only", async () => {
    const server = slateServer({
      credential_mode: "shared",
      connect_url: SLATE_SIGN_IN,
    });
    api.catalog = catalogWith(
      slateApp({ providers: [shared()], servers: [server] }),
    );
    org.level = 1;
    renderPage();
    const drawer = await openApp(SLATE);
    expect(
      within(drawer).getByText(
        `${SLATE_NAME} is added, but an Owner or Admin has not signed in to its account yet, so agents cannot call it.`,
      ),
    ).toBeTruthy();
    expect(
      within(drawer).queryByRole("button", { name: SIGN_IN_BUTTON }),
    ).toBeNull();
    expect(browser.opened).toEqual([]);
  });

  it("an Owner or Admin signs in to the organization's shared account", async () => {
    const server = slateServer({
      credential_mode: "shared",
      connect_url: SLATE_SIGN_IN,
    });
    api.catalog = catalogWith(
      slateApp({ providers: [shared()], servers: [server] }),
    );
    renderPage();
    const drawer = await openApp(SLATE);
    expect(
      within(drawer).getByText(
        `${SLATE_NAME} is added. Sign in to the organization's ${SLATE_NAME} account so agents can call it.`,
      ),
    ).toBeTruthy();
    await userEvent.click(
      within(drawer).getByRole("button", { name: SIGN_IN_BUTTON }),
    );
    expect(browser.opened).toEqual([
      `${new URL(HOST_API).origin}${SLATE_SIGN_IN}`,
    ]);
  });

  it("offers the account mode when the catalog offers both, and adds in the one chosen", async () => {
    const provider = slateProvider();
    provider.account_modes = ["agent", "user"];
    api.catalog = catalogWith(slateApp({ providers: [provider] }));
    api.post = vi.fn(async () => ({ data: slateAdded() }));
    renderPage();
    const drawer = await openApp(SLATE);
    const modes = within(drawer).getByRole("radiogroup", { name: "Accounts" });
    expect(
      within(modes).getByRole("radio", {
        name: "Each person signs in with their own account",
      }).checked,
    ).toBe(true);
    await userEvent.click(
      within(modes).getByRole("radio", {
        name: "One account for the organization, signed in by an Owner or Admin",
      }),
    );
    await userEvent.click(
      within(drawer).getByRole("button", { name: "Connect" }),
    );
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(add(SLATE), {
        provider: "slate-oauth",
        mode: "agent",
        url_variables: {},
      }),
    );
  });

  it("draws no sign-in for a URL that is not on the API's own sign-in path", async () => {
    api.catalog = catalogWith(
      slateApp({
        servers: [
          slateServer({
            connect_url: "https://other.example.test/agentcc/mcp/connect/x/",
          }),
        ],
      }),
    );
    renderPage();
    const drawer = await openApp(SLATE);
    expect(
      within(drawer).queryByRole("button", { name: SIGN_IN_BUTTON }),
    ).toBeNull();
    expect(
      within(drawer).getByText(
        `${SLATE_NAME} is added, but this deployment cannot connect its account, so agents cannot call it. Disconnect it to remove it.`,
      ),
    ).toBeTruthy();
  });

  it("a member cannot add it", async () => {
    org.level = 1;
    api.catalog = catalogWith(slateApp());
    renderPage();
    const drawer = await openApp(SLATE);
    expect(
      within(drawer).getByRole("button", { name: "Connect" }).disabled,
    ).toBe(true);
    expect(
      within(drawer).queryByRole("button", { name: SIGN_IN_BUTTON }),
    ).toBeNull();
  });
});

// The accounts on an added sign-in app this user may see: their own, other
// members' (for an Owner or Admin), and the organization's shared one. Each
// is disconnected where the API names a disconnect this user may follow.
describe("connected accounts", () => {
  const ORIGIN = new URL(HOST_API).origin;
  const ben = slateAccount({
    id: "6f1c2a9e-0000-4000-8000-000000000002",
    label: "ben@slate-mail.example.com",
    mine: false,
    disconnect_url: undefined,
  });

  function accountsRegion(drawer) {
    return within(drawer).getByRole("region", { name: "Accounts" });
  }

  it("lists this user's account and other members', and disconnects only this user's own", async () => {
    api.catalog = catalogWith(
      slateApp({
        servers: [
          slateServer({
            connected: true,
            connect_url: SLATE_SIGN_IN,
            accounts: [slateAccount(), ben],
          }),
        ],
      }),
    );
    api.remove = vi.fn(async () => {
      api.catalog = catalogWith(
        slateApp({
          servers: [
            slateServer({ connect_url: SLATE_SIGN_IN, accounts: [ben] }),
          ],
        }),
      );
      return { data: { status: true, result: { gateway_synced: true } } };
    });
    renderPage();
    const drawer = await openApp(SLATE);
    const accounts = accountsRegion(drawer);
    const mine = within(accounts)
      .getByText("ana@slate-mail.example.com")
      .closest("li");
    expect(mine.textContent).toContain("Your account");
    const theirs = within(accounts)
      .getByText("ben@slate-mail.example.com")
      .closest("li");
    expect(theirs.textContent).toContain("A member's account");
    expect(within(theirs).queryByRole("button")).toBeNull();

    await userEvent.click(
      within(mine).getByRole("button", {
        name: "Disconnect ana@slate-mail.example.com",
      }),
    );
    const confirm = (
      await screen.findByText(`Disconnect your ${SLATE_NAME} account?`)
    ).closest('[role="dialog"]');
    await userEvent.click(
      within(confirm).getByRole("button", { name: "Disconnect" }),
    );
    await waitFor(() =>
      expect(api.remove).toHaveBeenCalledWith(`${ORIGIN}${SLATE_DISCONNECT}`),
    );
    expect(api.remove).toHaveBeenCalledTimes(1);
    await waitFor(() =>
      expect(
        within(drawer).queryByText("ana@slate-mail.example.com"),
      ).toBeNull(),
    );
    expect(snackbars.at(-1)).toMatchObject({
      message: `Your ${SLATE_NAME} account is disconnected.`,
      options: { variant: "success" },
    });
    // Once the confirmation has closed, the drawer offers the sign-in again.
    expect(
      await within(drawer).findByRole("button", {
        name: `Sign in to ${SLATE_NAME}`,
      }),
    ).toBeTruthy();
  });

  it("shows the organization's shared account to everyone, and lets only an Owner or Admin disconnect it", async () => {
    const provider = slateProvider();
    provider.account_modes = ["agent"];
    provider.credential = { ...provider.credential, mode: "shared" };
    const sharedAccount = slateAccount({
      scope: "shared",
      label: "team@slate-mail.example.com",
      mine: false,
    });
    const catalog = () =>
      catalogWith(
        slateApp({
          providers: [provider],
          servers: [
            slateServer({
              credential_mode: "shared",
              connected: true,
              accounts: [sharedAccount],
            }),
          ],
        }),
      );
    org.level = 1;
    api.catalog = catalog();
    renderPage();
    const drawer = await openApp(SLATE);
    const row = within(accountsRegion(drawer))
      .getByText("team@slate-mail.example.com")
      .closest("li");
    expect(row.textContent).toContain("Shared by the organization");
    expect(within(row).queryByRole("button")).toBeNull();
  });

  it("an Owner or Admin disconnects the organization's shared account", async () => {
    const provider = slateProvider();
    provider.account_modes = ["agent"];
    provider.credential = { ...provider.credential, mode: "shared" };
    const sharedAccount = slateAccount({
      scope: "shared",
      label: "team@slate-mail.example.com",
      mine: false,
    });
    api.catalog = catalogWith(
      slateApp({
        providers: [provider],
        servers: [
          slateServer({
            credential_mode: "shared",
            connected: true,
            accounts: [sharedAccount],
          }),
        ],
      }),
    );
    api.remove = vi.fn(async () => ({
      data: { status: true, result: { gateway_synced: true } },
    }));
    renderPage();
    const drawer = await openApp(SLATE);
    const row = within(accountsRegion(drawer))
      .getByText("team@slate-mail.example.com")
      .closest("li");
    await userEvent.click(
      within(row).getByRole("button", {
        name: "Disconnect team@slate-mail.example.com",
      }),
    );
    const confirm = (
      await screen.findByText(
        `Disconnect the organization's ${SLATE_NAME} account?`,
      )
    ).closest('[role="dialog"]');
    await userEvent.click(
      within(confirm).getByRole("button", { name: "Disconnect" }),
    );
    await waitFor(() =>
      expect(api.remove).toHaveBeenCalledWith(`${ORIGIN}${SLATE_DISCONNECT}`),
    );
    expect(snackbars.at(-1)).toMatchObject({
      message: `The organization's ${SLATE_NAME} account is disconnected.`,
    });
  });

  it("says when an account needs a new sign-in, and offers it", async () => {
    api.catalog = catalogWith(
      slateApp({
        servers: [
          slateServer({
            connected: false,
            connect_url: SLATE_SIGN_IN,
            accounts: [slateAccount({ status: "needs_reauth" })],
          }),
        ],
      }),
    );
    renderPage();
    const drawer = await openApp(SLATE);
    const row = within(accountsRegion(drawer))
      .getByText("ana@slate-mail.example.com")
      .closest("li");
    expect(row.textContent).toContain("Needs a new sign-in");
    expect(
      within(drawer).getByRole("button", { name: `Sign in to ${SLATE_NAME}` }),
    ).toBeTruthy();
  });

  it("draws no disconnect for a URL that is not on the API's own sign-in path", async () => {
    api.catalog = catalogWith(
      slateApp({
        servers: [
          slateServer({
            connected: true,
            accounts: [
              slateAccount({
                disconnect_url:
                  "https://other.example.test/agentcc/mcp/connect/x/",
              }),
            ],
          }),
        ],
      }),
    );
    renderPage();
    const drawer = await openApp(SLATE);
    const row = within(accountsRegion(drawer))
      .getByText("ana@slate-mail.example.com")
      .closest("li");
    expect(within(row).queryByRole("button")).toBeNull();
  });
});

// Access control for a sign-in app: who connects it, whose account the
// gateway calls it as, and what it does with each tool (read-only tools are
// allowed; tools that change or delete data are blocked).
describe("access control of a sign-in app", () => {
  function accessControl(drawer) {
    return within(drawer).getByRole("region", { name: "Access control" });
  }

  function rowText(region, label) {
    return within(region).getByText(label).parentElement.textContent;
  }

  it("says each person signs in with their own account", async () => {
    api.catalog = catalogWith(slateApp());
    renderPage();
    const region = accessControl(await openApp(SLATE));
    expect(rowText(region, "Who can connect")).toContain(
      "Owners and Admins add it. Each person then signs in with their own account.",
    );
    expect(rowText(region, "Account")).toContain("Each person's own account");
  });

  it("says the organization shares one account", async () => {
    const provider = slateProvider();
    provider.account_modes = ["agent"];
    provider.credential = { ...provider.credential, mode: "shared" };
    api.catalog = catalogWith(slateApp({ providers: [provider] }));
    renderPage();
    const region = accessControl(await openApp(SLATE));
    expect(rowText(region, "Who can connect")).toContain(
      "Owners and Admins of this organization",
    );
    expect(rowText(region, "Account")).toContain(
      "One shared account for the organization",
    );
  });

  it("names the gateway's rule for tools the catalog does not declare", async () => {
    const provider = slateProvider();
    provider.tools = [];
    api.catalog = catalogWith(slateApp({ providers: [provider] }));
    renderPage();
    const region = accessControl(await openApp(SLATE));
    expect(rowText(region, "Tools")).toContain(
      "Read-only tools are allowed. Tools that change or delete data are blocked.",
    );
  });

  it("shows each tool the app listed when it was added, with the gateway's rule for it", async () => {
    api.catalog = catalogWith(
      slateApp({
        servers: [
          slateServer({
            connected: true,
            tools: [
              { name: "list_threads", gateway_posture: "allow", reason: null },
              { name: "search_mail", gateway_posture: "allow", reason: null },
              {
                name: "delete_thread",
                gateway_posture: "deny",
                reason: "destructive_tool",
              },
            ],
          }),
        ],
      }),
    );
    renderPage();
    const region = accessControl(await openApp(SLATE));
    expect(rowText(region, "Tools")).toContain("2 of 3 tools allowed");
    const items = within(region).getAllByRole("listitem");
    expect(
      items.map((item) => item.querySelector("p, span")?.textContent),
    ).toEqual(["list_threads", "search_mail", "delete_thread"]);
    const deleted = items[2];
    expect(deleted.textContent).toContain("Blocked");
    expect(deleted.textContent).toContain(
      "Blocked: it can delete or overwrite data.",
    );
    expect(within(region).queryByText("send_mail")).toBeNull();
    expect(
      within(region).getByText(
        `These are the tools ${SLATE_NAME} listed when it was added, and the gateway's rule for each, set on the app's virtual server. A later edit to that virtual server is not shown here.`,
      ),
    ).toBeTruthy();
    // The grid's count agrees with the drawer's.
    const row = screen.getByTestId(`row-${SLATE}`);
    expect(row.querySelector('[data-col="tools"]').textContent).toBe("2 of 3");
  });
});

// A link that names one app (?app=<id>), as the page a sign-in returns to or
// the link an agent's connect prompt carries, opens that app's drawer.
describe("a link to one app", () => {
  afterEach(() => window.history.replaceState({}, "", "/"));

  it("opens the app the link names, and closing it leaves the page's own address", async () => {
    window.history.replaceState({}, "", `/integrations?app=${SLATE}`);
    api.catalog = catalogWith(
      slateApp({ servers: [slateServer({ connect_url: SLATE_SIGN_IN })] }),
    );
    renderPage();
    const drawer = await screen.findByRole("dialog");
    expect(within(drawer).getByText(SLATE_NAME)).toBeTruthy();
    expect(
      within(drawer).getByRole("button", { name: `Sign in to ${SLATE_NAME}` }),
    ).toBeTruthy();
    await userEvent.click(
      within(drawer).getByRole("button", { name: "Close" }),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(window.location.search).toBe("");
  });

  it("opens nothing for an app the page does not list", async () => {
    window.history.replaceState({}, "", "/integrations?app=no-such-app");
    renderPage();
    await screen.findByTestId("row-acme-docs");
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
