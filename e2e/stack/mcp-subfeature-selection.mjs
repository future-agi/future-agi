// Shared declaration for the wrapper, bin/e2e Compose invocation and inspector.
// Expected flags are reviewed constants, never inferred from inspected containers.
import { fileURLToPath } from "node:url";
export const MCP_GATE_KEYS = Object.freeze([
  "AGENTCC_MCP_UPSTREAM_OAUTH_ENABLED",
  "AGENTCC_MCP_CLIENT_OAUTH_ENABLED",
  "AGENTCC_MCP_K2_APPS_ENABLED",
  "AGENTCC_MCP_CLIENT_INSTALLS_ENABLED",
  "AGENTCC_MCP_TOOL_CALL_EXPLORER_ENABLED",
  "AGENTCC_MCP_TOOL_CALL_DOWNLOAD_ENABLED",
]);
const bearer = "bearer:e2e-mcp-upstream-bearer-not-a-secret";
const table = Object.freeze({
  W07: ["mcp", false, false, false, false, false, false, bearer],
  mcp: ["mcp", false, false, false, false, false, false, bearer],
  W08: ["mcp", true, false, false, false, false, false, "oauth"],
  "mcp-upstream": ["mcp", true, false, false, false, false, false, "oauth"],
  W09: ["mcp-signin", false, true, false, false, false, false, bearer],
  "mcp-signin": ["mcp-signin", false, true, false, false, false, false, bearer],
  W10: ["mcp-catalog", true, false, true, false, false, false, bearer],
  "mcp-catalog": [
    "mcp-catalog",
    true,
    false,
    true,
    false,
    false,
    false,
    bearer,
  ],
  W12: ["mcp-signin", false, true, false, true, false, false, bearer],
  "connectors-browse": ["mcp", true, false, false, false, false, false, bearer],
  W14: ["mcp", false, false, false, false, true, true, bearer],
});
const fail = (message) => {
  throw new Error(`STOP: managed mock MCP subfeature ${message}`);
};

export function subfeatureDeclaration(env = process.env) {
  const selection = env.E2E_MCP_SUBFEATURE_SELECTION;
  const selected = selection !== undefined;
  if (selected && !Object.hasOwn(table, selection))
    fail("selection is not recognized");
  const row = selected
    ? table[selection]
    : [
        env.E2E_GATEWAY_PROFILE || "stock",
        false,
        false,
        false,
        false,
        false,
        false,
        bearer,
      ];
  const profile = row[0];
  if (selected && env.E2E_STACK !== "standalone")
    fail("selection requires Standalone");
  if (selected && env.E2E_GATEWAY_PROFILE !== profile)
    fail("selection conflicts with the declared profile");
  if (env.E2E_PROFILE === "cloud-mode" && selected)
    fail("selection conflicts with the attach profile");
  const gates = Object.fromEntries(
    MCP_GATE_KEYS.map((key, i) => [key, String(row[i + 1])]),
  );
  for (const key of MCP_GATE_KEYS) {
    if (Object.hasOwn(env, key) && env[key] !== gates[key])
      fail(`conflicting inherited flag (${key})`);
  }
  if (
    Object.hasOwn(env, "E2E_MCP_MOCK_AUTH") &&
    env.E2E_MCP_MOCK_AUTH !== row[7]
  )
    fail("conflicting mock auth role");
  return { selection, profile, gates, mockAuth: row[7] };
}

// Like gateway-profile tags, selection tags are admission, not quarantine.
// ON and OFF assertions run in separate, identically attested stack phases.
export function mcpSubfeatureExclusionPattern(env = process.env) {
  const { selection } = subfeatureDeclaration(env);
  const selected = selection ? `(?!${selection}(?![\\w-]))` : "";
  return new RegExp(`@mcp-subfeature-${selected}[\\w-]+`);
}

function serviceKeys(service) {
  if (
    ["app", "backend", "worker", "worker-exact-aggregation"].includes(service)
  )
    return MCP_GATE_KEYS;
  if (["agentcc-gateway", "agentcc-gateway-2"].includes(service))
    return MCP_GATE_KEYS.slice(0, 3);
  return [];
}

export function validateMcpGateEnvironment(
  service,
  env,
  declaration,
  complete = true,
) {
  const keys = serviceKeys(service);
  for (const key of MCP_GATE_KEYS) {
    if (Object.hasOwn(env, key) && !keys.includes(key))
      fail(`wrong service ${service} (${key})`);
  }
  for (const key of keys) {
    if (
      (complete || Object.hasOwn(env, key)) &&
      env[key] !== declaration.gates[key]
    ) {
      // Include the existing required-service prefix without echoing environment values.
      throw new Error(
        `STOP: managed mock required ${service} MCP subfeature mismatch (${key})`,
      );
    }
  }
}

export function validateMcpGateEntries(service, entries, declaration) {
  const seen = new Set();
  const env = {};
  for (const entry of entries) {
    const index = entry.indexOf("=");
    const key = index < 0 ? entry : entry.slice(0, index);
    if (!MCP_GATE_KEYS.includes(key) && key !== "E2E_MCP_MOCK_AUTH") continue;
    if (index < 0) fail(`malformed entry on ${service}`);
    if (seen.has(key)) fail(`duplicate entry on ${service} (${key})`);
    seen.add(key);
    env[key] = entry.slice(index + 1);
  }
  const staticMock =
    service === "mcp-mock" &&
    ["mcp", "mcp-signin"].includes(declaration.profile);
  if (Object.hasOwn(env, "E2E_MCP_MOCK_AUTH") && !staticMock)
    fail(`wrong service ${service} (mock auth)`);
  if (staticMock && env.E2E_MCP_MOCK_AUTH !== declaration.mockAuth)
    fail(`required ${service} mock auth mismatch`);
  validateMcpGateEnvironment(service, env, declaration);
}

// Export only constant table values; shell callers parse KEY=value, never eval.
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  try {
    let env = process.env;
    if (process.argv[2] === "--select") {
      const selection = process.argv[3];
      if (!Object.hasOwn(table, selection)) fail("selection is not recognized");
      env = {
        E2E_MCP_SUBFEATURE_SELECTION: selection,
        E2E_STACK: "standalone",
        E2E_GATEWAY_PROFILE: table[selection][0],
      };
    } else if (process.argv[2] !== "--compose") {
      fail("selection command is not recognized");
    }
    const d = subfeatureDeclaration(env);
    const values = { ...d.gates, E2E_MCP_MOCK_AUTH: d.mockAuth };
    if (d.selection !== undefined)
      Object.assign(values, {
        E2E_MCP_SUBFEATURE_SELECTION: d.selection,
        E2E_STACK: "standalone",
        E2E_GATEWAY_PROFILE: d.profile,
      });
    for (const [key, value] of Object.entries(values))
      process.stdout.write(`${key}=${value}\n`);
  } catch (error) {
    process.stderr.write(`${error.message}\n`);
    process.exitCode = 64;
  }
}
