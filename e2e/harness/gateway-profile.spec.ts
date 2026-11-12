import { test, expect, request } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { parse } from 'yaml';
import { E2E } from '../lib/env';
import { GatewayAdmin } from '../lib/gateway-admin';
import { walkGatewayProfile, GATEWAY_PROFILES, nodeFingerprint, PROFILE_FINGERPRINTS, PROFILE_MCP_SIGN_IN,
  PROFILE_REAL_CATALOG, PROFILE_SERVES_MCP, PROFILE_DELTAS, PROFILE_RUNS_MCP_MOCK, profileConfig, validateProfileSource } from '../lib/profile';

// H5's own tests. Offline: the fingerprint read from GET /-/config bodies
// captured from futureagi/agentcc-gateway:e2esa started on each profile's file
// (2026-10-22). Live: the running gateway is the declared profile, so a flow that
// reads its expectations from walkGatewayProfile() never passes on the wrong stack.
// mcp (H-MCP) runs stock's node values, so /-/config alone cannot tell it from
// stock; whether the gateway serves MCP (GatewayAdmin.mcpServed) does. mcp-catalog
// serves MCP as mcp does and is told from it by the catalogue Django serves: the
// one this build bundles, not H-MCP's fixture (PROFILE_REAL_CATALOG; its flow
// checks the catalogue it reads, lib/real-catalog.ts). mcp-signin is mcp plus
// MCP client sign-in, told from it by the protected-resource metadata only it
// publishes (PROFILE_MCP_SIGN_IN).
// (grepInvertPattern's profile rule: scripts/quarantine.test.mjs; the inspector's
// profile rule: harness/managed-mock.spec.ts P3-P7, N8-N16.)

const registry = JSON.parse(readFileSync(new URL('../stack/gateway-profiles.json', import.meta.url), 'utf8'));
delete registry._comment;

// Share the behavioral oracle with its collision control; profile names are not identity.
const profileIdentity = (p: (typeof GATEWAY_PROFILES)[number]) =>
  JSON.stringify({ node: PROFILE_FINGERPRINTS[p], mcp: PROFILE_SERVES_MCP[p], catalog: PROFILE_REAL_CATALOG[p],
    signIn: PROFILE_MCP_SIGN_IN[p], delta: PROFILE_DELTAS[p], staticFixture: PROFILE_RUNS_MCP_MOCK[p],
    declaration: { overlays: registry[p].overlays, services: registry[p].services,
      requires: registry[p].requires, acceptance: registry[p].acceptance ?? 'source-declared' } });
function assertDistinctProfileIdentities(identities: string[]) {
  expect(identities).toHaveLength(GATEWAY_PROFILES.length);
  expect(new Set(identities).size, 'nine walk profiles must have distinct structural identities')
    .toBe(GATEWAY_PROFILES.length);
}

const captured = {
  stock: { server: { host: '0.0.0.0', idle_timeout_ms: 120000, max_request_timeout_ms: 300000, port: 8080,
    read_timeout_ms: 5000, write_timeout_ms: 300000 }, logging: { content_mode: 'full', include_bodies: false,
    level: 'info', request_logging: true }, org_sections_enforced: ['alerting', 'budgets', 'rate_limiting', 'tool_policy'] },
  r7alt: { server: { host: '0.0.0.0', idle_timeout_ms: 90000, max_request_timeout_ms: 300000, port: 8080,
    read_timeout_ms: 7000, write_timeout_ms: 300000 }, logging: { content_mode: 'full', include_bodies: true,
    level: 'warn', request_logging: true }, org_sections_enforced: ['alerting', 'budgets', 'cache', 'rate_limiting', 'tool_policy'] },
};

test('gateway profile offline: each captured node config reads as its own profile, and the two differ', () => {
  expect(nodeFingerprint(captured.stock)).toEqual(PROFILE_FINGERPRINTS.stock);
  expect(nodeFingerprint(captured.r7alt)).toEqual(PROFILE_FINGERPRINTS.r7alt);
  const { stock, r7alt } = PROFILE_FINGERPRINTS;
  // request_logging is on in both (the gateway's default); every other field tells them apart.
  expect(Object.keys(stock).filter((k) => stock[k as keyof typeof stock] === r7alt[k as keyof typeof r7alt]))
    .toEqual(['requestLogging']);
});

test('gateway profile offline: every profile reads apart from every other by its node values, MCP or catalogue', () => {
  expect(nodeFingerprint(captured.stock)).toEqual(PROFILE_FINGERPRINTS.mcp);
  expect(nodeFingerprint(captured.stock)).toEqual(PROFILE_FINGERPRINTS['mcp-catalog']);
  expect(nodeFingerprint(captured.stock)).toEqual(PROFILE_FINGERPRINTS['mcp-signin']);
  expect(PROFILE_SERVES_MCP).toEqual({"stock": false, "r7alt": false, "mcp": true, "mcp-catalog": true, "mcp-signin": true, "mcp-otel": true, "pricefeed": false, "onekey-cat": false, "cloud-mode": false});
  expect(PROFILE_REAL_CATALOG).toEqual({"stock": true, "r7alt": true, "mcp": false, "mcp-catalog": true, "mcp-signin": false, "mcp-otel": false, "pricefeed": true, "onekey-cat": true, "cloud-mode": true});
  expect(PROFILE_MCP_SIGN_IN).toEqual({"stock": false, "r7alt": false, "mcp": false, "mcp-catalog": false, "mcp-signin": true, "mcp-otel": false, "pricefeed": false, "onekey-cat": false, "cloud-mode": false});
  expect(registry).toEqual({
  "stock": {
    "file": "e2e/stack/gateway.e2e.yaml",
    "stacks": [
      "distributed",
      "standalone"
    ],
    "requires": [],
    "overlays": [],
    "services": [],
    "unblockedBy": ""
  },
  "r7alt": {
    "file": "e2e/stack/gateway.r7alt.yaml",
    "stacks": [
      "standalone"
    ],
    "requires": [],
    "overlays": [],
    "services": [],
    "unblockedBy": ""
  },
  "mcp": {
    "file": "e2e/stack/gateway.mcp.yaml",
    "stacks": [
      "standalone"
    ],
    "requires": [
      {
        "file": "agentcc-gateway/internal/config/config.go",
        "contains": "yaml:\"state_keys\""
      },
      {
        "file": "agentcc-gateway/internal/config/config.go",
        "contains": "yaml:\"connectors\""
      },
      {
        "file": "e2e/stack/docker-compose.mcp-e2e.yml"
      }
    ],
    "overlays": [
      "e2e/stack/docker-compose.mcp-e2e.yml"
    ],
    "services": [
      "mcp-mock"
    ],
    "unblockedBy": "the MCP line (release-next/mcp-apps c584a4486a, gw/MCP-CRED-REVOKE-W4-1): the gateway connectors section, the mcp-mock overlay and bin/e2e build mcpmock; mock extensions from gw/E2E-WALK-MOCKS-1"
  },
  "mcp-catalog": {
    "file": "e2e/stack/gateway.mcp-catalog.yaml",
    "stacks": [
      "standalone"
    ],
    "requires": [
      {
        "file": "agentcc-gateway/internal/config/config.go",
        "contains": "yaml:\"state_keys\""
      },
      {
        "file": "e2e/stack/docker-compose.mcp-catalog-e2e.yml"
      }
    ],
    "overlays": [
      "e2e/stack/docker-compose.mcp-catalog-e2e.yml"
    ],
    "services": [],
    "unblockedBy": ""
  },
  "mcp-signin": {
    "file": "e2e/stack/gateway.mcp-signin.yaml",
    "stacks": [
      "standalone"
    ],
    "requires": [
      {
        "file": "agentcc-gateway/internal/config/config.go",
        "contains": "yaml:\"state_keys\""
      },
      {
        "file": "agentcc-gateway/internal/config/config.go",
        "contains": "yaml:\"connectors\""
      },
      {
        "file": "agentcc-gateway/internal/config/config.go",
        "contains": "yaml:\"public_url\""
      },
      {
        "file": "e2e/stack/docker-compose.mcp-e2e.yml"
      }
    ],
    "overlays": [
      "e2e/stack/docker-compose.mcp-e2e.yml"
    ],
    "services": [
      "mcp-mock"
    ],
    "unblockedBy": "the MCP line (gw/MCP-CRED-REVOKE-W4-1 and the G13b build): the gateway connectors section and MCP client sign-in, the mcp-mock overlay; mock /register and /revoke from gw/E2E-WALK-MOCKS-1"
  },
  "mcp-otel": {
    "file": "e2e/stack/gateway.mcp-otel.yaml",
    "stacks": [
      "standalone"
    ],
    "requires": [
      {
        "file": "agentcc-gateway/internal/config/config.go",
        "contains": "yaml:\"state_keys\""
      },
      {
        "file": "agentcc-gateway/internal/config/config.go",
        "contains": "yaml:\"connectors\""
      },
      {
        "file": "agentcc-gateway/internal/config/config.go",
        "contains": "yaml:\"default_content_mode\""
      },
      {
        "file": "agentcc-gateway/internal/tenant/config.go",
        "contains": "json:\"trace_content,omitempty\""
      },
      {
        "file": "e2e/stack/docker-compose.mcp-otel-e2e.yml"
      },
      {
        "file": "e2e/stack/docker-compose.walk-mocks.yml",
        "contains": "otlp-sink:"
      }
    ],
    "overlays": [
      "e2e/stack/docker-compose.mcp-otel-e2e.yml"
    ],
    "services": [
      "mcp-mock",
      "otlp-sink"
    ],
    "unblockedBy": "G16 spans (gw/G16-W5-SPANS-1 5431964337: tenant privacy.trace_content and MCP full-content capture) on the MCP line, plus the otlp-sink service from gw/E2E-WALK-MOCKS-1 in e2e/stack/docker-compose.walk-mocks.yml"
  },
  "pricefeed": {
    "file": "e2e/stack/gateway.pricefeed.yaml",
    "stacks": [
      "standalone"
    ],
    "requires": [
      {
        "file": "agentcc-gateway/internal/config/config.go",
        "contains": "yaml:\"public_price_feed\""
      }
    ],
    "overlays": [],
    "services": [],
    "unblockedBy": "G8-A public price feed (gw/G8-PRICE-FEED-1 12d24e74da: agentcc-gateway/internal/config public_price_feed)"
  },
  "onekey-cat": {
    "file": "e2e/stack/gateway.onekey-cat.yaml",
    "stacks": [
      "standalone"
    ],
    "requires": [
      {
        "file": "e2e/stack/gateway.onekey-cat.yaml"
      }
    ],
    "overlays": [],
    "services": [],
    "unblockedBy": "INT-4: gw/ONEKEY-CAT-2 1ca0af80f5 has no configuration key for the CAT-2 opt-in (catalog.Model.InclusiveEnabled is an in-process gate set only in tests, agentcc-gateway/internal/catalog/catalog.go); the profile file and its delta are written once INT-4 names the operator setting"
  },
  "cloud-mode": {
    "file": "e2e/stack/gateway.e2e.yaml",
    "stacks": [
      "standalone"
    ],
    "requires": [],
    "overlays": [],
    "services": [],
    "unblockedBy": "Contract 713: main must announce acceptance with a head and job; E2E_CLOUD_MODE_OVERLAY is VM-only attach metadata, never a signing file.",
    "acceptance": "not-accepted"
  }
});
  expect(PROFILE_RUNS_MCP_MOCK).toEqual({stock: false, r7alt: false, mcp: true, 'mcp-catalog': false,
    'mcp-signin': true, 'mcp-otel': false, pricefeed: false, 'onekey-cat': false, 'cloud-mode': false});
  const key = 'e2e-only-mcp-state-key-not-a-secret-0000';
  const mcp = {mcp: {enabled: true, state_keys: [key]}, connectors: {catalog_dir: '/opt/e2e/mcp/catalog'}};
  expect(PROFILE_DELTAS).toEqual({stock: {}, r7alt: {cache: {enabled: true},
    logging: {level: 'warn', request_logging: {enabled: true, include_bodies: true}},
    server: {read_timeout: '7s', idle_timeout: '90s'}}, mcp,
    'mcp-catalog': {mcp: {enabled: true, state_keys: [key]}},
    'mcp-signin': {...mcp, mcp: {enabled: true, state_keys: [key], public_url: 'http://localhost:8090',
      oauth: {enabled: true, issuer: 'http://localhost:8100/agentcc/mcp-oauth'}}},
    'mcp-otel': {...mcp, otel: {enabled: true, service_name: 'agentcc-gateway-e2e', exporter: 'otlp',
      endpoint: 'http://otlp-sink:4318', sample_rate: 1, include_bodies: true}, privacy: {default_content_mode: 'full'}},
    pricefeed: {public_price_feed: {enabled: true, sources: [{provider_id: 'e2e-synthetic', catalog_source: 'openrouter'}]}},
    'onekey-cat': null, 'cloud-mode': null});
  const stockSource = readFileSync(new URL('../stack/gateway.e2e.yaml', import.meta.url), 'utf8');
  for (const p of GATEWAY_PROFILES) {
    if (PROFILE_DELTAS[p] === null) {
      expect(() => profileConfig(p, stockSource)).toThrow('has no declared delta yet (owed)');
    } else {
      const committed = readFileSync(new URL(`../../${registry[p].file}`, import.meta.url), 'utf8');
      expect(parse(committed)).toEqual(profileConfig(p, stockSource));
      expect(() => validateProfileSource(p, stockSource, committed)).not.toThrow();
    }
  }
  assertDistinctProfileIdentities(GATEWAY_PROFILES.map(profileIdentity));
});

test('gateway profile offline: the nine-profile oracle rejects a structural collision', () => {
  const identities = GATEWAY_PROFILES.map(profileIdentity);
  assertDistinctProfileIdentities(identities);
  const collision = [...identities];
  collision[1] = collision[0];
  expect(collision).toHaveLength(9);
  expect(new Set(collision).size).toBe(8);
  expect(() => assertDistinctProfileIdentities(collision))
    .toThrow('nine walk profiles must have distinct structural identities');
  // The negative arm changes only its copy; the original nine remain the positive control.
  assertDistinctProfileIdentities(identities);
});

test('gateway profile live: the running gateway is the declared profile', async () => {
  const declared = walkGatewayProfile();
  expect(PROFILE_DELTAS[declared], 'owed/attach profiles must not become fabricated live positives').not.toBeNull();
  expect(registry[declared].acceptance).not.toBe('not-accepted');
  const admin = await GatewayAdmin.open();
  try {
    expect(nodeFingerprint(await admin.config())).toEqual(PROFILE_FINGERPRINTS[walkGatewayProfile()]);
    expect(await admin.mcpServed(), 'the gateway serves MCP exactly when the declared profile does')
      .toBe(PROFILE_SERVES_MCP[walkGatewayProfile()]);
  } finally {
    await admin.dispose();
  }
  // The protected-resource metadata (agentcc-gateway routes_mcp_oauth.go) is served
  // with MCP sign-in only, without a credential.
  const req = await request.newContext({ baseURL: E2E.gatewayUrl });
  try {
    const prm = await req.get('/.well-known/oauth-protected-resource/mcp');
    expect(prm.status() === 200, 'the gateway signs in MCP clients exactly when the declared profile does')
      .toBe(PROFILE_MCP_SIGN_IN[walkGatewayProfile()]);
  } finally {
    await req.dispose();
  }
});

// U1's recorder role is a serving topology, not just a library boolean.
test('gateway profile offline: mcp-otel retains the recorder instead of the public-only H-MCP override', () => {
  const overlay = parse(readFileSync(new URL('../stack/docker-compose.mcp-otel-e2e.yml', import.meta.url), 'utf8'));
  expect(overlay.services).toEqual({ app: {
    environment: {
      AGENTCC_MCP_UPSTREAM_OAUTH_ENABLED: '${AGENTCC_MCP_UPSTREAM_OAUTH_ENABLED:-false}',
      AGENTCC_MCP_CLIENT_OAUTH_ENABLED: '${AGENTCC_MCP_CLIENT_OAUTH_ENABLED:-false}',
      AGENTCC_MCP_K2_APPS_ENABLED: '${AGENTCC_MCP_K2_APPS_ENABLED:-false}',
      AGENTCC_MCP_STATE_KEYS: 'e2e-only-mcp-state-key-not-a-secret-0000',
      SSL_CERT_FILE: '/opt/e2e/mcp/ca.pem',
    },
    volumes: ['./e2e/.mcp/catalog:/opt/e2e/mcp/catalog:ro',
      './e2e/.mcp/catalog/dist/catalog.json:/app/backend/agentcc/connector_catalog/catalog.json:ro',
      './e2e/.mcp/ca.pem:/opt/e2e/mcp/ca.pem:ro'],
  } });
  expect(PROFILE_RUNS_MCP_MOCK['mcp-otel']).toBe(false);
  expect(registry['mcp-otel'].overlays).toEqual(['e2e/stack/docker-compose.mcp-otel-e2e.yml']);
});
