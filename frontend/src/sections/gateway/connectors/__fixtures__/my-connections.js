// "Your accounts" rows for the Integrations page tests: the answers of
// GET /agentcc/mcp/connect/ (M25x). The builders below compose rows for
// each test; my-connections-view.json beside this file holds the real
// view's own answers (GET, POST start and DELETE), dumped from it through
// the real connect flow against the test vendor, as the JSON fixtures
// beside this file were. __tests__/my-connections-fixture.test.js holds
// each builder to the real answer it stands for, field by field, so a
// change in the view's shape fails there before any page test trusts it.

// A per-person server whose organization stored its own OAuth app.
export const ISSUES = "issues";
// A per-person server with no OAuth app of the organization's.
export const TICKETS = "tickets";

export const startUrl = (serverId) => `/agentcc/mcp/connect/${serverId}/start/`;
export const disconnectUrl = (serverId) => `/agentcc/mcp/connect/${serverId}/`;

export const AUTHORIZE_URL =
  "https://auth.vendor.example/authorize?response_type=code&client_id=test-client";

// A row the reader can connect now and never connected.
export function connection(serverId, overrides = {}) {
  return {
    server_id: serverId,
    connected: false,
    cleanup_pending: false,
    upstream_revocation: null,
    status: null,
    connected_at: null,
    rotated_at: null,
    start: { state: "ready", reason: "org_oauth_app" },
    start_url: startUrl(serverId),
    disconnect_url: null,
    ...overrides,
  };
}

// The reader's own active account on the server.
export function connected(serverId, overrides = {}) {
  return connection(serverId, {
    connected: true,
    status: "active",
    connected_at: "2026-10-26T10:00:00Z",
    disconnect_url: disconnectUrl(serverId),
    ...overrides,
  });
}

// The reader's account after they disconnected it: kept as revoked, with
// nothing left to disconnect.
export function revoked(serverId, overrides = {}) {
  return connection(serverId, {
    status: "revoked",
    upstream_revocation: "confirmed",
    connected_at: "2026-10-26T10:00:00Z",
    ...overrides,
  });
}

// A row whose sign-in cannot start here, for the API's reason.
export function notStartable(serverId, state, reason, overrides = {}) {
  return connection(serverId, {
    start: { state, reason },
    start_url: null,
    ...overrides,
  });
}

export function listOf(rows) {
  return { status: true, result: { connections: rows } };
}

export function started(authorizeUrl = AUTHORIZE_URL) {
  return {
    status: true,
    result: { authorize_url: authorizeUrl, expires_in: 600 },
  };
}
