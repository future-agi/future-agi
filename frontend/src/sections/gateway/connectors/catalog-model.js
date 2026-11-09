// How the Integrations page reads the connector catalog API (CN9): which
// apps a filter keeps, an app's status, its provider, auth type and quality
// badge, and why a tool is blocked. Plain functions over the API's own
// records; the page never adds a fact the API did not send.

// The manifest's eeCategory values (connector-manifest/v1), in page order.
export const CATEGORIES = [
  { id: "productivity", label: "Productivity" },
  { id: "developer", label: "Developer" },
  { id: "communication", label: "Communication" },
  { id: "data", label: "Data" },
  { id: "research", label: "Research" },
];

const CATEGORY_LABELS = Object.fromEntries(
  CATEGORIES.map((category) => [category.id, category.label]),
);

// An open category facet such as "issue-tracking" reads "Issue tracking".
export function categoryLabel(id) {
  if (CATEGORY_LABELS[id]) return CATEGORY_LABELS[id];
  const words = String(id || "").replace(/-/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

// Whether a provider card says this deployment serves its sign-in end to
// end (connect.served, connect-flow.js).
export function signInServed(provider) {
  const connect = provider?.connect;
  return (
    Boolean(connect) &&
    typeof connect === "object" &&
    !Array.isArray(connect) &&
    connect.served === true
  );
}

// A provider this page can connect: no credential, one shared key (a bearer
// token or an API key) the admin stores in the vault, or a sign-in the API
// says this deployment serves. The API lists only such providers (CN9's
// plan_for); the page keeps the same rule, so a sign-in provider is never
// offered where its sign-in does not run. A sign-in only the org's own
// OAuth app can serve also needs a console that can store that app
// (`ownAppStorable`, CN11's routes).
function connectable(provider, ownAppStorable) {
  const credential = provider?.credential;
  if (credential?.mode === "none") return true;
  if (isSignInCredential(credential)) {
    if (!signInServed(provider)) return false;
    return ownAppStorable || provider.connect.own_app?.required !== true;
  }
  return (
    credential?.mode === "shared" &&
    (credential.kind === "bearer" || credential.kind === "api_key")
  );
}

// The apps the page lists: each with only its connectable providers, and an
// app with none left out unless the org already has a server of it (so it
// can still be seen and disconnected).
const K2_APP_IDS = new Set([
  "gmail",
  "google-calendar",
  "google-bigquery",
  "microsoft-outlook",
  "microsoft-teams",
  "arcade",
]);
const K2_GROUPS = new Set(["google", "microsoft", "arcade"]);

function k2Provider(app, provider) {
  return (
    provider?.id === "arcade" ||
    (isSignInCredential(provider?.credential) &&
      (K2_APP_IDS.has(app.id) ||
        K2_GROUPS.has(app.group) ||
        K2_GROUPS.has(provider?.connect?.own_app?.client_group)))
  );
}

export function connectableApps(
  apps,
  { ownAppStorable = false, upstreamOAuth = false, k2Apps = false } = {},
) {
  return (apps || [])
    .map((app) => ({
      ...app,
      providers: (app.providers || [])
        .filter(
          (provider) =>
            (upstreamOAuth || !isSignInCredential(provider.credential)) &&
            (k2Apps || !k2Provider(app, provider)) &&
            connectable(provider, ownAppStorable),
        )
        .map((provider) =>
          upstreamOAuth ? provider : { ...provider, connect: undefined },
        ),
      servers: (app.servers || []).filter((server) => {
        const provider = (app.providers || []).find(
          (item) => item.id === server.provider,
        );
        if (!upstreamOAuth && isSignInServer(server, provider)) return false;
        if (
          !k2Apps &&
          (k2Provider(app, provider) ||
            ((K2_APP_IDS.has(app.id) || K2_GROUPS.has(app.group)) &&
              isSignInServer(server, provider)))
        )
          return false;
        return true;
      }),
    }))
    .filter((app) => app.providers.length > 0 || app.servers.length > 0);
}

export const APP_STATUS = {
  CONNECTED: "connected",
  NEEDS_KEY: "needs_key",
  // A sign-in app that is added but has no account the gateway can call it
  // with: not signed in yet, or a sign-in this deployment does not serve.
  NOT_CONNECTED: "not_connected",
  OFF: "off",
  AVAILABLE: "available",
};

// A server whose account is a sign-in rather than a stored key: each person
// signs in (credential mode per_user, which only a sign-in has), or the
// provider it was added with signs in.
export function isSignInServer(server, provider) {
  return (
    server?.credential_mode === "per_user" ||
    isSignInCredential(provider?.credential)
  );
}

// The catalog's sign-in kinds: the vault's oauth, and the manifest's own
// names for it.
const SIGN_IN_KINDS = new Set(["oauth", "oauth2", "mcp_oauth"]);

export function isSignInCredential(credential) {
  return SIGN_IN_KINDS.has(credential?.kind);
}

// Connected: a server of the org names the app by catalog_ref and the API
// says it is connected (its shared key is in the vault and pushed, or it
// needs none). The API derives `connected` from exactly these servers.
export function isConnected(app) {
  return (app?.servers || []).some(
    (server) => Boolean(server.catalog_ref) && server.connected === true,
  );
}

export function appStatus(app) {
  const servers = app?.servers || [];
  if (!servers.length) return APP_STATUS.AVAILABLE;
  if (isConnected(app)) return APP_STATUS.CONNECTED;
  if (servers.some((server) => server.enabled === false)) return APP_STATUS.OFF;
  const provider = currentProvider(app);
  if (servers.some((server) => isSignInServer(server, provider))) {
    return APP_STATUS.NOT_CONNECTED;
  }
  return APP_STATUS.NEEDS_KEY;
}

// The provider the app is shown with: the one the org added, else the
// catalog's default, else the first one the org can add. Null when the org
// added a provider the catalog no longer offers.
export function currentProvider(app) {
  const providers = app?.providers || [];
  const server = app?.servers?.[0];
  if (server) {
    return (
      providers.find((provider) => provider.id === server.provider) || null
    );
  }
  return (
    providers.find((provider) => provider.id === app.default_provider) ||
    providers[0] ||
    null
  );
}

// The vendor's own label for the surface a provider uses (the manifest's
// vendorStatus: ga, preview, beta or unknown), as a plain fact. GA says
// nothing a reader needs, so it gives null; so does no provider.
const VENDOR_STATUS_TEXT = {
  preview: "Preview",
  beta: "Beta",
  unknown: "Not stated by the vendor",
};

export function vendorStatusText(provider) {
  const label = provider?.vendor_status;
  if (!label || label === "ga") return null;
  return VENDOR_STATUS_TEXT[label] || label;
}

export const AUTH_TYPES = {
  api_key: "API key",
  bearer: "Token",
  oauth: "Sign-in",
  none: "No key",
};

export function authType(credential) {
  if (!credential || credential.mode === "none") return "none";
  if (isSignInCredential(credential)) return "oauth";
  return credential.kind === "bearer" ? "bearer" : "api_key";
}

// The catalog's own name for the key or account ("Personal access token"),
// else the auth type's.
export function authLabel(credential) {
  return credential?.label || AUTH_TYPES[authType(credential)];
}

// Where the gateway puts the key on each call, as the catalog declares it;
// for a sign-in, whose account the gateway calls the app as.
export function placementText(credential) {
  const type = authType(credential);
  if (type === "none") return "No key needed";
  if (type === "oauth") {
    return credential.mode === "per_user"
      ? "Each person signs in with their own account"
      : "One account for the organization, signed in by an Owner or Admin";
  }
  if (credential.in === "query") {
    return `Sent in the ${credential.name} query parameter`;
  }
  if (credential.in === "url_variable") return "Sent in the server URL";
  if (type === "bearer") return "Sent as a bearer token";
  return `Sent in the ${credential.header_name || credential.name} header`;
}

// The auth filters worth offering: only types some listed app has.
export function authOptions(apps) {
  const present = new Set(
    (apps || []).map((app) => authType(currentProvider(app)?.credential)),
  );
  return Object.entries(AUTH_TYPES)
    .filter(([id]) => present.has(id))
    .map(([id, label]) => ({ id, label }));
}

// Badges come only from the provider's quality tier (quality.tier in the
// manifest); a value outside the tier enum shows no badge. `color` is the
// MUI chip colour.
const TIER_BADGES = {
  listed: { label: "Listed", color: "default" },
  verified: { label: "Verified", color: "success" },
  optimized: { label: "Optimized", color: "primary" },
};

export function tierBadge(provider) {
  return TIER_BADGES[provider?.tier] || null;
}

export function toolSummary(provider) {
  const tools = provider?.tools || [];
  return {
    allowed: tools.filter((tool) => tool.gateway_posture === "allow").length,
    total: tools.length,
  };
}

// An app's tools as its drawer shows them: the ones its server listed when
// it was added, where the API sends them, else its provider's in the
// catalog.
export function appToolSummary(app) {
  const listed = app?.servers?.[0]?.tools;
  return Array.isArray(listed)
    ? toolSummary({ tools: listed })
    : toolSummary(currentProvider(app));
}

// Why the gateway blocks a declared tool (CN9's tool_policy reasons).
const DENIALS = {
  posture_deny: "Blocked: the catalog denies this tool.",
  destructive_tool: "Blocked: it can delete or overwrite data.",
  write_tool:
    "Blocked: it changes data, and connector tools that write are blocked.",
  generic_tool:
    "Blocked: its real operation is one of its arguments, which a policy cannot check.",
  approval_not_enforced:
    "Blocked: it is set to ask for approval, and the gateway has no approval step for connector tools.",
};

export function denialText(reason) {
  if (!reason) return null;
  return DENIALS[reason] || "Blocked by the gateway.";
}

function matchesSearch(app, query) {
  const haystack = [
    app.id,
    app.name,
    app.description,
    app.vendor?.name,
    ...(app.tags || []),
  ]
    .join(" ")
    .toLowerCase();
  return haystack.includes(query);
}

function inCategory(app, category) {
  return (
    app.ee_category === category || (app.categories || []).includes(category)
  );
}

// The apps one view of the page shows: All or Connected, then search,
// category and auth type, each optional.
export function filterApps(apps, { view, search, category, auth }) {
  const query = (search || "").trim().toLowerCase();
  return (apps || []).filter(
    (app) =>
      (view !== "connected" || isConnected(app)) &&
      (!query || matchesSearch(app, query)) &&
      (!category || inCategory(app, category)) &&
      (!auth || authType(currentProvider(app)?.credential) === auth),
  );
}
