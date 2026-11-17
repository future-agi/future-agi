// Offline profile contract; run on a cloud VM with node --test.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import test from "node:test";

const wrapper = fileURLToPath(
  new URL("./with-mcp-subfeatures.sh", import.meta.url),
);
const switches = [
  "AGENTCC_MCP_UPSTREAM_OAUTH_ENABLED",
  "AGENTCC_MCP_CLIENT_OAUTH_ENABLED",
  "AGENTCC_MCP_K2_APPS_ENABLED",
  "AGENTCC_MCP_CLIENT_INSTALLS_ENABLED",
  "AGENTCC_MCP_TOOL_CALL_EXPLORER_ENABLED",
  "AGENTCC_MCP_TOOL_CALL_DOWNLOAD_ENABLED",
];
const names = [
  "E2E_STACK",
  "E2E_GATEWAY_PROFILE",
  ...switches,
  "E2E_MCP_MOCK_AUTH",
];
const bearer = "bearer:e2e-mcp-upstream-bearer-not-a-secret";
// Client installs is ON only for W12; explorer and downloads only for W14.
const cases = [
  ["W07", "mcp", "false", "false", "false", "false", "false", "false", bearer],
  ["mcp", "mcp", "false", "false", "false", "false", "false", "false", bearer],
  ["W08", "mcp", "true", "false", "false", "false", "false", "false", "oauth"],
  [
    "mcp-upstream",
    "mcp",
    "true",
    "false",
    "false",
    "false",
    "false",
    "false",
    "oauth",
  ],
  [
    "W09",
    "mcp-signin",
    "false",
    "true",
    "false",
    "false",
    "false",
    "false",
    bearer,
  ],
  [
    "mcp-signin",
    "mcp-signin",
    "false",
    "true",
    "false",
    "false",
    "false",
    "false",
    bearer,
  ],
  [
    "W10",
    "mcp-catalog",
    "true",
    "false",
    "true",
    "false",
    "false",
    "false",
    bearer,
  ],
  [
    "mcp-catalog",
    "mcp-catalog",
    "true",
    "false",
    "true",
    "false",
    "false",
    "false",
    bearer,
  ],
  [
    "connectors-browse",
    "mcp",
    "true",
    "false",
    "false",
    "false",
    "false",
    "false",
    bearer,
  ],
  [
    "W12",
    "mcp-signin",
    "false",
    "true",
    "false",
    "true",
    "false",
    "false",
    bearer,
  ],
  ["W14", "mcp", "false", "false", "false", "false", "true", "true", bearer],
];

for (const [selection, ...expected] of cases) {
  for (const inherited of ["true", "false"]) {
    test(`${selection} pins every switch despite inherited ${inherited}`, () => {
      const result = spawnSync(
        "bash",
        [
          wrapper,
          selection,
          process.execPath,
          "-e",
          `process.stdout.write(JSON.stringify(${JSON.stringify(names)}.map(name => process.env[name])))`,
        ],
        {
          encoding: "utf8",
          env: {
            ...process.env,
            ...Object.fromEntries(switches.map((name) => [name, inherited])),
            E2E_STACK: "distributed",
            E2E_GATEWAY_PROFILE: "stock",
            E2E_MCP_MOCK_AUTH: "stale",
          },
        },
      );
      assert.equal(result.status, 0, result.stderr);
      assert.deepEqual(JSON.parse(result.stdout), ["standalone", ...expected]);
    });
  }
}

test("invalid and missing selections never run the command", () => {
  for (const args of [[], ["W07"], ["unknown", "echo", "must-not-run"]]) {
    const result = spawnSync("bash", [wrapper, ...args], { encoding: "utf8" });
    assert.equal(result.status, 64);
    assert.equal(result.stdout, "");
    assert.match(result.stderr, /usage:/);
  }
});

test("the wrapped command exit code is preserved", () => {
  const result = spawnSync("bash", [wrapper, "W07", "bash", "-c", "exit 19"]);
  assert.equal(result.status, 19);
});
