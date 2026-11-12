import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HelmetProvider } from "react-helmet-async";
import { beforeEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen, waitFor, within } from "src/utils/test-utils";

import noneAdded from "../__fixtures__/catalog-none-added.json";
import acmeNoKey from "../__fixtures__/catalog-acme-added-no-key.json";
import mixed from "../__fixtures__/catalog-acme-connected.json";
import addAcme from "../__fixtures__/add-acme-docs.json";
import storedAcme from "../__fixtures__/stored-acme-docs.json";
import boltRefused from "../__fixtures__/add-bolt-crm-refused.json";
import keyRejected from "../__fixtures__/add-acme-docs-key-rejected.json";
import alreadyAdded from "../__fixtures__/add-acme-docs-again-409.json";
import addIris from "../__fixtures__/add-iris-code.json";
import memberRefused from "../__fixtures__/add-member-403.json";
import removedIris from "../__fixtures__/remove-iris-code.json";
import IntegrationsSection from "../IntegrationsSection";

// Every API answer here is CN9's or M14's own response (see __fixtures__).
// An add carries the org's key (CN-ADD): the add lists the provider's tools
// with it and stores it in the vault in the same write, so connecting an app
// is one request; only an app added without its key stores the key alone.
const KEY = "test-cn21-typed-key-0001";
const CREDENTIALS = "/agentcc/mcp/credentials/";
const add = (appId) => `/agentcc/connectors/apps/${appId}/add/`;

const { api, org, snackbars } = vi.hoisted(() => ({
  api: { catalog: null, post: null, remove: null },
  org: { level: 8 },
  snackbars: [],
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
vi.mock("notistack", () => ({
  enqueueSnackbar: (message, options) => snackbars.push({ message, options }),
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: org.level }),
}));

const clone = (value) => JSON.parse(JSON.stringify(value));
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
  const row = await screen.findByTestId(`row-${appId}`);
  await userEvent.click(row);
  return screen.findByRole("dialog");
}

// The key appears nowhere the person or another script can read it back:
// not in the page, not in any field, not in a message, not in the query
// client's caches.
function expectKeyNowhere() {
  expect(document.body.innerHTML).not.toContain(KEY);
  expect(screen.queryByDisplayValue(KEY)).toBeNull();
  expect(JSON.stringify(snackbars)).not.toContain(KEY);
  const caches = [
    ...client
      .getQueryCache()
      .getAll()
      .map((query) => query.state),
    ...client
      .getMutationCache()
      .getAll()
      .map((mutation) => mutation.state),
  ];
  expect(JSON.stringify(caches)).not.toContain(KEY);
}

beforeEach(() => {
  org.level = 8;
  snackbars.length = 0;
  api.catalog = clone(noneAdded);
  api.remove = vi.fn();
  api.post = vi.fn(async (url) => {
    if (url === add("acme-docs")) {
      api.catalog = clone(mixed);
      return { data: clone(addAcme) };
    }
    if (url === CREDENTIALS) {
      api.catalog = clone(mixed);
      return { data: clone(storedAcme) };
    }
    throw new Error(`unexpected POST ${url}`);
  });
});

describe("an API-key connect", () => {
  it("adds the app with its key in one request, and never echoes it", async () => {
    renderPage();
    const drawer = await openApp("acme-docs");
    const field = within(drawer).getByLabelText("API key");
    expect(field.getAttribute("type")).toBe("password");
    expect(field.getAttribute("autocomplete")).toBe("new-password");
    expect(
      within(drawer).getByText("Create a key under Settings, API keys."),
    ).toBeTruthy();
    expect(
      within(drawer)
        .getByRole("link", { name: "Where to find it" })
        .getAttribute("href"),
    ).toBe("https://docs.acme-docs.example.com/keys");

    await userEvent.type(field, KEY);
    await userEvent.click(
      within(drawer).getByRole("button", { name: "Connect" }),
    );

    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
    const [[addUrl, addBody]] = api.post.mock.calls;
    expect(addUrl).toBe(add("acme-docs"));
    // Where the key goes comes from the catalog, never from the request.
    expect(addBody).toEqual({
      provider: "acme-mcp",
      url_variables: {},
      credential: { value: KEY },
    });

    expect(await screen.findByText("Acme Docs is connected.")).toBeTruthy();
    // The add stored the key: no second request to the vault.
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(snackbars.at(-1)).toMatchObject({ options: { variant: "success" } });
    expectKeyNowhere();
  });

  it("refuses a pasted key with a line break before sending anything", async () => {
    renderPage();
    const drawer = await openApp("acme-docs");
    await userEvent.type(within(drawer).getByLabelText("API key"), `${KEY} `);
    await userEvent.click(
      within(drawer).getByRole("button", { name: "Connect" }),
    );
    expect(
      within(drawer).getByText(/Remove the spaces or line breaks/),
    ).toBeTruthy();
    expect(api.post).not.toHaveBeenCalled();
    // A refused key is not kept in the field.
    expect(within(drawer).getByLabelText("API key")).toHaveValue("");
  });

  it("never shows a refusal that repeats the typed key", async () => {
    api.catalog = clone(acmeNoKey);
    api.post = vi.fn(async () => {
      throw {
        statusCode: 400,
        code: "invalid_mcp_credential",
        message: `the vault could not use ${KEY}`,
      };
    });
    renderPage();
    const drawer = await openApp("acme-docs");
    await userEvent.type(within(drawer).getByLabelText("API key"), KEY);
    await userEvent.click(
      within(drawer).getByRole("button", { name: "Save key" }),
    );
    expect(
      await within(drawer).findByText("The server refused the request."),
    ).toBeTruthy();
    expectKeyNowhere();
  });

  it("shows the 409 when the app was added meanwhile", async () => {
    api.post = vi.fn(async () => {
      throw { statusCode: 409, ...clone(alreadyAdded) };
    });
    renderPage();
    const drawer = await openApp("acme-docs");
    await userEvent.type(within(drawer).getByLabelText("API key"), KEY);
    await userEvent.click(
      within(drawer).getByRole("button", { name: "Connect" }),
    );
    expect(await within(drawer).findByText(alreadyAdded.message)).toBeTruthy();
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(api.post.mock.calls[0][0]).toBe(add("acme-docs"));
    expectKeyNowhere();
  });

  it("shows the provider's refusal of the key; nothing is added, and it asks again", async () => {
    // A key the provider refuses when the add lists its tools adds nothing
    // (400 connector_credential_rejected): the form stays an add.
    api.post = vi.fn(async () => {
      throw { statusCode: 400, ...clone(keyRejected) };
    });
    renderPage();
    const drawer = await openApp("acme-docs");
    await userEvent.type(within(drawer).getByLabelText("API key"), KEY);
    await userEvent.click(
      within(drawer).getByRole("button", { name: "Connect" }),
    );
    expect(await within(drawer).findByText(keyRejected.message)).toBeTruthy();
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(api.post.mock.calls[0][0]).toBe(add("acme-docs"));
    expect(
      within(drawer).getByRole("button", { name: "Connect" }),
    ).toBeTruthy();
    expect(within(drawer).getByLabelText("API key")).toHaveValue("");
    expectKeyNowhere();
  });

  it("never shows an add refusal that repeats the typed key", async () => {
    api.post = vi.fn(async () => {
      throw {
        statusCode: 400,
        code: "connector_credential_invalid",
        message: `the provider could not use ${KEY}`,
      };
    });
    renderPage();
    const drawer = await openApp("acme-docs");
    await userEvent.type(within(drawer).getByLabelText("API key"), KEY);
    await userEvent.click(
      within(drawer).getByRole("button", { name: "Connect" }),
    );
    expect(
      await within(drawer).findByText("The server refused the request."),
    ).toBeTruthy();
    expectKeyNowhere();
  });

  it("stores only the key for an app added without one", async () => {
    api.catalog = clone(acmeNoKey);
    renderPage();
    const drawer = await openApp("acme-docs");
    expect(
      within(drawer).getByText(
        "Acme Docs is added, but its key is not stored, so the gateway cannot call it.",
      ),
    ).toBeTruthy();
    await userEvent.type(within(drawer).getByLabelText("API key"), KEY);
    await userEvent.click(
      within(drawer).getByRole("button", { name: "Save key" }),
    );
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
    expect(api.post.mock.calls[0][0]).toBe(CREDENTIALS);
    expect(await screen.findByText("Acme Docs is connected.")).toBeTruthy();
    expectKeyNowhere();
  });
});

describe("a retained app with a retired provider", () => {
  it.each([8, 1])("role=%s gets bounded removal guidance", async (level) => {
    org.level = level;
    api.catalog = clone(acmeNoKey);
    const app = api.catalog.results.find((entry) => entry.id === "acme-docs");
    app.providers = [];
    renderPage();
    const drawer = await openApp("acme-docs");
    expect(
      within(drawer).getByText(
        "Acme Docs uses a provider the catalog no longer offers. An organization Owner or Admin can disconnect it.",
      ),
    ).toBeTruthy();
    const disconnect = within(drawer).getByRole("button", {
      name: "Disconnect",
    });
    expect(disconnect.disabled).toBe(level < 8);
    expect(
      within(drawer).queryByRole("button", { name: "Save key" }),
    ).toBeNull();
    expect(api.post).not.toHaveBeenCalled();
    expect(api.remove).not.toHaveBeenCalled();
  });
});

describe("a token connect", () => {
  it("adds the app with its bearer token in the add, with no header name", async () => {
    api.post = vi.fn(async (url) => {
      if (url === add("iris-code")) return { data: clone(addIris) };
      throw new Error(`unexpected POST ${url}`);
    });
    renderPage();
    const drawer = await openApp("iris-code");
    await userEvent.type(
      within(drawer).getByLabelText("Personal access token"),
      KEY,
    );
    await userEvent.click(
      within(drawer).getByRole("button", { name: "Connect" }),
    );
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
    // The catalog puts a bearer token in Authorization itself.
    expect(api.post.mock.calls[0]).toEqual([
      add("iris-code"),
      { provider: "iris-mcp", url_variables: {}, credential: { value: KEY } },
    ]);
    await waitFor(() =>
      expect(snackbars.at(-1)).toMatchObject({
        message: "Iris Code is connected.",
        options: { variant: "success" },
      }),
    );
    expectKeyNowhere();
  });
});

describe("other connects", () => {
  it("asks for the URL variables the provider declares, and shows the server's refusal", async () => {
    api.post = vi.fn(async () => {
      throw { statusCode: 400, ...clone(boltRefused) };
    });
    renderPage();
    const drawer = await openApp("bolt-crm");
    await userEvent.click(
      within(drawer).getByRole("button", { name: "Connect" }),
    );
    expect(within(drawer).getAllByText("Required.")).toHaveLength(2);
    expect(api.post).not.toHaveBeenCalled();
    await userEvent.type(
      within(drawer).getByLabelText("accountHost"),
      "acme.bolt.example.com",
    );
    await userEvent.type(within(drawer).getByLabelText("workspace"), "sales");
    await userEvent.type(within(drawer).getByLabelText("Access token"), KEY);
    await userEvent.click(
      within(drawer).getByRole("button", { name: "Connect" }),
    );
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
    expect(api.post.mock.calls[0][1]).toEqual({
      provider: "bolt-mcp",
      url_variables: {
        accountHost: "acme.bolt.example.com",
        workspace: "sales",
      },
      credential: { value: KEY },
    });
    expect(await within(drawer).findByText(boltRefused.message)).toBeTruthy();
    expectKeyNowhere();
  });

  it("keeps a valid key while a URL variable is missing, and clears it once sent", async () => {
    api.post = vi.fn(async () => {
      throw { statusCode: 400, ...clone(boltRefused) };
    });
    renderPage();
    const drawer = await openApp("bolt-crm");
    const field = within(drawer).getByLabelText("Access token");
    await userEvent.type(field, KEY);
    await userEvent.click(
      within(drawer).getByRole("button", { name: "Connect" }),
    );
    expect(within(drawer).getAllByText("Required.")).toHaveLength(2);
    expect(api.post).not.toHaveBeenCalled();
    expect(field).toHaveValue(KEY);
    await userEvent.type(
      within(drawer).getByLabelText("accountHost"),
      "acme.bolt.example.com",
    );
    await userEvent.type(within(drawer).getByLabelText("workspace"), "sales");
    await userEvent.click(
      within(drawer).getByRole("button", { name: "Connect" }),
    );
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
    expect(field).toHaveValue("");
    expect(await within(drawer).findByText(boltRefused.message)).toBeTruthy();
    expectKeyNowhere();
  });

  it("connects an app that needs no key in one step", async () => {
    api.post = vi.fn(async () => ({
      data: {
        status: true,
        result: {
          ...clone(addAcme).result,
          app: "cobalt-search",
          credential: { mode: "none" },
        },
      },
    }));
    renderPage();
    const drawer = await openApp("cobalt-search");
    expect(within(drawer).getByText("No key needed")).toBeTruthy();
    expect(within(drawer).queryByLabelText(/key|token/i)).toBeNull();
    await userEvent.click(
      within(drawer).getByRole("button", { name: "Connect" }),
    );
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
    expect(api.post.mock.calls[0]).toEqual([
      add("cobalt-search"),
      { provider: "cobalt-mcp", url_variables: {} },
    ]);
  });

  it("lets the admin pick between an app's providers", async () => {
    renderPage();
    const drawer = await openApp("fern-files");
    const choice = within(drawer).getByRole("radiogroup", {
      name: "Connect with",
    });
    await userEvent.click(
      within(choice).getByRole("radio", { name: /Sent in the server URL/ }),
    );
    expect(within(drawer).getByLabelText("region")).toBeTruthy();
  });
});

describe("who may connect", () => {
  it("a member sees the app, its access and Connect disabled, but no key field", async () => {
    org.level = 1;
    renderPage();
    const drawer = await openApp("acme-docs");
    expect(
      within(drawer).getByText(
        "Only an organization Owner or Admin can connect apps.",
      ),
    ).toBeTruthy();
    expect(
      within(drawer).getByRole("button", { name: "Connect" }),
    ).toBeDisabled();
    expect(within(drawer).queryByLabelText("API key")).toBeNull();
    expect(within(drawer).getByText("search_pages")).toBeTruthy();
  });

  it("shows the server's 403 when the role changed under the page", async () => {
    api.post = vi.fn(async () => {
      throw { statusCode: 403, ...clone(memberRefused) };
    });
    renderPage();
    const drawer = await openApp("cobalt-search");
    await userEvent.click(
      within(drawer).getByRole("button", { name: "Connect" }),
    );
    expect(await within(drawer).findByText(memberRefused.message)).toBeTruthy();
  });
});

describe("access control (read)", () => {
  it("shows the account mode, the virtual server and why each tool is blocked", async () => {
    api.catalog = clone(mixed);
    renderPage();
    const drawer = await openApp("acme-docs");
    const access = within(drawer).getByRole("region", {
      name: "Access control",
    });
    expect(
      within(access).getByText("Owners and Admins of this organization"),
    ).toBeTruthy();
    expect(
      within(access).getByText("One shared account for the organization"),
    ).toBeTruthy();
    expect(within(access).getByText("acme-docs")).toBeTruthy();
    expect(within(access).getByText("2 of 7 tools allowed")).toBeTruthy();
    expect(
      within(access).getByText("Blocked: it can delete or overwrite data."),
    ).toBeTruthy();
    // The API returns the catalog's rules, not the virtual server's rules as
    // they are now, and the page says which.
    expect(
      within(access).getByText(
        /catalog's rules, set on the app's virtual server when it is added\. A later edit to that virtual server is not shown here\./,
      ),
    ).toBeTruthy();
  });
});

describe("facts", () => {
  it("state a vendor status other than GA as plain text, never a badge", async () => {
    const catalog = clone(noneAdded);
    const acme = catalog.results.find((entry) => entry.id === "acme-docs");
    acme.providers[0].vendor_status = "preview";
    api.catalog = catalog;
    renderPage();
    const drawer = await openApp("acme-docs");
    expect(within(drawer).getByText("Vendor status")).toBeTruthy();
    expect(within(drawer).getByText("Preview")).toBeTruthy();
    // The only badge is the quality tier's.
    expect(within(drawer).getByText("Listed")).toBeTruthy();
  });

  it("say nothing about a GA vendor surface", async () => {
    renderPage();
    const drawer = await openApp("acme-docs");
    expect(within(drawer).queryByText("Vendor status")).toBeNull();
  });
});

describe("the app's header", () => {
  it("names the vendor only when it is not the app's own name", async () => {
    renderPage();
    const acme = await openApp("acme-docs");
    expect(within(acme).getAllByText("Acme Docs")).toHaveLength(1);
    await userEvent.click(within(acme).getByRole("button", { name: "Close" }));
    const bolt = await openApp("bolt-crm");
    expect(within(bolt).getByText("Bolt Labs")).toBeTruthy();
  });
});

describe("a turned-off app", () => {
  const OFF_TEXT =
    "Acme Docs is added but turned off in the organization's gateway configuration (mcp.servers.acme-docs.enabled is false), so agents cannot call it.";

  // CN9 reports a server with `enabled: false` in the org config as enabled
  // false and never connected (_org_connectors).
  function turnedOff() {
    const catalog = clone(mixed);
    const acme = catalog.results.find((entry) => entry.id === "acme-docs");
    acme.servers = [{ ...acme.servers[0], enabled: false, connected: false }];
    acme.connected = false;
    return catalog;
  }

  it("names where it was turned off, links no page that cannot turn it on, and turns it on from here", async () => {
    api.catalog = turnedOff();
    api.remove = vi.fn(async () => {
      api.catalog = clone(noneAdded);
      return { data: clone(removedIris) };
    });
    renderPage();
    const drawer = await openApp("acme-docs");
    expect(
      within(drawer).getByText(
        `${OFF_TEXT} To turn it on from here, disconnect it and connect it again with its key.`,
      ),
    ).toBeTruthy();
    expect(within(drawer).getByText("Turned off")).toBeTruthy();
    expect(within(drawer).queryByRole("link", { name: /MCP/ })).toBeNull();
    expect(
      within(drawer).queryByRole("button", { name: "Connect" }),
    ).toBeNull();

    await userEvent.click(
      within(drawer).getByRole("button", { name: "Disconnect" }),
    );
    const confirm = /** @type {HTMLElement} */ (
      (await screen.findByText("Disconnect Acme Docs?")).closest(
        '[role="dialog"]',
      )
    );
    expect(
      within(confirm).getByText(
        "Disconnecting removes the app's servers from the saved configuration and revokes their stored credentials. It removes those servers from virtual-server membership and deletes their empty connector-owned virtual servers. If the gateway does not pick up the change, a warning says so.",
      ),
    ).toBeTruthy();
    await userEvent.click(
      within(confirm).getByRole("button", { name: "Disconnect" }),
    );
    // Not added any more: the connect form is back, with an empty key field.
    expect(
      await within(drawer).findByRole("button", { name: "Connect" }),
    ).toBeTruthy();
    expect(within(drawer).getByLabelText("API key")).toHaveValue("");
  });

  // The server names a provider the catalog no longer offers.
  function retired({ otherProviders }) {
    const catalog = turnedOff();
    const acme = catalog.results.find((entry) => entry.id === "acme-docs");
    acme.servers[0].provider = "acme-retired";
    if (!otherProviders) acme.providers = [];
    return catalog;
  }

  it("with a retired provider and another one offered, says to connect with that one", async () => {
    api.catalog = retired({ otherProviders: true });
    renderPage();
    const drawer = await openApp("acme-docs");
    expect(
      within(drawer).getByText(
        `${OFF_TEXT} It was added with a provider the catalog no longer offers: disconnect it, then connect it with another provider.`,
      ),
    ).toBeTruthy();
  });

  it("with a retired provider and none other offered, never promises a connect", async () => {
    api.catalog = retired({ otherProviders: false });
    renderPage();
    const drawer = await openApp("acme-docs");
    expect(
      within(drawer).getByText(
        `${OFF_TEXT} It was added with a provider the catalog no longer offers: disconnect it to remove it.`,
      ),
    ).toBeTruthy();
    expect(
      within(drawer).getByRole("button", { name: "Disconnect" }),
    ).toBeTruthy();
  });

  it("a member is told where it was turned off, and Disconnect is disabled", async () => {
    org.level = 1;
    api.catalog = turnedOff();
    renderPage();
    const drawer = await openApp("acme-docs");
    expect(within(drawer).getByText(OFF_TEXT)).toBeTruthy();
    expect(
      within(drawer).getByRole("button", { name: "Disconnect" }),
    ).toBeDisabled();
    expect(
      within(drawer).queryByRole("button", { name: "Connect" }),
    ).toBeNull();
  });
});

describe("disconnect", () => {
  it("removes the app after confirmation", async () => {
    api.catalog = clone(mixed);
    api.remove = vi.fn(async () => {
      api.catalog = clone(noneAdded);
      return { data: clone(removedIris) };
    });
    renderPage();
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
    expect(snackbars.at(-1)).toMatchObject({
      message: "Acme Docs is disconnected.",
      options: { variant: "success" },
    });
  });
});
