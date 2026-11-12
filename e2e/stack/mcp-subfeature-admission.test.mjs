// U3 contract controls. Execution belongs on the guarded harness kit, never locally.
import assert from "node:assert/strict";
import test from "node:test";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import {
  subfeatureDeclaration,
  validateMcpGateEnvironment,
  validateMcpGateEntries,
} from "./mcp-subfeature-selection.mjs";

const keys = [
  "AGENTCC_MCP_UPSTREAM_OAUTH_ENABLED",
  "AGENTCC_MCP_CLIENT_OAUTH_ENABLED",
  "AGENTCC_MCP_K2_APPS_ENABLED",
  "AGENTCC_MCP_CLIENT_INSTALLS_ENABLED",
  "AGENTCC_MCP_TOOL_CALL_EXPLORER_ENABLED",
  "AGENTCC_MCP_TOOL_CALL_DOWNLOAD_ENABLED",
];
const bearer = "bearer:e2e-mcp-upstream-bearer-not-a-secret";
const vectors = [
  [
    "W07",
    "mcp",
    ["false", "false", "false", "false", "false", "false"],
    bearer,
  ],
  [
    "mcp",
    "mcp",
    ["false", "false", "false", "false", "false", "false"],
    bearer,
  ],
  [
    "W08",
    "mcp",
    ["true", "false", "false", "false", "false", "false"],
    "oauth",
  ],
  [
    "mcp-upstream",
    "mcp",
    ["true", "false", "false", "false", "false", "false"],
    "oauth",
  ],
  [
    "W09",
    "mcp-signin",
    ["false", "true", "false", "false", "false", "false"],
    bearer,
  ],
  [
    "mcp-signin",
    "mcp-signin",
    ["false", "true", "false", "false", "false", "false"],
    bearer,
  ],
  [
    "W10",
    "mcp-catalog",
    ["true", "false", "true", "false", "false", "false"],
    bearer,
  ],
  [
    "mcp-catalog",
    "mcp-catalog",
    ["true", "false", "true", "false", "false", "false"],
    bearer,
  ],
  [
    "W12",
    "mcp-signin",
    ["false", "true", "false", "true", "false", "false"],
    bearer,
  ],
  [
    "connectors-browse",
    "mcp",
    ["true", "false", "false", "false", "false", "false"],
    bearer,
  ],
  ["W14", "mcp", ["false", "false", "false", "false", "true", "true"], bearer],
];
const gates = (values) =>
  Object.fromEntries(keys.map((k, i) => [k, values[i]]));
const declaration = (selection, profile) =>
  subfeatureDeclaration({
    E2E_MCP_SUBFEATURE_SELECTION: selection,
    E2E_GATEWAY_PROFILE: profile,
    E2E_STACK: "standalone",
  });

for (const [selection, profile, values, auth] of vectors) {
  test(`U3 ${selection}: fixed six Django / three gateway gates and replica parity`, () => {
    const d = declaration(selection, profile);
    assert.deepEqual(d.gates, gates(values));
    assert.equal(d.profile, profile);
    assert.equal(d.mockAuth, auth);
    for (const service of [
      "app",
      "backend",
      "worker",
      "worker-exact-aggregation",
    ]) {
      assert.doesNotThrow(() =>
        validateMcpGateEnvironment(service, gates(values), d),
      );
    }
    const gateway = gates(values);
    for (const key of keys.slice(3)) delete gateway[key];
    for (const service of ["agentcc-gateway", "agentcc-gateway-2"]) {
      assert.doesNotThrow(() =>
        validateMcpGateEnvironment(service, gateway, d),
      );
      assert.throws(
        () => validateMcpGateEnvironment(service, gates(values), d),
        /wrong service/,
      );
    }
    for (const service of ["app", "agentcc-gateway-2"]) {
      const correct = service === "app" ? gates(values) : gateway;
      for (const key of Object.keys(correct)) {
        for (const wrong of [
          undefined,
          "",
          "TRUE",
          "0",
          correct[key] === "true" ? "false" : "true",
        ]) {
          const changed = { ...correct, [key]: wrong };
          if (wrong === undefined) delete changed[key];
          assert.throws(
            () => validateMcpGateEnvironment(service, changed, d),
            /MCP subfeature/,
          );
        }
      }
    }
    for (const service of ["mock-llm", "mcp-mock", "frontend", "postgres"]) {
      assert.throws(
        () => validateMcpGateEnvironment(service, { [keys[0]]: "false" }, d),
        /wrong service/,
      );
    }
  });
}

test("U3 no selection means production OFF for every profile, never W10 defaults", () => {
  for (const profile of [
    "stock",
    "r7alt",
    "mcp",
    "mcp-signin",
    "mcp-catalog",
    "mcp-otel",
    "pricefeed",
    "onekey-cat",
    "cloud-mode",
  ]) {
    const d = subfeatureDeclaration({ E2E_GATEWAY_PROFILE: profile });
    assert.deepEqual(
      d.gates,
      gates(["false", "false", "false", "false", "false", "false"]),
    );
    assert.equal(d.mockAuth, bearer);
    assert.doesNotThrow(() => validateMcpGateEnvironment("app", d.gates, d));
    assert.throws(
      () => validateMcpGateEnvironment("app", {}, d),
      /required app MCP subfeature/,
    );
    assert.throws(
      () =>
        validateMcpGateEnvironment("app", { ...d.gates, [keys[0]]: "true" }, d),
      /MCP subfeature/,
    );
  }
});

test("U3 malformed declaration, incompatible profile, inherited override and duplicate entries fail closed", () => {
  for (const selection of ["", "unknown", "W08,W12"]) {
    assert.throws(
      () => declaration(selection, "mcp"),
      /MCP subfeature selection/,
    );
  }
  assert.throws(() => declaration("W08", "mcp-signin"), /profile/);
  assert.throws(
    () =>
      subfeatureDeclaration({
        E2E_MCP_SUBFEATURE_SELECTION: "W08",
        E2E_GATEWAY_PROFILE: "mcp",
        E2E_STACK: "distributed",
      }),
    /Standalone/,
  );
  for (const key of keys)
    assert.throws(
      () => subfeatureDeclaration({ [key]: "true" }),
      /conflicting/,
    );
  const d = declaration("W08", "mcp");
  const entries = Object.entries(d.gates).map(([k, v]) => `${k}=${v}`);
  assert.doesNotThrow(() => validateMcpGateEntries("app", entries, d));
  assert.throws(
    () => validateMcpGateEntries("app", [...entries, `${keys[0]}=false`], d),
    /duplicate/,
  );
  assert.throws(
    () => validateMcpGateEntries("app", [...entries, keys[0]], d),
    /malformed|duplicate/,
  );
});

test("U3 wrapper exports the shared selection consumed by Compose and inspector", () => {
  const wrapper = fileURLToPath(
    new URL("./with-mcp-subfeatures.sh", import.meta.url),
  );
  for (const [selection, profile, values, auth] of vectors) {
    const names = [
      "E2E_MCP_SUBFEATURE_SELECTION",
      "E2E_GATEWAY_PROFILE",
      ...keys,
      "E2E_MCP_MOCK_AUTH",
    ];
    const result = spawnSync(
      "bash",
      [
        wrapper,
        selection,
        process.execPath,
        "-e",
        `process.stdout.write(JSON.stringify(${JSON.stringify(names)}.map(k => process.env[k])))`,
      ],
      { encoding: "utf8" },
    );
    assert.equal(result.status, 0, result.stderr);
    assert.deepEqual(JSON.parse(result.stdout), [
      selection,
      profile,
      ...values,
      auth,
    ]);
  }
});

test("U3 Compose refuses conflicting declarations before any Docker invocation", () => {
  const bin = fileURLToPath(new URL("../../bin/e2e", import.meta.url));
  const result = spawnSync("bash", [bin, "compose", "config"], {
    encoding: "utf8",
    env: {
      ...process.env,
      E2E_STACK: "standalone",
      E2E_GATEWAY_PROFILE: "mcp",
      E2E_MCP_SUBFEATURE_SELECTION: "W07",
      [keys[0]]: "true",
    },
  });
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /MCP subfeature.*conflicting/);
});

test("U3 mock authentication is checked on the serving fixture, not only in the parent shell", () => {
  for (const selection of ["W07", "W08", "W09", "W12", "W14"]) {
    const profile = ["W07", "W08", "W14"].includes(selection)
      ? "mcp"
      : "mcp-signin";
    const d = declaration(selection, profile);
    assert.throws(
      () =>
        subfeatureDeclaration({
          E2E_MCP_SUBFEATURE_SELECTION: selection,
          E2E_GATEWAY_PROFILE: profile,
          E2E_STACK: "standalone",
          E2E_MCP_MOCK_AUTH: d.mockAuth === bearer ? "oauth" : bearer,
        }),
      /conflicting mock auth role/,
    );
    assert.doesNotThrow(() =>
      validateMcpGateEntries(
        "mcp-mock",
        [`E2E_MCP_MOCK_AUTH=${d.mockAuth}`],
        d,
      ),
    );
    for (const entries of [
      [],
      ["E2E_MCP_MOCK_AUTH=none"],
      ["E2E_MCP_MOCK_AUTH=stale"],
      ["E2E_MCP_MOCK_AUTH"],
      ["E2E_MCP_MOCK_AUTH="],
      [`E2E_MCP_MOCK_AUTH=${d.mockAuth === bearer ? "oauth" : bearer}`],
      [`E2E_MCP_MOCK_AUTH=${d.mockAuth}`, `E2E_MCP_MOCK_AUTH=${d.mockAuth}`],
    ]) {
      assert.throws(
        () => validateMcpGateEntries("mcp-mock", entries, d),
        /MCP subfeature/,
      );
    }
    assert.throws(
      () =>
        validateMcpGateEntries(
          "mock-llm",
          [`E2E_MCP_MOCK_AUTH=${d.mockAuth}`],
          d,
        ),
      /wrong service/,
    );
  }
});
