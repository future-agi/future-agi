import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HelmetProvider } from "react-helmet-async";
import { beforeEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";

import mixed from "../__fixtures__/catalog-acme-connected.json";
import acmeNoKey from "../__fixtures__/catalog-acme-added-no-key.json";
import addAcme from "../__fixtures__/add-acme-docs.json";
import removed from "../__fixtures__/remove-iris-code.json";
import {
  catalogWith,
  slateApp,
  slateProvider,
  SLATE,
} from "../__fixtures__/sign-in";
import IntegrationsSection from "../IntegrationsSection";

// Connecting an app, or saving its OAuth app, while MCP is off for the
// organization turns MCP on for it (one server rule for the key add, the
// sign-in add and the OAuth app save). The drawer says so before the click,
// from the organization's saved MCP setting (the gateway config the MCP page
// reads). The action waits while that setting loads. When it cannot be read,
// an Owner or Admin keeps the ordinary action and is told that this click (the
// connect, or the OAuth app save) turns MCP on if MCP is off; anyone else who
// sees Connect reads nothing about MCP, as when the setting is read.
const KEY = "test-turn-on-typed-key-0001";
const CODE = "connector_mcp_turn_on_not_allowed";
const CONNECT_NOTICE =
  "Adding this connector turns MCP on for the organization.";
const SAVE_NOTICE = "Saving this OAuth app turns MCP on for the organization.";
// The server's refusal, shown when it answers with CODE.
const REFUSED =
  "MCP is off for this organization. Ask an owner or admin to turn it on.";
const MAY_TURN_ON =
  "If MCP is off for this organization, connecting turns it on.";
const SAVE_MAY_TURN_ON =
  "If MCP is off for this organization, saving this OAuth app turns it on.";
const ADMINS = [
  ["an Owner", 15],
  ["an Admin", 8],
];
const OTHERS = [
  ["a Member", 3],
  ["a Viewer", 1],
];
const OAUTH_ROOT = `/agentcc/connectors/apps/${SLATE}/oauth/`;
const add = (appId) => `/agentcc/connectors/apps/${appId}/add/`;
const activates = (n) =>
  `Turning MCP on also activates the ${n} ${n === 1 ? "server" : "servers"} already saved.`;

const { api, org, snackbars, gatewayConfig } = vi.hoisted(() => ({
  api: { catalog: null, oauth: null, post: null, remove: null },
  org: { level: 8 },
  snackbars: [],
  gatewayConfig: { current: null },
}));

vi.mock("src/utils/axios", () => ({
  default: {
    get: async (url) =>
      url === "/agentcc/connectors/apps/slate-mail/oauth/"
        ? { data: { result: api.oauth } }
        : { data: api.catalog },
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
// The organization's saved gateway config, as the MCP page reads it.
vi.mock(
  "src/sections/gateway/providers/hooks/useGatewayConfig",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useGatewayConfig: () => gatewayConfig.current,
  }),
);
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
        client_oauth: false,
        k2_apps: false,
      },
    },
    isPending: false,
    isError: false,
  }),
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({
    orgLevel: org.level,
    currentOrganizationId: "org-a",
    selectedOrganization: { id: "org-a" },
  }),
}));
vi.mock("ag-grid-react", () => import("./ag-grid-mock"));
vi.mock("src/hooks/use-ag-theme", () => ({ useAgThemeWith: () => ({}) }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("notistack", () => ({
  enqueueSnackbar: (message, options) => snackbars.push({ message, options }),
}));

const clone = (value) => JSON.parse(JSON.stringify(value));
const saved = (mcp) => ({ data: { mcp }, isError: false, isPending: false });
const LOADING = { data: undefined, isError: false, isPending: true };
const FAILED = {
  data: undefined,
  isError: true,
  isPending: false,
  error: new Error("down"),
};
// `count` saved servers the turn-on activates, and one that stays off.
function withServers(count, mcp = { enabled: false }) {
  const servers = {
    paused: { url: "https://p.example.com/mcp", enabled: false },
  };
  for (let i = 0; i < count; i += 1)
    servers[`saved-${i}`] = { url: `https://s${i}.example.com/mcp` };
  return saved({ ...mcp, servers });
}
const OFF_STATES = [
  ["never set", saved({})],
  ["set off", saved({ enabled: false })],
];

let client;

function renderPage() {
  client = new QueryClient({
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

function refreshedKeys(spy) {
  return spy.mock.calls.map(([filters]) => JSON.stringify(filters?.queryKey));
}

beforeEach(() => {
  org.level = 8;
  snackbars.length = 0;
  gatewayConfig.current = saved({ enabled: true });
  api.catalog = catalogWith(slateApp());
  api.oauth = null;
  api.remove = vi.fn();
  api.post = vi.fn(async (url) => {
    if (url === add("acme-docs")) {
      api.catalog = clone(mixed);
      return { data: clone(addAcme) };
    }
    throw new Error(`unexpected POST ${url}`);
  });
});

describe("connecting a key app", () => {
  it.each(OFF_STATES)(
    "with MCP %s, says the add turns MCP on and names that on the button",
    async (_, state) => {
      gatewayConfig.current = state;
      renderPage();
      const drawer = await openApp("acme-docs");
      expect(within(drawer).getByText(CONNECT_NOTICE)).toBeInTheDocument();
      expect(within(drawer).queryByText(/already saved/)).toBeNull();
      expect(within(drawer).queryByText(MAY_TURN_ON)).toBeNull();
      expect(
        within(drawer).queryByRole("button", { name: "Connect" }),
      ).toBeNull();
      const button = within(drawer).getByRole("button", {
        name: "Connect and turn on MCP",
      });
      expect(button).toBeEnabled();

      await userEvent.type(within(drawer).getByLabelText("API key"), KEY);
      await userEvent.click(button);
      await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
      // The request is the ordinary add: the server rule turns MCP on.
      expect(api.post.mock.calls[0]).toEqual([
        add("acme-docs"),
        { provider: "acme-mcp", url_variables: {}, credential: { value: KEY } },
      ]);
    },
  );

  it.each([
    [1, { enabled: false }],
    [3, { enabled: false }],
    [2, {}],
  ])(
    "counts the %s saved servers the turn-on activates",
    async (count, mcp) => {
      gatewayConfig.current = withServers(count, mcp);
      renderPage();
      const drawer = await openApp("acme-docs");
      expect(within(drawer).getByText(CONNECT_NOTICE)).toBeInTheDocument();
      expect(within(drawer).getByText(activates(count))).toBeInTheDocument();
    },
  );

  it("with MCP on, keeps Connect and says nothing about MCP", async () => {
    gatewayConfig.current = withServers(2, { enabled: true });
    renderPage();
    const drawer = await openApp("acme-docs");
    expect(
      within(drawer).getByRole("button", { name: "Connect" }),
    ).toBeEnabled();
    expect(within(drawer).queryByText(CONNECT_NOTICE)).toBeNull();
    expect(within(drawer).queryByText(/already saved/)).toBeNull();
    expect(within(drawer).queryByText(MAY_TURN_ON)).toBeNull();
    expect(
      within(drawer).queryByRole("button", { name: "Connect and turn on MCP" }),
    ).toBeNull();
  });

  it("keeps Connect waiting while the MCP setting loads, and posts nothing", async () => {
    gatewayConfig.current = LOADING;
    renderPage();
    const drawer = await openApp("acme-docs");
    const button = within(drawer).getByRole("button", { name: "Connect" });
    expect(button).toBeDisabled();
    expect(within(drawer).queryByText(CONNECT_NOTICE)).toBeNull();
    expect(within(drawer).queryByText(MAY_TURN_ON)).toBeNull();
    await userEvent.type(within(drawer).getByLabelText("API key"), KEY);
    // Enter in the key field submits the form, whatever the button shows.
    fireEvent.submit(button.closest("form"));
    expect(api.post).not.toHaveBeenCalled();
  });

  it.each(ADMINS)(
    "for %s, while the MCP setting cannot be read, keeps Connect, says a connect turns MCP on if it is off, and connects",
    async (_, level) => {
      org.level = level;
      gatewayConfig.current = FAILED;
      renderPage();
      const drawer = await openApp("acme-docs");
      const button = within(drawer).getByRole("button", { name: "Connect" });
      expect(button).toBeEnabled();
      expect(button).toHaveAttribute("aria-describedby");
      expect(
        document.getElementById(button.getAttribute("aria-describedby")),
      ).toHaveTextContent(MAY_TURN_ON);
      expect(within(drawer).queryByText(SAVE_MAY_TURN_ON)).toBeNull();
      expect(within(drawer).queryByText(CONNECT_NOTICE)).toBeNull();
      expect(within(drawer).queryByText(REFUSED)).toBeNull();
      expect(
        within(drawer).queryByRole("button", {
          name: "Connect and turn on MCP",
        }),
      ).toBeNull();

      await userEvent.type(within(drawer).getByLabelText("API key"), KEY);
      await userEvent.click(button);
      await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
      expect(api.post.mock.calls[0]).toEqual([
        add("acme-docs"),
        { provider: "acme-mcp", url_variables: {}, credential: { value: KEY } },
      ]);
    },
  );

  it.each(OTHERS)(
    "for %s, while the MCP setting cannot be read, keeps the Owner or Admin line and says nothing about MCP",
    async (_, level) => {
      org.level = level;
      gatewayConfig.current = FAILED;
      renderPage();
      const drawer = await openApp("acme-docs");
      expect(
        within(drawer).getByText(
          "Only an organization Owner or Admin can connect apps.",
        ),
      ).toBeInTheDocument();
      const button = within(drawer).getByRole("button", { name: "Connect" });
      expect(button).toBeDisabled();
      expect(button).not.toHaveAttribute("aria-describedby");
      expect(within(drawer).queryByText(REFUSED)).toBeNull();
      expect(within(drawer).queryByText(MAY_TURN_ON)).toBeNull();
      expect(within(drawer).queryByText(SAVE_MAY_TURN_ON)).toBeNull();
      expect(within(drawer).queryByText(CONNECT_NOTICE)).toBeNull();
      expect(within(drawer).queryByLabelText("API key")).toBeNull();
      fireEvent.click(button);
      expect(api.post).not.toHaveBeenCalled();
    },
  );

  it.each([
    ["off", saved({ enabled: false })],
    ["on", saved({ enabled: true })],
  ])(
    "for a Member, with the MCP setting read %s, says nothing about MCP, as before",
    async (_, state) => {
      org.level = 3;
      gatewayConfig.current = state;
      renderPage();
      const drawer = await openApp("acme-docs");
      expect(
        within(drawer).getByRole("button", { name: "Connect" }),
      ).toBeDisabled();
      expect(within(drawer).queryByText(REFUSED)).toBeNull();
      expect(within(drawer).queryByText(MAY_TURN_ON)).toBeNull();
      expect(within(drawer).queryByText(CONNECT_NOTICE)).toBeNull();
    },
  );

  it("shows the refusal for someone who may not turn MCP on", async () => {
    gatewayConfig.current = saved({ enabled: false });
    api.post = vi.fn(async () => {
      throw { statusCode: 403, code: CODE, message: "Refused." };
    });
    renderPage();
    const drawer = await openApp("acme-docs");
    await userEvent.type(within(drawer).getByLabelText("API key"), KEY);
    await userEvent.click(
      within(drawer).getByRole("button", { name: "Connect and turn on MCP" }),
    );
    expect(await within(drawer).findByText(REFUSED)).toBeInTheDocument();
    expect(within(drawer).queryByText("Refused.")).toBeNull();
  });

  it("re-reads the MCP setting and status after the add", async () => {
    gatewayConfig.current = saved({});
    renderPage();
    const spy = vi.spyOn(client, "invalidateQueries");
    const drawer = await openApp("acme-docs");
    await userEvent.type(within(drawer).getByLabelText("API key"), KEY);
    await userEvent.click(
      within(drawer).getByRole("button", { name: "Connect and turn on MCP" }),
    );
    await waitFor(() =>
      expect(refreshedKeys(spy)).toEqual(
        expect.arrayContaining([
          JSON.stringify(["agentcc-gateway-config"]),
          JSON.stringify(["agentcc-mcp-status"]),
        ]),
      ),
    );
  });
});

// Storing the key of an app added without one never changes MCP.
describe("storing a key", () => {
  it("while the MCP setting cannot be read, keeps Save key with no MCP line to point at", async () => {
    api.catalog = clone(acmeNoKey);
    gatewayConfig.current = FAILED;
    renderPage();
    const drawer = await openApp("acme-docs");
    const button = within(drawer).getByRole("button", { name: "Save key" });
    expect(button).toBeEnabled();
    expect(button).not.toHaveAttribute("aria-describedby");
    expect(within(drawer).queryByText(MAY_TURN_ON)).toBeNull();
    expect(within(drawer).queryByText(CONNECT_NOTICE)).toBeNull();
    expect(within(drawer).queryByText(REFUSED)).toBeNull();
  });
});

describe("an OAuth sign-in add", () => {
  it("with MCP off, says the add turns MCP on and names that on the button", async () => {
    gatewayConfig.current = withServers(1);
    renderPage();
    const drawer = await openApp(SLATE);
    expect(within(drawer).getByText(CONNECT_NOTICE)).toBeInTheDocument();
    expect(within(drawer).getByText(activates(1))).toBeInTheDocument();
    expect(
      within(drawer).getByRole("button", { name: "Connect and turn on MCP" }),
    ).toBeEnabled();
  });

  it("with MCP on, keeps Connect", async () => {
    renderPage();
    const drawer = await openApp(SLATE);
    expect(
      within(drawer).getByRole("button", { name: "Connect" }),
    ).toBeEnabled();
    expect(within(drawer).queryByText(CONNECT_NOTICE)).toBeNull();
  });
});

describe("saving an OAuth app", () => {
  beforeEach(() => {
    const provider = slateProvider({ connect: { configure_oauth: true } });
    provider.account_modes = ["user", "agent"];
    api.catalog = catalogWith(slateApp({ providers: [provider] }));
    api.oauth = {
      configured: false,
      mode: "per_user",
      registration: "dcr",
      has_secret: false,
      redirect_uri: "https://api.example.test/agentcc/mcp/connect/callback/",
      discovery_url: "https://mcp.slate-mail.example.com/mcp",
    };
    api.post = vi.fn(async (url) => ({
      data: {
        result: url.endsWith("discovery/")
          ? {
              issuer: "https://auth.example.test",
              dcr_supported: true,
              pkce_s256: true,
              redirect_uri: api.oauth.redirect_uri,
            }
          : {
              ...api.oauth,
              configured: true,
              mcp_enabled: true,
              mcp_turned_on: true,
            },
      },
    }));
  });

  async function openPanel() {
    renderPage();
    const drawer = await openApp(SLATE);
    await within(drawer).findByRole("heading", { name: "Configure OAuth" });
    expect(await within(drawer).findByLabelText("Redirect URI")).toHaveValue(
      api.oauth.redirect_uri,
    );
    return drawer;
  }

  it.each(OFF_STATES)(
    "with MCP %s, says the save turns MCP on and names that on the button",
    async (_, state) => {
      gatewayConfig.current = state;
      const drawer = await openPanel();
      expect(within(drawer).getByText(SAVE_NOTICE)).toBeInTheDocument();
      expect(within(drawer).queryByText(/already saved/)).toBeNull();
      expect(within(drawer).queryByText(MAY_TURN_ON)).toBeNull();
      expect(
        within(drawer).queryByRole("button", { name: "Save OAuth app" }),
      ).toBeNull();
      expect(
        within(drawer).getByRole("button", { name: "Save and turn on MCP" }),
      ).toBeEnabled();
    },
  );

  it.each([1, 4])(
    "counts the %s saved servers the turn-on activates",
    async (count) => {
      gatewayConfig.current = withServers(count);
      const drawer = await openPanel();
      expect(within(drawer).getByText(SAVE_NOTICE)).toBeInTheDocument();
      expect(within(drawer).getByText(activates(count))).toBeInTheDocument();
    },
  );

  it("with MCP on, keeps Save OAuth app and says nothing about MCP", async () => {
    const drawer = await openPanel();
    expect(
      within(drawer).getByRole("button", { name: "Save OAuth app" }),
    ).toBeEnabled();
    expect(within(drawer).queryByText(SAVE_NOTICE)).toBeNull();
    expect(within(drawer).queryByText(MAY_TURN_ON)).toBeNull();
  });

  it("keeps the save waiting while the MCP setting loads", async () => {
    gatewayConfig.current = LOADING;
    const drawer = await openPanel();
    const button = within(drawer).getByRole("button", {
      name: "Save OAuth app",
    });
    expect(button).toBeDisabled();
    expect(within(drawer).queryByText(MAY_TURN_ON)).toBeNull();
  });

  it.each(ADMINS)(
    "for %s, while the MCP setting cannot be read, keeps Save OAuth app, says a save turns MCP on if it is off, and saves",
    async (_, level) => {
      org.level = level;
      gatewayConfig.current = FAILED;
      const drawer = await openPanel();
      const button = within(drawer).getByRole("button", {
        name: "Save OAuth app",
      });
      expect(button).toBeEnabled();
      expect(button).toHaveAttribute("aria-describedby");
      expect(
        document.getElementById(button.getAttribute("aria-describedby")),
      ).toHaveTextContent(SAVE_MAY_TURN_ON);
      expect(within(drawer).queryByText(MAY_TURN_ON)).toBeNull();
      expect(within(drawer).queryByText(SAVE_NOTICE)).toBeNull();
      expect(within(drawer).queryByText(REFUSED)).toBeNull();
      expect(
        within(drawer).queryByRole("button", { name: "Save and turn on MCP" }),
      ).toBeNull();

      await userEvent.click(button);
      await waitFor(() =>
        expect(api.post.mock.calls.map(([url]) => url)).toContain(OAUTH_ROOT),
      );
    },
  );

  it("shows the refusal sentence, not the generic failure, for someone who may not turn MCP on", async () => {
    gatewayConfig.current = saved({ enabled: false });
    const discovery = api.post;
    api.post = vi.fn(async (url, ...rest) => {
      if (url.endsWith("discovery/")) return discovery(url, ...rest);
      throw { statusCode: 403, code: CODE, message: "Refused." };
    });
    const drawer = await openPanel();
    await userEvent.click(
      within(drawer).getByRole("button", { name: "Save and turn on MCP" }),
    );
    expect(await within(drawer).findByText(REFUSED)).toBeInTheDocument();
    expect(
      within(drawer).queryByText(/OAuth app could not be saved/),
    ).toBeNull();
  });

  it("re-reads the MCP setting and status after the save", async () => {
    gatewayConfig.current = saved({ enabled: false });
    const drawer = await openPanel();
    const spy = vi.spyOn(client, "invalidateQueries");
    await userEvent.click(
      within(drawer).getByRole("button", { name: "Save and turn on MCP" }),
    );
    await waitFor(() =>
      expect(api.post.mock.calls.map(([url]) => url)).toContain(OAUTH_ROOT),
    );
    await waitFor(() =>
      expect(refreshedKeys(spy)).toEqual(
        expect.arrayContaining([
          JSON.stringify(["agentcc-gateway-config"]),
          JSON.stringify(["agentcc-mcp-status"]),
        ]),
      ),
    );
  });
});

// Only a connect and an OAuth app save can turn MCP on, so only they wait
// for the MCP setting and status to be read again.
describe("disconnecting", () => {
  it("settles without waiting for the MCP setting to be read again", async () => {
    api.catalog = clone(mixed);
    // A removal's answer; the drawer reads only gateway_synced from it.
    api.remove = vi.fn(async () => {
      api.catalog = catalogWith(slateApp());
      return { data: clone(removed) };
    });
    renderPage();
    // The MCP setting's re-read never answers.
    const configKey = JSON.stringify(["agentcc-gateway-config"]);
    const invalidate = client.invalidateQueries.bind(client);
    vi.spyOn(client, "invalidateQueries").mockImplementation(
      (filters, ...rest) =>
        JSON.stringify(filters?.queryKey) === configKey
          ? new Promise(() => {})
          : invalidate(filters, ...rest),
    );
    const drawer = await openApp("acme-docs");
    await userEvent.click(
      within(drawer).getByRole("button", { name: "Disconnect" }),
    );
    const confirm = /** @type {HTMLElement} */ (
      (await screen.findByText("Disconnect Acme Docs?")).closest(
        '[role="dialog"]',
      )
    );
    await userEvent.click(
      within(confirm).getByRole("button", { name: "Disconnect" }),
    );
    await waitFor(() =>
      expect(api.remove).toHaveBeenCalledWith(add("acme-docs")),
    );
    await waitFor(() =>
      expect(snackbars.at(-1)).toMatchObject({
        message: "Acme Docs is disconnected.",
        options: { variant: "success" },
      }),
    );
  });
});
