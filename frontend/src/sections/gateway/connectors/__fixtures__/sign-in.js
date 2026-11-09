// Sign-in apps for the Integrations page tests. HAND-WRITTEN, not replayed
// from a view: on the build these tests were written on, no view serves a
// sign-in provider (CN9's plan_for refuses every OAuth provider, and the
// connect routes are not mounted). Each shape follows the contract the page
// reads, written out in ../connect-flow.js. Once M25x, CN11 and CN24 serve
// it, regenerate these from the real views, as the JSON fixtures beside this
// file were.
import noneAdded from "./catalog-none-added.json";

const clone = (value) => JSON.parse(JSON.stringify(value));

export const SLATE = "slate-mail";
export const SLATE_NAME = "Slate Mail";
export const SLATE_REF = "slate-mail/slate-oauth@1.0.0";

// The catalog card of Slate Mail's one provider, which signs each person in
// with their own account. Tools as the catalog declares them. `connect` says
// this deployment serves its sign-in; leave it out with `served: false`.
export function slateProvider({ served = true, connect = {} } = {}) {
  return {
    ...(served ? { connect: { served: true, ...connect } } : {}),
    account_modes: ["user"],
    catalog_ref: SLATE_REF,
    credential: {
      header_name: "",
      help: "",
      help_url: "",
      in: "",
      kind: "oauth",
      label: "Slate account",
      mode: "per_user",
      name: "",
    },
    era: "auto",
    id: "slate-oauth",
    kind: "vendor_mcp",
    lifecycle: "active",
    publisher: "vendor",
    tier: "listed",
    tools: [
      {
        description: "The search_mail tool.",
        destructive: false,
        gateway_posture: "allow",
        idempotent: true,
        name: "search_mail",
        open_world: true,
        read_only: true,
        reason: null,
        title: "Search mail",
      },
      {
        description: "The send_mail tool.",
        destructive: false,
        gateway_posture: "deny",
        idempotent: false,
        name: "send_mail",
        open_world: true,
        read_only: false,
        reason: "write_tool",
        title: "Send mail",
      },
    ],
    transport: "http",
    url_template: "https://mcp.slate-mail.example.com/mcp",
    url_variables: [],
    vendor_status: "ga",
    version: "1.0.0",
  };
}

// The org's server of Slate Mail, as _org_connectors reports it for the
// user reading the page.
export function slateServer(overrides = {}) {
  return {
    catalog_ref: SLATE_REF,
    connected: false,
    credential_mode: "per_user",
    enabled: true,
    provider: "slate-oauth",
    server_id: SLATE,
    version: "1.0.0",
    virtual_server: { id: "vs_01M5F7JC0SLATE00000000000", slug: SLATE },
    ...overrides,
  };
}

export function slateApp({ providers = [slateProvider()], servers = [] } = {}) {
  return {
    categories: ["email"],
    connected: servers.some((server) => server.connected),
    default_provider: "slate-oauth",
    description: "Read and send email from a team inbox.",
    docs_url: "https://docs.slate-mail.example.com/mcp",
    ee_category: "communication",
    group: "other",
    icon: null,
    id: SLATE,
    lifecycle: "active",
    name: SLATE_NAME,
    providers,
    servers,
    tags: ["email"],
    vendor: { domains: ["slate-mail.example.com"], name: "Slate Labs" },
  };
}

// The catalog page with the key apps of catalog-none-added.json and `apps`.
export function catalogWith(...apps) {
  const catalog = clone(noneAdded);
  catalog.results.push(...apps.map(clone));
  catalog.count = catalog.results.length;
  return catalog;
}

// Where the API sends this user to sign in to Slate Mail.
export const SLATE_SIGN_IN =
  "/agentcc/mcp/connect/slate-mail/start/?binding=b1";

// POST apps/slate-mail/add/ for the sign-in provider: the server is added
// with no account yet, and connect_url is where the admin signs in next.
export function slateAdded({ connectUrl = SLATE_SIGN_IN } = {}) {
  return {
    status: true,
    result: {
      server_id: SLATE,
      catalog_ref: SLATE_REF,
      virtual_server: { id: "vs_01M5F7JC0SLATE00000000000", slug: SLATE },
      tools: [],
      connect_url: connectUrl,
      gateway_synced: true,
    },
  };
}

// Where this user disconnects their own Slate Mail account (M25x's DELETE).
export const SLATE_DISCONNECT = "/agentcc/mcp/connect/slate-mail/";

// An account on the Slate Mail server, as this user may see it.
export function slateAccount(overrides = {}) {
  return {
    id: "6f1c2a9e-0000-4000-8000-000000000001",
    scope: "user",
    label: "ana@slate-mail.example.com",
    mine: true,
    status: "active",
    disconnect_url: SLATE_DISCONNECT,
    ...overrides,
  };
}

// The callback an org registers with the vendor for its own OAuth app, as
// the API names it (the value is configured per deployment; this one is a
// placeholder on a reserved test domain).
export const SLATE_REDIRECT_URI =
  "https://api.example.test/agentcc/mcp/connect/callback/";

// Slate Mail's provider when the org may sign in through its own OAuth app.
export function slateOwnAppProvider({ required = false } = {}) {
  return slateProvider({
    connect: {
      own_app: {
        client_group: "slate",
        redirect_uri: SLATE_REDIRECT_URI,
        required,
      },
    },
  });
}

// An org OAuth client as CN11's views describe it (describe_oauth_client:
// everything but the secret).
export function slateClient(overrides = {}) {
  return {
    id: "0b7e9a52-0000-4000-8000-0000000000c1",
    client_group: "slate",
    server_id: null,
    client_id: "slate-client-1",
    label: "",
    status: "active",
    version: 1,
    created_at: "2026-10-25T10:00:00Z",
    rotated_at: null,
    has_secret: true,
    ...overrides,
  };
}

// A page of CN11's GET /agentcc/connectors/oauth-clients/.
export function clientsPage(results = []) {
  return {
    count: results.length,
    next: null,
    previous: null,
    total_pages: 1,
    current_page: 1,
    results,
  };
}
