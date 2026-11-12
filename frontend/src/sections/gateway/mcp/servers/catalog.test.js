import { describe, expect, it } from "vitest";

import {
  AUTH_FILTERS,
  STATUS_FILTERS,
  carriedFields,
  filterServers,
  probeMessage,
  serverAuth,
  serverRows,
} from "./catalog";

const VAULT = [
  {
    id: "c1",
    server_id: "crm-tools",
    kind: "api_key",
    header_name: "X-CRM-Key",
    status: "active",
    scope: "shared",
  },
];

describe("serverAuth", () => {
  it("reads a vault reference with the vault's kind and header", () => {
    expect(
      serverAuth(
        {
          credential: { mode: "shared", credential_id: "c1", state: "active" },
        },
        VAULT,
      ),
    ).toEqual({
      kind: "api_key",
      label: "API key",
      state: "set",
      header: "X-CRM-Key",
      credentialId: "c1",
    });
  });

  it("reads a vault reference it cannot resolve as a stored credential", () => {
    expect(
      serverAuth(
        {
          credential: { mode: "shared", credential_id: "c9", state: "active" },
        },
        [],
      ),
    ).toMatchObject({
      kind: "stored",
      label: "Stored credential",
      state: "set",
    });
  });

  it("reads a revoked reference as needing a new credential", () => {
    expect(
      serverAuth(
        {
          credential: { mode: "shared", credential_id: "c1", state: "revoked" },
        },
        VAULT,
      ),
    ).toMatchObject({ state: "revoked" });
  });

  it("reads the legacy inline form, set or not", () => {
    expect(serverAuth({ auth: { type: "bearer", token: "x" } })).toMatchObject({
      kind: "bearer",
      state: "set",
    });
    expect(serverAuth({ auth: { type: "bearer" } })).toMatchObject({
      kind: "bearer",
      state: "not_set",
    });
    expect(serverAuth({ url: "http://x" })).toMatchObject({
      kind: "none",
      label: "No auth",
    });
    expect(serverAuth({ credential: { mode: "none" } })).toMatchObject({
      kind: "none",
    });
  });
});

describe("serverRows and filters", () => {
  const mcp = {
    servers: {
      "crm-tools": {
        url: "http://demo-upstream:8080/mcp-crm",
        transport: "http",
        credential: { mode: "shared", credential_id: "c1", state: "active" },
      },
      "support-tools": {
        url: "http://demo-upstream:8080/mcp",
        transport: "http",
      },
      legacy: { url: "http://x/mcp", enabled: false },
    },
  };
  const status = {
    servers: [
      { server_id: "crm-tools", healthy: true, tool_count: 2 },
      {
        server_id: "support-tools",
        healthy: false,
        tool_count: 0,
        last_error: "initialize: http 404",
      },
    ],
  };
  const rows = serverRows(mcp, status, VAULT);

  it("joins config, health and vault per server", () => {
    expect(
      rows.map((r) => [r.id, r.health.label, r.toolCount, r.auth.kind]),
    ).toEqual([
      ["crm-tools", "Healthy", 2, "api_key"],
      ["legacy", "Disabled", 0, "none"],
      ["support-tools", "Unhealthy", 0, "none"],
    ]);
    expect(rows[2].lastError).toBe("initialize: http 404");
    expect(rows[0].host).toBe("demo-upstream:8080/mcp-crm");
  });

  it("filters on connection status, auth and text", () => {
    const by = (filters) => filterServers(rows, filters).map((r) => r.id);
    expect(by({ status: "connected" })).toEqual(["crm-tools"]);
    expect(by({ status: "unhealthy" })).toEqual(["support-tools"]);
    expect(by({ status: "disabled" })).toEqual(["legacy"]);
    expect(by({ auth: "none" })).toEqual(["legacy", "support-tools"]);
    expect(by({ auth: "api_key" })).toEqual(["crm-tools"]);
    expect(by({ search: "MCP-CRM" })).toEqual(["crm-tools"]);
    expect(STATUS_FILTERS.map((f) => f.value)).toEqual([
      "connected",
      "unhealthy",
      "disabled",
      "not_connected",
    ]);
    expect(AUTH_FILTERS.map((f) => f.value)).toEqual([
      "none",
      "bearer",
      "api_key",
      "stored",
    ]);
  });
});

describe("the connect flow", () => {
  it("carries a stored server's non-secret fields into an edit, and drops the ignored cache TTL", () => {
    expect(
      carriedFields({
        url: "http://old/mcp",
        transport: "http",
        enabled: true,
        timeout_ms: 10000,
        era: "auto",
        forward_headers: ["x-tenant"],
        labels: ["private_data"],
        tools_cache_ttl: "5m",
        auth: { type: "bearer", token: "secret" },
        credential: { mode: "shared", credential_id: "c1", state: "active" },
        command: "npx thing",
      }),
    ).toEqual({
      enabled: true,
      timeout_ms: 10000,
      era: "auto",
      forward_headers: ["x-tenant"],
      labels: ["private_data"],
    });
  });

  it("keeps catalog and connection metadata without echoing credentials or unknown fields", () => {
    const metadata = {
      catalog_ref: "gitlab/pat@1.0.0",
      url_variables: { host: "gitlab.example.test" },
      toolkits: ["gitlab"],
      oauth: { adapter: "auto" },
      tools_cache_ttl_seconds: 120,
    };
    expect(carriedFields({
      ...metadata,
      auth: { type: "bearer", token: "test-only-not-a-real-token" },
      credential: { mode: "shared", credential_id: "c1", state: "active" },
      command: "not-a-console-field",
      tools_cache_ttl: "5m",
      unknown_setting: true,
    })).toEqual(metadata);
  });

  it("keeps explicit empty metadata and a zero refresh interval", () => {
    const metadata = {
      catalog_ref: "",
      url_variables: {},
      toolkits: [],
      oauth: {},
      tools_cache_ttl_seconds: 0,
    };
    expect(carriedFields(metadata)).toEqual(metadata);
    expect(carriedFields({ catalog_ref: undefined })).toEqual({});
    expect(carriedFields()).toEqual({});
  });

  it("explains a probe answer in words", () => {
    expect(
      probeMessage({
        ok: true,
        server_info: { name: "support-tools", version: "1.4.0" },
        tool_count: 4,
        era: "2025-11-25",
        latency_ms: 65,
      }),
    ).toEqual({
      ok: true,
      text: "Test passed for support-tools 1.4.0: 4 tools, protocol 2025-11-25, 65ms. Save this server to keep the configuration.",
    });
    expect(
      probeMessage({ ok: false, error: "not_mcp", latency_ms: 14 }),
    ).toEqual({
      ok: false,
      text: "This URL did not answer as an MCP server. Use the server's MCP endpoint (it often ends in /mcp).",
    });
    expect(probeMessage({ ok: false, error: "http_401" }).text).toMatch(
      /http_401/,
    );
  });
});
