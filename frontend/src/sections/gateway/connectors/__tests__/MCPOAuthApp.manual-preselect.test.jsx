import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HelmetProvider } from "react-helmet-async";
import { beforeEach, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen, waitFor, within } from "src/utils/test-utils";
import {
  catalogWith,
  slateApp,
  slateProvider,
  SLATE,
  SLATE_NAME,
} from "../__fixtures__/sign-in";
import IntegrationsSection from "../IntegrationsSection";

// Slack, HubSpot, Asana, Zoom, GitHub remote and BigQuery sign in through MCP
// OAuth discovery but offer no dynamic client registration: the Configure
// OAuth panel must steer the admin to a manual client and link the vendor's
// setup docs.
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
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

const ROOT = `/agentcc/connectors/apps/${SLATE}/oauth/`;
const URL = "https://mcp.slate-mail.example.com/mcp";
const DOCS = "https://docs.example.test/setup";
const DOCS_LINK = `Open ${SLATE_NAME} docs`;
const APP_SETUP = "https://developer.example.test/apps/new";
// The vendor the slateApp fixture lists (vendor.name).
const VENDOR = "Slate Labs";
const APP_LINK = `Create your ${VENDOR} OAuth app`;
const REASON = `${SLATE_NAME} does not support dynamic registration. Use a manual client.`;
const CLIENT_ID_HELP = `From your ${VENDOR} OAuth app. Add the Redirect URI above to that app first.`;
const DISCOVERY_NO_DCR = "Discovery passed. PKCE S256 supported.";

// credential.help_url is where the admin registers the vendor app (the
// catalog's auth.appRegistration.url); "" until the catalog provides one.
function catalogApp(docsUrl, helpUrl = "") {
  const provider = slateProvider({ connect: { configure_oauth: true } });
  provider.account_modes = ["user", "agent"];
  provider.credential.help_url = helpUrl;
  const app = slateApp({ providers: [provider] });
  app.docs_url = docsUrl;
  return catalogWith(app);
}

function discoveryReturns(dcrSupported) {
  api.post = vi.fn(async (url) => ({
    data: {
      result: url.endsWith("discovery/")
        ? {
            issuer: "https://auth.example.test",
            dcr_supported: dcrSupported,
            pkce_s256: true,
            redirect_uri: api.config.redirect_uri,
          }
        : { ...api.config, configured: true },
    },
  }));
}

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
  api.catalog = catalogApp(DOCS);
  api.get = vi.fn(async (url) => ({
    data: url === ROOT ? { result: api.config } : api.catalog,
  }));
  discoveryReturns(true);
});

async function open() {
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
  await screen.findByRole("heading", { name: "Configure OAuth" });
  // A heading exists even on a query failure; require real configuration data.
  expect(await screen.findByLabelText("Redirect URI")).toHaveValue(
    api.config.redirect_uri,
  );
}

it("selects a manual client and disables DCR when discovery finds no dynamic registration", async () => {
  discoveryReturns(false);
  await open();
  const dcr = screen.getByRole("radio", { name: "Dynamic registration (DCR)" });
  expect(dcr).toBeChecked();
  expect(screen.queryByLabelText("Client ID")).toBeNull();

  await userEvent.click(screen.getByRole("button", { name: "Test discovery" }));
  await screen.findByText(REASON);

  expect(screen.getByRole("radio", { name: "Manual client" })).toBeChecked();
  expect(
    screen.getByRole("radio", { name: "Dynamic registration (DCR)" }),
  ).toBeDisabled();
  expect(screen.getByLabelText("Client ID")).toBeInTheDocument();
  expect(screen.getByLabelText("Client secret")).toBeInTheDocument();
});

it("keeps DCR selected and enabled when discovery finds dynamic registration", async () => {
  await open();
  await userEvent.click(screen.getByRole("button", { name: "Test discovery" }));
  await screen.findByText(/Dynamic registration available/);

  const dcr = screen.getByRole("radio", { name: "Dynamic registration (DCR)" });
  expect(dcr).toBeChecked();
  expect(dcr).toBeEnabled();
  expect(
    screen.getByRole("radio", { name: "Manual client" }),
  ).not.toBeChecked();
  expect(screen.queryByLabelText("Client ID")).toBeNull();
});

it.each([
  ["an https", DOCS, true],
  ["an http", "http://docs.example.test/setup", false],
  ["no", undefined, false],
])(
  "with %s docs URL, shows the vendor docs link: %s",
  async (_label, docsUrl, shown) => {
    api.catalog = catalogApp(docsUrl);
    await open();
    const link = screen.queryByRole("link", { name: DOCS_LINK });
    if (!shown) {
      expect(link).toBeNull();
      return;
    }
    expect(link).toHaveAttribute("href", DOCS);
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  },
);

it.each([
  [false, true],
  [true, false],
])(
  "after a discovery with dcr_supported %s, shows why to use a manual client: %s",
  async (dcrSupported, shown) => {
    discoveryReturns(dcrSupported);
    await open();
    expect(screen.queryByText(REASON)).toBeNull();

    await userEvent.click(
      screen.getByRole("button", { name: "Test discovery" }),
    );
    await screen.findByText(/Discovery passed/);

    if (shown) expect(screen.getByText(REASON)).toBeInTheDocument();
    else expect(screen.queryByText(REASON)).toBeNull();
  },
);

it("Save with DCR and no discovery runs discovery first and steers to a manual client when there is no dynamic registration", async () => {
  discoveryReturns(false);
  await open();
  expect(
    screen.getByRole("radio", { name: "Dynamic registration (DCR)" }),
  ).toBeChecked();

  await userEvent.click(screen.getByRole("button", { name: "Save OAuth app" }));
  await screen.findByText(REASON);

  expect(api.post).toHaveBeenCalledTimes(1);
  expect(api.post.mock.calls[0][0]).toBe(ROOT + "discovery/");
  expect(api.post).not.toHaveBeenCalledWith(
    ROOT,
    expect.anything(),
    expect.anything(),
  );
  expect(screen.getByRole("radio", { name: "Manual client" })).toBeChecked();
  await waitFor(() => expect(screen.getByLabelText("Client ID")).toHaveFocus());
});

it("Save with DCR and no discovery runs discovery first, then saves DCR when dynamic registration is available", async () => {
  await open();

  await userEvent.click(screen.getByRole("button", { name: "Save OAuth app" }));

  await waitFor(() =>
    expect(api.post).toHaveBeenCalledWith(
      ROOT,
      expect.objectContaining({ registration: "dcr", discovery_url: URL }),
      expect.anything(),
    ),
  );
  expect(api.post).toHaveBeenCalledTimes(2);
  expect(api.post.mock.calls[0][0]).toBe(ROOT + "discovery/");
  expect(api.post.mock.calls[1][1]).not.toHaveProperty("client_secret");
  expect(screen.queryByText(REASON)).toBeNull();
});

it("Save with DCR and no discovery does not save when discovery fails", async () => {
  api.post = vi.fn(async (url) => {
    if (url.endsWith("discovery/")) throw new Error("discovery unavailable");
    return { data: { result: { ...api.config, configured: true } } };
  });
  await open();

  await userEvent.click(screen.getByRole("button", { name: "Save OAuth app" }));
  await screen.findByText(/Discovery failed/);

  expect(api.post).toHaveBeenCalledTimes(1);
  expect(api.post.mock.calls[0][0]).toBe(ROOT + "discovery/");
  expect(
    screen.getByRole("radio", { name: "Dynamic registration (DCR)" }),
  ).toBeChecked();
  expect(screen.queryByText(REASON)).toBeNull();
});

// Only someone who can register the app sees it, and only until it is
// configured; the docs link stays for every viewer.
it.each([
  ["an https", 8, false, true, APP_SETUP],
  ["an https", 3, false, false, APP_SETUP],
  ["an https", 1, false, false, APP_SETUP],
  ["an https", 8, true, false, APP_SETUP],
  ["an http", 8, false, false, "http://developer.example.test/apps/new"],
  ["an empty", 8, false, false, ""],
])(
  "with %s app registration URL at role %s, configured %s, shows the create-app link: %s",
  async (_label, level, configured, shown, helpUrl) => {
    org.level = level;
    api.config = { ...api.config, configured };
    api.catalog = catalogApp(DOCS, helpUrl);
    await open();
    expect(screen.getByRole("link", { name: DOCS_LINK })).toBeInTheDocument();
    const link = screen.queryByRole("link", { name: APP_LINK });
    if (!shown) {
      expect(link).toBeNull();
      return;
    }
    expect(link).toHaveAttribute("href", APP_SETUP);
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    // Same line as the docs link, separated by a middle dot that screen
    // readers skip.
    expect(link.parentElement).toHaveTextContent(`${DOCS_LINK} · ${APP_LINK}`);
    expect(within(link.parentElement).getByText("·")).toHaveAttribute(
      "aria-hidden",
      "true",
    );
  },
);

it("names the app in the create-app link when the catalog lists no vendor", async () => {
  api.catalog = catalogApp(DOCS, APP_SETUP);
  delete api.catalog.results.find((app) => app.id === SLATE).vendor;
  await open();
  expect(
    screen.getByRole("link", { name: `Create your ${SLATE_NAME} OAuth app` }),
  ).toHaveAttribute("href", APP_SETUP);
  expect(screen.queryByRole("link", { name: APP_LINK })).toBeNull();
});

it("after a discovery without dynamic registration, says what to do once, under the registration choice", async () => {
  discoveryReturns(false);
  await open();
  await userEvent.click(screen.getByRole("button", { name: "Test discovery" }));

  expect(await screen.findByText(DISCOVERY_NO_DCR)).toBeInTheDocument();
  expect(screen.queryByText(/Dynamic registration unavailable/)).toBeNull();
  expect(screen.getByText(REASON)).toBeInTheDocument();
  expect(screen.getAllByText(/use a manual client/i)).toHaveLength(1);
});

it("points the Client ID field at the vendor OAuth app and the Redirect URI", async () => {
  await open();
  expect(screen.queryByText(CLIENT_ID_HELP)).toBeNull();

  await userEvent.click(screen.getByLabelText("Manual client"));

  const helper = screen.getByText(CLIENT_ID_HELP);
  expect(
    screen.getByLabelText("Client ID").getAttribute("aria-describedby"),
  ).toBe(helper.id);
});

it("describes the Client ID field with the reason line while dynamic registration is unavailable", async () => {
  discoveryReturns(false);
  await open();
  await userEvent.click(screen.getByRole("button", { name: "Test discovery" }));

  const reason = await screen.findByText(REASON);
  const helper = screen.getByText(CLIENT_ID_HELP);
  expect(reason.id).not.toBe("");
  expect(helper.id).not.toBe("");
  expect(
    screen.getByLabelText("Client ID").getAttribute("aria-describedby"),
  ).toBe(`${reason.id} ${helper.id}`);
});

it("Save with DCR and no discovery moves focus to the discovery-failed alert when discovery fails", async () => {
  api.post = vi.fn(async (url) => {
    if (url.endsWith("discovery/")) throw new Error("discovery unavailable");
    return { data: { result: { ...api.config, configured: true } } };
  });
  await open();

  await userEvent.click(screen.getByRole("button", { name: "Save OAuth app" }));

  const failed = await screen.findByText(/Discovery failed/);
  const alert = failed.closest('[role="alert"]');
  expect(alert).not.toBeNull();
  await waitFor(() => expect(alert).toHaveFocus());
});

// Every failure the panel reports moves focus to its notice, so the admin
// sees and hears it wherever they were in the panel.
const DISCOVERY_FAILED =
  "Discovery failed. Check the MCP server URL, its resource metadata and PKCE S256 support.";
const SAVE_FAILED =
  "OAuth app could not be saved. Check discovery and client settings, then re-enter the secret if using a manual client.";
const NOT_SYNCED =
  "OAuth app saved. The gateway has not picked up the change yet; wait for synchronization before signing in.";
const SIGN_IN_FAILED =
  "Sign-in could not start. Check the authorization server and gateway configuration, then retry.";

function discoveryFails() {
  api.post = vi.fn(async (url) => {
    if (url.endsWith("discovery/")) throw new Error("discovery unavailable");
    return { data: { result: { ...api.config, configured: true } } };
  });
}

// The notice with exactly this text, once its alert has focus.
async function focusedNotice(text) {
  const message = await screen.findByText(text);
  const alert = message.closest('[role="alert"]');
  expect(alert).not.toBeNull();
  await waitFor(() => expect(alert).toHaveFocus());
  return alert;
}

it("a failed Test discovery moves focus to the discovery-failed alert, which does not say Not saved", async () => {
  discoveryFails();
  await open();

  await userEvent.click(screen.getByRole("button", { name: "Test discovery" }));

  await focusedNotice(DISCOVERY_FAILED);
  expect(screen.queryByText(/Not saved\./)).toBeNull();
  expect(api.post).toHaveBeenCalledTimes(1);
  expect(api.post.mock.calls[0][0]).toBe(ROOT + "discovery/");
});

it("Save with DCR and no discovery starts the discovery-failed notice with Not saved", async () => {
  discoveryFails();
  await open();

  await userEvent.click(screen.getByRole("button", { name: "Save OAuth app" }));

  await focusedNotice(`Not saved. ${DISCOVERY_FAILED}`);
  expect(screen.queryByText(DISCOVERY_FAILED)).toBeNull();
  expect(api.post).toHaveBeenCalledTimes(1);
});

it("a failed Save still clears the secret and moves focus to the notice that says to re-enter it", async () => {
  api.post = vi.fn(async () => {
    throw new Error("save refused");
  });
  await open();
  await userEvent.click(screen.getByLabelText("Manual client"));
  await userEvent.type(screen.getByLabelText("Client ID"), "synthetic-client");
  await userEvent.type(
    screen.getByLabelText("Client secret"),
    "synthetic-client-secret-for-unit-tests",
  );

  await userEvent.click(screen.getByRole("button", { name: "Save OAuth app" }));

  await focusedNotice(SAVE_FAILED);
  expect(screen.getByLabelText("Client secret")).toHaveValue("");
  expect(screen.getByRole("button", { name: "Save OAuth app" })).toBeDisabled();
  expect(api.post).toHaveBeenCalledTimes(1);
});

it("a save the gateway has not picked up yet moves focus to its warning", async () => {
  api.post = vi.fn(async (url) => ({
    data: {
      result: url.endsWith("discovery/")
        ? { dcr_supported: true, pkce_s256: true }
        : { ...api.config, configured: true, gateway_synced: false },
    },
  }));
  await open();

  await userEvent.click(screen.getByRole("button", { name: "Save OAuth app" }));

  await focusedNotice(NOT_SYNCED);
  expect(api.post).toHaveBeenCalledTimes(2);
});

it("a sign-in that could not start moves focus to its alert", async () => {
  api.config = { ...api.config, configured: true };
  api.post = vi.fn(async () => {
    throw new Error("start refused");
  });
  await open();

  await userEvent.click(
    screen.getByRole("button", { name: `Sign in to ${SLATE_NAME}` }),
  );

  await focusedNotice(SIGN_IN_FAILED);
});
