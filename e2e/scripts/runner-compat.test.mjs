import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const root = fileURLToPath(new URL("../../", import.meta.url)).replace(
  /\/$/,
  "",
);
// Execute the real CLI against a fake Docker executable. No daemon, network or
// stack is available here. The tuples below are the c99dc71f35 compose contract;
// new profile/pins/replica/mocks overlays must not leak into existing callers.
function invoke(stack, profile, args = ["compose", "config"], project) {
  const dir = mkdtempSync(path.join(os.tmpdir(), "e2e-runner-compat-"));
  try {
    writeFileSync(
      path.join(dir, "docker"),
      "#!/usr/bin/env node\n" +
        "console.log(JSON.stringify({args:process.argv.slice(2),mount:process.env.AGENTCC_CONFIG_PATH}));\n",
      { mode: 0o755 },
    );
    const env = { ...process.env, PATH: `${dir}:${process.env.PATH}` };
    for (const name of [
      "E2E_STACK",
      "E2E_GATEWAY_PROFILE",
      "E2E_COMPOSE_PROJECT",
      "E2E_WALK_PINS",
      "E2E_GATEWAY_REPLICAS",
      "E2E_WALK_MOCKS",
      "E2E_WALK_MOCK_SERVICES",
      "AGENTCC_CONFIG_PATH",
    ])
      delete env[name];
    if (stack !== undefined) env.E2E_STACK = stack;
    if (profile !== undefined) env.E2E_GATEWAY_PROFILE = profile;
    if (project !== undefined) env.E2E_COMPOSE_PROJECT = project;
    return spawnSync("bash", [path.join(root, "bin/e2e"), ...args], {
      env,
      encoding: "utf8",
    });
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

for (const stack of [undefined, "distributed", "standalone"]) {
  for (const profile of [undefined, "", "stock"]) {
    test(`legacy runner: ${stack ?? "unset"} / ${profile ?? "unset"} retains the original compose invocation`, () => {
      const result = invoke(stack, profile);
      assert.equal(result.status, 0, result.stderr);
      const standalone = stack === "standalone";
      assert.deepEqual(JSON.parse(result.stdout), {
        args: [
          "compose",
          "--env-file",
          `${root}/e2e/stack/${standalone ? "standalone-e2e" : "e2e"}.env`,
          "-f",
          `${root}/${standalone ? "docker-compose.yml" : "docker-compose.distributed.yml"}`,
          "-f",
          `${root}/e2e/stack/docker-compose.${standalone ? "standalone-e2e" : "e2e"}.yml`,
          "-p",
          standalone ? "futureagi-e2e-standalone" : "futureagi-e2e",
          "config",
        ],
        mount: "e2e/stack/gateway.e2e.yaml",
      });
    });
  }
}

test("legacy runner: r7alt only changes the standalone gateway mount and preserves forwarded arguments", () => {
  const stock = invoke("standalone", "stock", ["ps", "--all"]);
  const alternate = invoke("standalone", "r7alt", ["ps", "--all"]);
  assert.equal(stock.status, 0, stock.stderr);
  assert.equal(alternate.status, 0, alternate.stderr);
  assert.deepEqual(JSON.parse(alternate.stdout), {
    ...JSON.parse(stock.stdout),
    mount: "e2e/stack/gateway.r7alt.yaml",
  });
  const refused = invoke("distributed", "r7alt");
  assert.notEqual(refused.status, 0);
  assert.equal(refused.stdout, ""); // Docker was never called.
  assert.match(refused.stderr, /standalone stack only/i);
});

test("legacy runner: distributed custom project and unknown-profile refusal remain intact", () => {
  const custom = invoke(
    "distributed",
    "stock",
    ["logs", "--tail", "2", "backend"],
    "compat-project",
  );
  assert.equal(custom.status, 0, custom.stderr);
  const args = JSON.parse(custom.stdout).args;
  assert.equal(args[args.indexOf("-p") + 1], "compat-project");
  assert.deepEqual(args.slice(-4), ["logs", "--tail", "2", "backend"]);
  const refused = invoke(undefined, "not-a-profile");
  assert.equal(refused.status, 2); // documented expanded-profile discovery refusal
  assert.equal(refused.stdout, "");
  assert.match(refused.stderr, /is not one of/);
});
