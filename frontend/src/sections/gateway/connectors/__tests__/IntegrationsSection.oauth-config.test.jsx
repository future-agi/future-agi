import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HelmetProvider } from "react-helmet-async";
import { beforeEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen, waitFor, within } from "src/utils/test-utils";
import {
  catalogWith,
  slateApp,
  slateProvider,
  SLATE,
} from "../__fixtures__/sign-in";
import IntegrationsSection from "../IntegrationsSection";

const { api, org } = vi.hoisted(() => ({ api: {}, org: { level: 8 } }));
vi.mock("src/utils/axios", () => ({
  default: {
    get: (...args) => api.get(...args),
    post: (...args) => api.post(...args),
  },
  endpoints: { gateway: { connectorCatalog: "/agentcc/connectors/catalog/" } },
}));
vi.mock("src/sections/gateway/context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "gw-1" }),
}));
// Configuring a discovered MCP OAuth app is upstream OAuth: these are the
// switch-ON cases; subfeature-flags.test.js pins the default-OFF deployment.
vi.mock("src/sections/gateway/mcp/hooks/useMCPConfig", () => ({
  useMCPStatus: () => ({
    data: {
      enabled: true,
      features: { upstream_oauth: true, client_oauth: false, k2_apps: false },
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
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

const ROOT = `/agentcc/connectors/apps/${SLATE}/oauth/`;
const URL = "https://mcp.slate-mail.example.com/mcp";
const SECRET = "synthetic-client-secret-for-unit-tests";
let client;

beforeEach(() => {
  org.level = 8;
  api.config = {
    configured: false,
    mode: "per_user",
    registration: "dcr",
    has_secret: false,
    redirect_uri: "https://api.example.test/agentcc/mcp/connect/callback/",
    discovery_url: URL,
  };
  const provider = slateProvider({ connect: { configure_oauth: true } });
  provider.account_modes = ["user", "agent"];
  api.catalog = catalogWith(slateApp({ providers: [provider] }));
  api.get = vi.fn(async (url) => ({
    data: url === ROOT ? { result: api.config } : api.catalog,
  }));
  api.post = vi.fn(async (url) => ({
    data: {
      result: url.endsWith("discovery/")
        ? {
            issuer: "https://auth.example.test",
            dcr_supported: true,
            pkce_s256: true,
            redirect_uri: api.config.redirect_uri,
          }
        : { ...api.config, configured: true },
    },
  }));
});

async function open() {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <HelmetProvider>
      <QueryClientProvider client={client}>
        <IntegrationsSection />
      </QueryClientProvider>
    </HelmetProvider>,
  );
  await userEvent.click(await screen.findByTestId(`row-${SLATE}`));
  await screen.findByRole("heading", { name: "Configure OAuth" });
  // A heading exists even on a query failure; require real configuration data.
  expect(await screen.findByLabelText("Redirect URI")).toHaveValue(
    api.config.redirect_uri,
  );
}

it("tests discovery without saving and registers DCR in shared mode", async () => {
  await open();
  expect(screen.getByLabelText("Discovery URL (MCP server)")).toHaveValue(URL);
  await userEvent.click(screen.getByRole("button", { name: "Test discovery" }));
  await screen.findByText(/Discovery passed/);
  expect(api.post).toHaveBeenCalledTimes(1);
  expect(api.post.mock.calls[0][0]).toBe(ROOT + "discovery/");
  await userEvent.click(screen.getByLabelText("Shared account"));
  await userEvent.click(screen.getByRole("button", { name: "Save OAuth app" }));
  await waitFor(() =>
    expect(api.post).toHaveBeenCalledWith(
      ROOT,
      expect.objectContaining({
        mode: "shared",
        registration: "dcr",
        discovery_url: URL,
      }),
      expect.anything(),
    ),
  );
  expect(api.post.mock.calls[1][1]).not.toHaveProperty("client_secret");
});

it("masks the manual secret and clears it without retaining it in query caches", async () => {
  await open();
  await userEvent.click(screen.getByLabelText("Manual client"));
  await userEvent.type(screen.getByLabelText("Client ID"), "synthetic-client");
  const field = screen.getByLabelText("Client secret");
  expect(field).toHaveAttribute("type", "password");
  await userEvent.type(field, SECRET);
  await userEvent.click(screen.getByRole("button", { name: "Save OAuth app" }));
  await waitFor(() =>
    expect(api.post).toHaveBeenCalledWith(
      ROOT,
      expect.objectContaining({
        client_secret: SECRET,
        client_id: "synthetic-client",
        registration: "manual",
        mode: "per_user",
      }),
      expect.anything(),
    ),
  );
  expect(screen.queryByDisplayValue(SECRET)).toBeNull();
  expect(JSON.stringify(client.getMutationCache().getAll())).not.toContain(
    SECRET,
  );
  expect(
    JSON.stringify(
      client
        .getQueryCache()
        .getAll()
        .map((q) => q.state),
    ),
  ).not.toContain(SECRET);
});

it("never echoes a failed save's request or response containing the secret", async () => {
  api.post = vi.fn(async () => {
    throw new Error(SECRET);
  });
  await open();
  await userEvent.click(screen.getByLabelText("Manual client"));
  await userEvent.type(screen.getByLabelText("Client ID"), "synthetic-client");
  await userEvent.type(screen.getByLabelText("Client secret"), SECRET);
  await userEvent.click(screen.getByRole("button", { name: "Save OAuth app" }));
  await screen.findByText(/OAuth app could not be saved/);
  expect(screen.queryByDisplayValue(SECRET)).toBeNull();
  expect(document.body.textContent).not.toContain(SECRET);
});

it.each([1, 3])(
  "role %s reads configuration without write controls",
  async (level) => {
    org.level = level;
    await open();
    expect(
      screen.getByText(/Not configured\. Ask an organization Owner or Admin/),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save OAuth app" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Test discovery" })).toBeNull();
    expect(screen.queryByLabelText("Client secret")).toBeNull();
    expect(api.post).not.toHaveBeenCalled();
  },
);

it.each([
  [1, "per_user", false],
  [3, "per_user", true],
  [1, "shared", false],
  [3, "shared", false],
])(
  "role %s reads saved %s configuration with sign-in=%s",
  async (level, mode, allowed) => {
    org.level = level;
    api.config = { ...api.config, configured: true, mode };
    await open();
    expect(screen.getByText(/OAuth app configured/)).toBeInTheDocument();
    expect(
      screen.getByText(/Sign in separately to connect an account/),
    ).toBeInTheDocument();
    const button = screen.queryByRole("button", {
      name: "Sign in to Slate Mail",
    });
    if (allowed) expect(button).toBeEnabled();
    else {
      expect(button).toBeNull();
      expect(
        screen.getByText(
          mode === "shared"
            ? /Only an organization Owner or Admin can sign in/
            : /Read-only access/,
        ),
      ).toBeInTheDocument();
    }
    expect(screen.queryByRole("button", { name: "Save OAuth app" })).toBeNull();
    expect(api.post).not.toHaveBeenCalled();
  },
);

it("shows saved registration without pretending an account is connected", async () => {
  api.config = {
    ...api.config,
    configured: true,
    has_secret: true,
    registration: "manual",
  };
  await open();
  await screen.findByText(/OAuth app configured/);
  expect(screen.queryByRole("button", { name: "Save OAuth app" })).toBeNull();
  expect(screen.queryByLabelText("Client secret")).toBeNull();
});

// Datadog, GitLab, monday.com and Neon list a key provider, their default,
// next to an MCP OAuth provider. The add endpoint refuses an MCP OAuth
// provider (oauth_configuration_required), so picking it must open its OAuth
// app configuration, and picking the key provider again must bring the key
// form back.
describe("an app with a key provider and an MCP OAuth provider", () => {
  const KEY_PROVIDER = "slate-api-key";
  const KEY_LABEL = "Slate API key";

  beforeEach(() => {
    const oauthProvider = slateProvider({ connect: { configure_oauth: true } });
    oauthProvider.account_modes = ["user", "agent"];
    const keyProvider = {
      ...slateProvider({ served: false }),
      id: KEY_PROVIDER,
      catalog_ref: `${SLATE}/${KEY_PROVIDER}@1.0.0`,
      account_modes: ["agent"],
      credential: {
        header_name: "X-Slate-Key",
        help: "",
        help_url: "",
        in: "header",
        kind: "api_key",
        label: KEY_LABEL,
        mode: "shared",
        name: "X-Slate-Key",
      },
    };
    const app = slateApp({ providers: [keyProvider, oauthProvider] });
    app.default_provider = KEY_PROVIDER;
    api.catalog = catalogWith(app);
  });

  // Opens the drawer on the default key provider and returns the drawer.
  async function openMixed() {
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <HelmetProvider>
        <QueryClientProvider client={client}>
          <IntegrationsSection />
        </QueryClientProvider>
      </HelmetProvider>,
    );
    await userEvent.click(await screen.findByTestId(`row-${SLATE}`));
    const drawer = await screen.findByRole("dialog");
    expect(await within(drawer).findByLabelText(KEY_LABEL)).toBeTruthy();
    expect(
      within(drawer).queryByRole("heading", { name: "Configure OAuth" }),
    ).toBeNull();
    return drawer;
  }

  function pick(drawer, name) {
    const choice = within(drawer).getByRole("radiogroup", {
      name: "Connect with",
    });
    return userEvent.click(within(choice).getByRole("radio", { name }));
  }

  it("picking the OAuth provider opens its OAuth app configuration, not an add", async () => {
    const drawer = await openMixed();

    await pick(drawer, /Slate account/);

    await within(drawer).findByRole("heading", { name: "Configure OAuth" });
    expect(await within(drawer).findByLabelText("Redirect URI")).toHaveValue(
      api.config.redirect_uri,
    );
    expect(
      within(drawer).getByRole("button", { name: "Save OAuth app" }),
    ).toBeEnabled();
    expect(
      within(drawer).queryByRole("button", { name: "Connect" }),
    ).toBeNull();
    expect(within(drawer).queryByLabelText(KEY_LABEL)).toBeNull();
    expect(api.get).toHaveBeenCalledWith(ROOT);
    expect(api.post).not.toHaveBeenCalled();
  });

  it("picking the key provider again brings its key form back", async () => {
    const drawer = await openMixed();
    await pick(drawer, /Slate account/);
    await within(drawer).findByRole("heading", { name: "Configure OAuth" });

    await pick(drawer, /Slate API key/);

    expect(await within(drawer).findByLabelText(KEY_LABEL)).toBeTruthy();
    expect(
      within(drawer).getByRole("button", { name: "Connect" }),
    ).toBeEnabled();
    expect(
      within(drawer).queryByRole("heading", { name: "Configure OAuth" }),
    ).toBeNull();
    expect(api.post).not.toHaveBeenCalled();
  });
});
