// Package-free operator-documentation contract; execute on the cloud rig.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const root = new URL("../../", import.meta.url);
const reference = readFileSync(new URL("deploy/env-reference.toml", root), "utf8");
const installation = readFileSync(new URL("INSTALLATION.md", root), "utf8");
const row = (name) => reference.split(`keys = ["${name}"]`)[1]?.split("[[part.section.row]]")[0];

test("MCP core documentation distinguishes YAML enablement from the state key", () => {
  const config = row("AGENTCC_CONFIG_PATH");
  assert.ok(config?.includes("mcp.enabled"));
  assert.match(config, /self-hosted/);
  assert.match(config, /explicit.*false/);
  assert.match(config, /hosted profile.*off/);
  assert.equal(reference.includes('keys = ["AGENTCC_MCP_ENABLED"]'), false);
});

test("MCP state-key documentation preserves key lists and fails closed", () => {
  const state = row("AGENTCC_MCP_STATE_KEYS");
  assert.ok(state, "state key has an operator-reference row");
  assert.match(state, /current key first/);
  assert.match(state, /at least 32 bytes/);
  assert.match(state, /preserve.*existing key list/);
  assert.match(state, /gateway replicas/);
  assert.match(state, /Explicit `mcp.enabled: true` without a valid key refuses startup/);
  assert.match(state, /starts with MCP off/);
  assert.match(state, /Hosted behavior is unchanged/);
});

test("Installation names the MCP YAML switch and state-key prerequisite", () => {
  assert.match(installation, /mcp\.enabled/);
  assert.match(installation, /AGENTCC_MCP_STATE_KEYS/);
  assert.match(installation, /explicit `false` keeps it off/);
  assert.match(installation, /preserve existing keys/);
  assert.match(installation, /without a key the gateway starts with MCP off/);
});
