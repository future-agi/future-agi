import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HelmetProvider } from "react-helmet-async";
import { beforeEach, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen, within } from "src/utils/test-utils";
import {
  catalogWith,
  slateAccount,
  slateApp,
  slateProvider,
  slateServer,
  SLATE,
  SLATE_SIGN_IN,
} from "../__fixtures__/sign-in";
import IntegrationsSection from "../IntegrationsSection";

// An OAuth provider's help (the catalog's auth.help, on the card as
// credential.help) says what signing in or disconnecting cannot do, such as a
// grant Disconnect cannot revoke at the vendor. It is shown on the Configure
// OAuth panel, and with the sign-in card's accounts, beside their Disconnect,
// whenever the drawer lists accounts.
const { api } = vi.hoisted(() => ({ api: {} }));
vi.mock("src/utils/axios", () => ({
  default: {
    get: (...args) => api.get(...args),
    post: (...args) => api.post(...args),
    delete: (...args) => api.remove(...args),
  },
  endpoints: { gateway: { connectorCatalog: "/agentcc/connectors/catalog/" } },
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
    orgLevel: 8,
    currentOrganizationId: "org-a",
    selectedOrganization: { id: "org-a" },
  }),
}));
vi.mock("ag-grid-react", () => import("./ag-grid-mock"));
vi.mock("src/hooks/use-ag-theme", () => ({ useAgThemeWith: () => ({}) }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

const ROOT = `/agentcc/connectors/apps/${SLATE}/oauth/`;
const HELP =
  "Disconnect deletes FutureAGI's stored copy but cannot revoke the grant at Slate Mail, so also revoke it in Slate Mail.";

// Slate Mail with its one sign-in provider carrying `help`; `configure` puts
// its sign-in behind the Configure OAuth panel, and `accounts` adds the org's
// server with those accounts.
function catalogApp({ help = HELP, configure = true, accounts = null } = {}) {
  const provider = slateProvider({
    connect: configure ? { configure_oauth: true } : {},
  });
  provider.credential.help = help;
  const servers = accounts
    ? [slateServer({ connected: true, connect_url: SLATE_SIGN_IN, accounts })]
    : [];
  return catalogWith(slateApp({ providers: [provider], servers }));
}

beforeEach(() => {
  api.config = {
    configured: false,
    mode: "per_user",
    registration: "dcr",
    has_secret: false,
    redirect_uri: "https://api.example.test/agentcc/mcp/connect/callback/",
    discovery_url: "https://mcp.slate-mail.example.com/mcp",
  };
  api.catalog = catalogApp();
  api.get = vi.fn(async (url) => ({
    data: url === ROOT ? { result: api.config } : api.catalog,
  }));
  api.post = vi.fn(async (url) => {
    throw new Error(`unexpected POST ${url}`);
  });
  api.remove = vi.fn(async (url) => {
    throw new Error(`unexpected DELETE ${url}`);
  });
});

async function openDrawer() {
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
  await userEvent.click(await screen.findByTestId(`row-${SLATE}`));
  return screen.findByRole("dialog");
}

it("shows the provider's help on the Configure OAuth panel", async () => {
  const drawer = await openDrawer();
  await within(drawer).findByRole("heading", { name: "Configure OAuth" });
  expect(await within(drawer).findByLabelText("Redirect URI")).toHaveValue(
    api.config.redirect_uri,
  );
  expect(within(drawer).getAllByText(HELP)).toHaveLength(1);
});

it("shows no help line when the provider has none", async () => {
  api.catalog = catalogApp({ help: "" });
  const drawer = await openDrawer();
  await within(drawer).findByLabelText("Redirect URI");
  expect(within(drawer).queryByText(HELP)).toBeNull();
});

it("shows the provider's help with the accounts on the sign-in card", async () => {
  api.catalog = catalogApp({ configure: false, accounts: [slateAccount()] });
  const drawer = await openDrawer();
  const accounts = within(drawer).getByRole("region", { name: "Accounts" });
  expect(within(accounts).getByText(HELP)).toBeInTheDocument();
  expect(within(drawer).getAllByText(HELP)).toHaveLength(1);
});

it("shows it beside the accounts' Disconnect when the Configure OAuth panel is shown too", async () => {
  api.catalog = catalogApp({ accounts: [slateAccount()] });
  const drawer = await openDrawer();
  await within(drawer).findByLabelText("Redirect URI");
  const accounts = within(drawer).getByRole("region", { name: "Accounts" });
  expect(within(accounts).getByText(HELP)).toBeInTheDocument();
  expect(
    within(accounts).getByRole("button", {
      name: "Disconnect ana@slate-mail.example.com",
    }),
  ).toBeInTheDocument();
  // Once on the Configure OAuth panel, once with the accounts.
  expect(within(drawer).getAllByText(HELP)).toHaveLength(2);
});
