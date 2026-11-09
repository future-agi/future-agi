// Package-free W14 wiring checks. Never render Compose or print its contents.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import * as selection from "./mcp-subfeature-selection.mjs";

const root = new URL("../../", import.meta.url);
const keys = [
  "AGENTCC_MCP_TOOL_CALL_EXPLORER_ENABLED",
  "AGENTCC_MCP_TOOL_CALL_DOWNLOAD_ENABLED",
];

for (const file of [
  "docker-compose.yml",
  "docker-compose.distributed.yml",
  "e2e/stack/docker-compose.mcp-e2e.yml",
]) {
  test(`W14 ${file} forwards both Django switches with false defaults`, () => {
    const lines = readFileSync(new URL(file, root), "utf8").split("\n");
    for (const key of keys) {
      const matches = lines.filter((line) =>
        line.trimStart().startsWith(`${key}:`),
      );
      // Only booleans/key names reach assertion diagnostics, never file values.
      assert.equal(matches.length === 1, true, `${key}: exactly one entry`);
      assert.equal(
        matches[0]?.trim() === `${key}: \${${key}:-false}`,
        true,
        `${key}: false-default forwarding`,
      );
    }
  });
}

test("W14 operator copy keeps NEW as a comment, not in either description", () => {
  const text = readFileSync(new URL("deploy/env-reference.toml", root), "utf8");
  assert.equal(text.includes("# NEW: W14 deployment switches"), true);
  for (const key of keys) {
    const block = text
      .split(`keys = ["${key}"]`)[1]
      ?.split("[[part.section.row]]")[0];
    assert.equal(Boolean(block), true, `${key}: documented`);
    assert.equal(
      block.includes("Enables the NEW MCP"),
      false,
      `${key}: no internal marker in operator copy`,
    );
    assert.equal(
      block.includes('default = "false"'),
      true,
      `${key}: default remains false`,
    );
  }
  assert.equal(
    text.includes("these keys have no separate Compose interpolation entry"),
    false,
  );
});

test("W14 flow selection excludes ON flows during W07 and OFF flows during W14", () => {
  assert.equal(typeof selection.mcpSubfeatureExclusionPattern, "function");
  const pattern = (name) =>
    selection.mcpSubfeatureExclusionPattern({
      E2E_MCP_SUBFEATURE_SELECTION: name,
      E2E_STACK: "standalone",
      E2E_GATEWAY_PROFILE: "mcp",
    });
  for (const id of ["GW-E2E-025", "GW-E2E-031"]) {
    const title = `${id} @gw-profile-mcp @mcp-subfeature-W14`;
    assert.equal(pattern("W14").test(title), false);
    assert.equal(pattern("W07").test(title), true);
    assert.equal(selection.mcpSubfeatureExclusionPattern({}).test(title), true);
  }
  const off = "W14-OFF @gw-profile-mcp @mcp-subfeature-W07";
  assert.equal(pattern("W07").test(off), false);
  assert.equal(pattern("W14").test(off), true);
  assert.equal(pattern("W14").test("legacy MCP flow @gw-profile-mcp"), false);
  assert.equal(pattern("W14").test("@mcp-subfeature-W140"), true);
});

test("W14 admission and both flows consume the selection tags", () => {
  const admission = readFileSync(
    new URL("e2e/lib/quarantine.ts", root),
    "utf8",
  );
  assert.equal(
    admission.includes("patterns.push(mcpSubfeatureExclusionPattern().source)"),
    true,
  );
  for (const name of ["tool-calls", "tool-search"]) {
    const flow = readFileSync(
      new URL(`e2e/flows/gateway/mcp/${name}.spec.ts`, root),
      "utf8",
    );
    assert.equal(
      flow.includes("'@mcp-subfeature-W14'"),
      true,
      `${name}: W14 tag`,
    );
    assert.equal(
      flow.includes("tool_call_explorer: true, tool_call_download: true"),
      true,
      `${name}: serving capability assertion`,
    );
    if (name === "tool-calls") {
      assert.equal(
        flow.includes("'@mcp-subfeature-W07'"),
        true,
        "OFF phase tag",
      );
      assert.equal(
        flow.includes("'mcp_tool_call_explorer_disabled'"),
        true,
        "OFF typed refusal",
      );
    }
  }
});
