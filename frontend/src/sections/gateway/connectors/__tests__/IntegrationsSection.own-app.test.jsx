import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HelmetProvider } from "react-helmet-async";
import { beforeEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen, waitFor, within } from "src/utils/test-utils";

import {
  SLATE,
  SLATE_NAME,
  SLATE_REDIRECT_URI,
  catalogWith,
  clientsPage,
  slateApp,
  slateClient,
  slateOwnAppProvider,
  slateProvider,
} from "../__fixtures__/sign-in";
import IntegrationsSection from "../IntegrationsSection";

// "Use your own OAuth app" (CN11): an Owner or Admin stores the org's own
// OAuth client for a sign-in app, and the gateway signs people in through
// it. The client secret is write-only. Catalog answers are hand-written to
// connect-flow.js (__fixtures__/sign-in.js); client answers follow CN11's
// describe_oauth_client.
const CATALOG = "/agentcc/connectors/catalog/";
const CLIENTS = "/agentcc/connectors/oauth-clients/";
const client = (id) => `${CLIENTS}${id}/`;
const SECRET = "test-cn25-client-secret-0001";
const SECTION = "Your own OAuth app";

const { api, org, snackbars, gateway } = vi.hoisted(() => ({
  api: { catalog: null, clients: [], post: null, remove: null },
  org: { level: 8, upstreamOAuth: true },
  snackbars: [],
  gateway: {},
}));

vi.mock("src/utils/axios", () => ({
  default: {
    get: async (url) => {
      if (url === "/agentcc/connectors/oauth-clients/") {
        return { data: JSON.parse(JSON.stringify(api.clientsPage())) };
      }
      return { data: api.catalog };
    },
    post: (...args) => api.post(...args),
    delete: (...args) => api.remove(...args),
  },
  endpoints: { gateway },
}));
vi.mock("../connect-flow", async (importOriginal) => ({
  ...(await importOriginal()),
  openSignIn: () => {},
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
        upstream_oauth: org.upstreamOAuth,
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

let queryClient;

function renderPage() {
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <HelmetProvider>
      <QueryClientProvider client={queryClient}>
        <IntegrationsSection />
      </QueryClientProvider>
    </HelmetProvider>,
  );
}

async function openApp(appId) {
  await userEvent.click(await screen.findByTestId(`row-${appId}`));
  return screen.findByRole("dialog");
}

// The secret appears nowhere the person or another script can read it back.
function expectSecretNowhere() {
  expect(document.body.innerHTML).not.toContain(SECRET);
  expect(screen.queryByDisplayValue(SECRET)).toBeNull();
  expect(JSON.stringify(snackbars)).not.toContain(SECRET);
  const caches = [
    ...queryClient
      .getQueryCache()
      .getAll()
      .map((query) => query.state),
    ...queryClient
      .getMutationCache()
      .getAll()
      .map((mutation) => mutation.state),
  ];
  expect(JSON.stringify(caches)).not.toContain(SECRET);
}

beforeEach(() => {
  org.level = 8;
  org.upstreamOAuth = true;
  snackbars.length = 0;
  for (const key of Object.keys(gateway)) delete gateway[key];
  Object.assign(gateway, {
    connectorCatalog: CATALOG,
    connectorAdd: (appId) => `/agentcc/connectors/apps/${appId}/add/`,
    mcpCredentials: "/agentcc/mcp/credentials/",
    connectorOAuthClients: CLIENTS,
    connectorOAuthClient: client,
  });
  api.catalog = catalogWith(slateApp({ providers: [slateOwnAppProvider()] }));
  api.clients = [];
  api.clientsPage = () => clientsPage(api.clients);
  api.post = vi.fn(async (url) => {
    throw new Error(`unexpected POST ${url}`);
  });
  api.remove = vi.fn(async (url) => {
    throw new Error(`unexpected DELETE ${url}`);
  });
});

describe("using the organization's own OAuth app", () => {
  it("shows the redirect URI to register, stores the client with its secret, and never shows the secret again", async () => {
    api.post = vi.fn(async (url) => {
      if (url !== CLIENTS) throw new Error(`unexpected POST ${url}`);
      api.clients = [slateClient()];
      return { data: { status: true, result: slateClient() } };
    });
    renderPage();
    const drawer = await openApp(SLATE);
    const section = await within(drawer).findByRole("region", {
      name: SECTION,
    });
    expect(within(section).getByText(SLATE_REDIRECT_URI)).toBeTruthy();
    const secret = within(section).getByLabelText("Client secret");
    expect(secret.getAttribute("type")).toBe("password");
    expect(secret.getAttribute("autocomplete")).toBe("new-password");
    await userEvent.type(
      within(section).getByLabelText("Client ID"),
      "slate-client-1",
    );
    await userEvent.type(secret, SECRET);
    await userEvent.click(
      within(section).getByRole("button", { name: "Save" }),
    );
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(CLIENTS, {
        client_group: "slate",
        client_id: "slate-client-1",
        client_secret: SECRET,
      }),
    );
    expect(await within(section).findByText("slate-client-1")).toBeTruthy();
    expect(within(section).queryByLabelText("Client secret")).toBeNull();
    expect(snackbars.at(-1)).toMatchObject({
      message: `${SLATE_NAME} signs in through your OAuth app.`,
      options: { variant: "success" },
    });
    expectSecretNowhere();
  });

  it("removes the stored client after a confirmation", async () => {
    api.clients = [slateClient()];
    api.remove = vi.fn(async (url) => {
      if (url !== client(slateClient().id))
        throw new Error(`unexpected DELETE ${url}`);
      api.clients = [];
      return {
        data: { status: true, result: slateClient({ status: "revoked" }) },
      };
    });
    renderPage();
    const drawer = await openApp(SLATE);
    const section = await within(drawer).findByRole("region", {
      name: SECTION,
    });
    await within(section).findByText("slate-client-1");
    await userEvent.click(
      within(section).getByRole("button", { name: "Remove" }),
    );
    const confirm = (
      await screen.findByText(`Remove your OAuth app for ${SLATE_NAME}?`)
    ).closest('[role="dialog"]');
    await userEvent.click(
      within(confirm).getByRole("button", { name: "Remove" }),
    );
    await waitFor(() =>
      expect(api.remove).toHaveBeenCalledWith(client(slateClient().id)),
    );
    // Once the confirmation has closed, the form is back.
    expect(await within(drawer).findByLabelText("Client secret")).toBeTruthy();
  });

  it("never shows a refusal that repeats the secret, and clears the field", async () => {
    api.post = vi.fn(async () => {
      throw new Error(`client_secret ${SECRET} is not accepted`);
    });
    renderPage();
    const drawer = await openApp(SLATE);
    const section = await within(drawer).findByRole("region", {
      name: SECTION,
    });
    await userEvent.type(
      within(section).getByLabelText("Client ID"),
      "slate-client-1",
    );
    await userEvent.type(
      within(section).getByLabelText("Client secret"),
      SECRET,
    );
    await userEvent.click(
      within(section).getByRole("button", { name: "Save" }),
    );
    expect(
      await within(section).findByText("The server refused the request."),
    ).toBeTruthy();
    expect(within(section).getByLabelText("Client secret").value).toBe("");
    expectSecretNowhere();
  });

  it("holds Connect until the org's own app is stored, where only it can serve the sign-in", async () => {
    api.catalog = catalogWith(
      slateApp({ providers: [slateOwnAppProvider({ required: true })] }),
    );
    api.post = vi.fn(async () => {
      api.clients = [slateClient()];
      return { data: { status: true, result: slateClient() } };
    });
    renderPage();
    const drawer = await openApp(SLATE);
    const section = await within(drawer).findByRole("region", {
      name: SECTION,
    });
    expect(
      await within(drawer).findByText(
        `${SLATE_NAME} signs in only through your organization's own OAuth app. Add it below to connect.`,
      ),
    ).toBeTruthy();
    expect(
      within(drawer).getByRole("button", { name: "Connect" }).disabled,
    ).toBe(true);
    await userEvent.type(
      within(section).getByLabelText("Client ID"),
      "slate-client-1",
    );
    await userEvent.type(
      within(section).getByLabelText("Client secret"),
      SECRET,
    );
    await userEvent.click(
      within(section).getByRole("button", { name: "Save" }),
    );
    await waitFor(() =>
      expect(
        within(drawer).getByRole("button", { name: "Connect" }).disabled,
      ).toBe(false),
    );
  });
});

describe("where the organization's own OAuth app is not offered", () => {
  it("draws nothing of it for a sign-in the catalog does not offer it for", async () => {
    api.catalog = catalogWith(slateApp({ providers: [slateProvider()] }));
    renderPage();
    const drawer = await openApp(SLATE);
    expect(within(drawer).queryByRole("region", { name: SECTION })).toBeNull();
    expect(drawer.textContent).not.toMatch(/own OAuth app|client secret/i);
  });

  it("draws nothing of it where the console has no route for it", async () => {
    delete gateway.connectorOAuthClients;
    delete gateway.connectorOAuthClient;
    renderPage();
    const drawer = await openApp(SLATE);
    expect(within(drawer).queryByRole("region", { name: SECTION })).toBeNull();
    expect(drawer.textContent).not.toMatch(/own OAuth app|client secret/i);
  });

  it("lists no sign-in that only the org's own app can serve where the console cannot store one", async () => {
    delete gateway.connectorOAuthClients;
    delete gateway.connectorOAuthClient;
    api.catalog = catalogWith(
      slateApp({ providers: [slateOwnAppProvider({ required: true })] }),
    );
    renderPage();
    await screen.findByTestId("row-acme-docs");
    expect(screen.queryByTestId(`row-${SLATE}`)).toBeNull();
  });

  it("draws nothing of it for a member, who may not read the org's clients", async () => {
    org.level = 1;
    const get = vi.fn(api.clientsPage);
    api.clientsPage = get;
    renderPage();
    const drawer = await openApp(SLATE);
    expect(within(drawer).queryByRole("region", { name: SECTION })).toBeNull();
    expect(get).not.toHaveBeenCalled();
  });
});

it("static credentials never expose an OAuth registration control when upstream OAuth is OFF", async () => {
  org.upstreamOAuth = false;
  const provider = {
    ...slateOwnAppProvider(),
    credential: { mode: "shared", kind: "bearer" },
  };
  api.catalog = catalogWith(slateApp({ providers: [provider], servers: [] }));
  renderPage();
  const drawer = await openApp(SLATE);
  expect(within(drawer).queryByText(SECTION)).toBeNull();
  expect(within(drawer).queryByText(/OAuth app/i)).toBeNull();
});
