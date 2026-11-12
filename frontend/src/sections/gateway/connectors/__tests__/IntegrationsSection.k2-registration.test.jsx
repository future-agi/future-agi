import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HelmetProvider } from "react-helmet-async";
import { beforeEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen, waitFor, within } from "src/utils/test-utils";

import {
  SLATE_REDIRECT_URI,
  catalogWith,
  clientsPage,
  slateApp,
  slateClient,
  slateProvider,
} from "../__fixtures__/sign-in";
import {
  ISSUES,
  connection,
  listOf,
  notStartable,
} from "../__fixtures__/my-connections";
import IntegrationsSection from "../IntegrationsSection";
import { openSignIn } from "../connect-flow";

// K2 sign-in needs the organization's own app registration (W4b, G14b):
// FutureAGI's own Gmail and Outlook connectors sign people in only through
// the organization's own Google or Microsoft OAuth app, and the console says
// so where Connect waits for it, in "Your accounts", and when a start is
// refused. Catalog cards are hand-written to connect-flow.js's contract
// (__fixtures__/sign-in.js); account rows to the accounts view's serializer
// (__fixtures__/my-connections.js), with the reason the API adds for K2.
const CATALOG = "/agentcc/connectors/catalog/";
const CLIENTS = "/agentcc/connectors/oauth-clients/";
const SECRET = "test-k2-client-secret-0001";
const SECTION = "Your own OAuth app";

const { api, org, gateway } = vi.hoisted(() => ({
  api: { catalog: null, clients: [], list: null, post: null },
  org: { level: 8, k2Apps: true },
  gateway: {},
}));

vi.mock("src/utils/axios", () => ({
  default: {
    get: async (url) => {
      if (url === "/agentcc/connectors/oauth-clients/") {
        return { data: clientsPage(api.clients) };
      }
      if (url === "/agentcc/mcp/connect/") return { data: api.list };
      return { data: api.catalog };
    },
    post: (...args) => api.post(...args),
    delete: async () => ({ data: { status: true, result: {} } }),
  },
  endpoints: { gateway },
}));
vi.mock("../connect-flow", async (importOriginal) => ({
  ...(await importOriginal()),
  openSignIn: vi.fn(),
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
        k2_apps: org.k2Apps,
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

// A K2 pilot app as the catalog would card it once its sign-in is served:
// FutureAGI's own REST provider, each person's own account, and only the
// organization's own app of the group can serve it.
function k2App(id, name, clientGroup) {
  const provider = slateProvider({
    connect: {
      own_app: {
        client_group: clientGroup,
        redirect_uri: SLATE_REDIRECT_URI,
        required: true,
      },
    },
  });
  Object.assign(provider, {
    id: "futureagi-rest",
    catalog_ref: `${id}/futureagi-rest@1.0.0`,
    kind: "rest",
    publisher: "futureagi",
    url_template: `https://{connectorsHost}/${id}/mcp`,
  });
  provider.credential.label = `${name} account`;
  return {
    ...slateApp({ providers: [provider] }),
    id,
    name,
    default_provider: "futureagi-rest",
  };
}

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

async function accounts() {
  const section = await screen.findByRole("region", { name: "Your accounts" });
  await waitFor(() =>
    expect(section.getAttribute("aria-busy")).not.toBe("true"),
  );
  return section;
}

beforeEach(() => {
  vi.mocked(openSignIn).mockClear();
  org.level = 8;
  org.k2Apps = true;
  for (const key of Object.keys(gateway)) delete gateway[key];
  Object.assign(gateway, {
    connectorCatalog: CATALOG,
    connectorAdd: (appId) => `/agentcc/connectors/apps/${appId}/add/`,
    mcpCredentials: "/agentcc/mcp/credentials/",
    mcpConnections: "/agentcc/mcp/connect/",
    connectorOAuthClients: CLIENTS,
    connectorOAuthClient: (id) => `${CLIENTS}${id}/`,
  });
  api.catalog = catalogWith(
    k2App("gmail", "Gmail", "google"),
    k2App("microsoft-outlook", "Outlook", "microsoft"),
  );
  api.clients = [];
  api.list = listOf([]);
  api.post = vi.fn(async (url) => {
    throw new Error(`unexpected POST ${url}`);
  });
});

describe("a K2 app waits for the organization's own app registration", () => {
  it.each([
    ["gmail", "Gmail", "Google", "google"],
    ["microsoft-outlook", "Outlook", "Microsoft", "microsoft"],
  ])(
    "names the %s vendor whose app is required and connects once it is stored",
    async (appId, name, vendor, group) => {
      api.post = vi.fn(async (url, body) => {
        if (url !== CLIENTS) throw new Error(`unexpected POST ${url}`);
        api.clients = [slateClient({ client_group: body.client_group })];
        return { data: { status: true, result: api.clients[0] } };
      });
      renderPage();
      const drawer = await openApp(appId);
      expect(
        await within(drawer).findByText(
          `${name} requires your organization's own ${vendor} OAuth app registration; Future AGI's ${vendor} app is not used here. Add it below to connect.`,
        ),
      ).toBeTruthy();
      const connect = within(drawer).getByRole("button", { name: "Connect" });
      expect(connect.disabled).toBe(true);

      const section = within(drawer).getByRole("region", { name: SECTION });
      await userEvent.type(
        within(section).getByLabelText("Client ID"),
        `${group}-client-1`,
      );
      await userEvent.type(
        within(section).getByLabelText("Client secret"),
        SECRET,
      );
      await userEvent.click(
        within(section).getByRole("button", { name: "Save" }),
      );

      await waitFor(() =>
        expect(api.post).toHaveBeenCalledWith(CLIENTS, {
          client_group: group,
          client_id: `${group}-client-1`,
          client_secret: SECRET,
        }),
      );
      await waitFor(() =>
        expect(
          within(drawer).getByRole("button", { name: "Connect" }).disabled,
        ).toBe(false),
      );
      expect(document.body.innerHTML).not.toContain(SECRET);
    },
  );
});

describe("the served catalogue registration contract", () => {
  it.each([3, 1])(
    "shows the registration hold without setup controls at role %s",
    async (level) => {
      org.level = level;
      const app = k2App("gmail", "Gmail", "google");
      app.providers[0].connect.state = "registration_required";
      app.providers[0].connect.reason = "org_oauth_app_required";
      api.catalog = catalogWith(app);
      renderPage();
      const drawer = await openApp("gmail");
      expect(within(drawer).queryByRole("region", { name: SECTION })).toBe(
        null,
      );
      expect(within(drawer).queryByLabelText("Client secret")).toBe(null);
      expect(drawer.textContent).toContain("OAuth app registration");
      expect(
        within(drawer).getByRole("button", { name: "Connect" }).disabled,
      ).toBe(true);
      expect(api.post).not.toHaveBeenCalled();
      expect(openSignIn).not.toHaveBeenCalled();
    },
  );

  it("refuses an empty registration and shows the exact redirect URI", async () => {
    renderPage();
    const drawer = await openApp("gmail");
    const section = within(drawer).getByRole("region", { name: SECTION });
    expect(section.textContent).toContain(SLATE_REDIRECT_URI);
    await userEvent.click(
      within(section).getByRole("button", { name: "Save" }),
    );
    expect(
      await within(section).findByText("Enter the client ID."),
    ).toBeTruthy();
    expect(within(section).getByText("Enter the client secret.")).toBeTruthy();
    expect(api.post).not.toHaveBeenCalled();
    expect(openSignIn).not.toHaveBeenCalled();
  });

  it("adds then POSTs the served start route before following the authorization server", async () => {
    const app = k2App("gmail", "Gmail", "google");
    app.providers[0].connect.state = "ready";
    api.catalog = catalogWith(app);
    api.clients = [slateClient({ client_group: "google" })];
    const authorize =
      "https://authorization.example.test/google/authorize?state=synthetic";
    api.post = vi.fn(async (url) => {
      if (url === "/agentcc/connectors/apps/gmail/add/") {
        return {
          data: {
            status: true,
            result: {
              server_id: "gmail",
              start_url: "/agentcc/mcp/connect/gmail/start/",
            },
          },
        };
      }
      if (url.endsWith("/agentcc/mcp/connect/gmail/start/")) {
        return { data: { status: true, result: { authorize_url: authorize } } };
      }
      throw new Error("unexpected request");
    });
    renderPage();
    const drawer = await openApp("gmail");
    await waitFor(() =>
      expect(
        within(drawer).getByRole("button", { name: "Connect" }).disabled,
      ).toBe(false),
    );
    await userEvent.click(
      within(drawer).getByRole("button", { name: "Connect" }),
    );
    await waitFor(() => expect(openSignIn).toHaveBeenCalledWith(authorize));
    expect(api.post).toHaveBeenCalledTimes(2);
    expect(api.post.mock.calls[1][2]).toEqual({ withCredentials: true });
  });
});

describe("your accounts on a K2 app", () => {
  it("says an Owner or Admin must register the organization's app, and offers no Connect", async () => {
    org.level = 3;
    api.list = listOf([
      notStartable("gmail", "needs_admin", "org_oauth_app_required"),
    ]);
    renderPage();
    const row = within(await accounts()).getByTestId("my-connection-gmail");
    expect(row.textContent).toContain(
      "signs in only through your organization's own OAuth app registration",
    );
    expect(row.textContent).toContain("An Owner or Admin registers it");
    expect(within(row).queryByRole("button", { name: /connect/i })).toBe(null);
  });

  it("explains a start the platform refused for a missing registration", async () => {
    org.level = 3;
    api.list = listOf([connection(ISSUES)]);
    api.post = vi.fn(async () => {
      throw {
        code: "connector_oauth_org_client_required",
        message:
          "Gmail signs in only through your organization's own Google OAuth app registration",
      };
    });
    renderPage();
    const section = await accounts();
    await userEvent.click(
      within(section).getByRole("button", {
        name: `Connect your account on ${ISSUES}`,
      }),
    );
    const alert = await within(section).findByRole("alert");
    expect(alert.textContent).toContain(
      "own OAuth app registration with its vendor",
    );
    expect(alert.textContent).toContain("An Owner or Admin registers it");
  });
});

it("K2 OFF omits Google and Microsoft cards while static apps stay visible", async () => {
  org.k2Apps = false;
  renderPage();
  await screen.findByTestId("row-acme-docs");
  expect(screen.queryByTestId("row-gmail")).toBeNull();
  expect(screen.queryByTestId("row-microsoft-outlook")).toBeNull();
  expect(screen.queryByText(SECTION)).toBeNull();
});
