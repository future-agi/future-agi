/* eslint-env node */
import assert from "node:assert/strict";
import {
  readFileSync,
  mkdtempSync,
  mkdirSync,
  writeFileSync,
  rmSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import { runInNewContext } from "node:vm";
import test from "node:test";

const root = new URL("../", import.meta.url);
const script = new URL("./write-runtime-config.mjs", import.meta.url);
const placeholder = readFileSync(new URL("public/config.js", root), "utf8");
const shellNames = readFileSync(new URL("docker-entrypoint.sh", root), "utf8")
  .match(/^CONFIG_NAMES="\n([\s\S]*?)\n"/m)[1]
  .trim()
  .split(/\s+/);
const names = [
  ...new Set([
    ...shellNames,
    "VITE_OMEGA_CONSOLE_URL",
    "VITE_OMEGA_EMBED_PARENT_ORIGINS",
  ]),
];

function generate(vars, prefix = "DEV_", seed) {
  const dir = mkdtempSync(join(tmpdir(), "runtime-config-"));
  const output = join(dir, "dist/config.js");
  try {
    if (seed !== undefined) {
      mkdirSync(join(dir, "dist"));
      writeFileSync(output, seed);
    }
    const result = spawnSync(
      process.execPath,
      [script.pathname, "--prefix", prefix, "--output", output],
      {
        env: {
          ...process.env,
          RUNTIME_CONFIG_VARIABLES_JSON: JSON.stringify(vars),
        },
        encoding: "utf8",
      },
    );
    let content;
    try {
      content = readFileSync(output, "utf8");
    } catch {
      /* refusal must not write */
    }
    return { ...result, content };
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}
function config(content) {
  const context = { window: {} };
  runInNewContext(content, context);
  return JSON.parse(JSON.stringify(context.window.__FUTURE_AGI_CONFIG__));
}

test("unset variables preserve the shipped placeholder byte for byte", () => {
  const result = generate({});
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.content, placeholder);
});
test("allow-list follows the container plus the two Console origin names", () => {
  const vars = Object.fromEntries(
    names.map((name) => [
      `DEV_${name}`,
      name === "VITE_OMEGA_CONSOLE" ? "false" : "25",
    ]),
  );
  vars.DEV_VITE_UNREVIEWED = "must-not-ship";
  vars.PROD_VITE_HOST_API = "must-not-ship";
  vars.DEV_PRIVATE_KEY = "must-not-ship";
  const result = generate(vars);
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(Object.keys(config(result.content)).sort(), names.sort());
  assert.ok(!result.content.includes("must-not-ship"));
});
test("each environment selects its own variables without cross-environment fallback", () => {
  for (const prefix of ["DEV_", "PROD_", "US2_"]) {
    const result = generate(
      { [`${prefix}VITE_OMEGA_CONSOLE`]: "false", VITE_OMEGA_CONSOLE: "true" },
      prefix,
    );
    assert.equal(result.status, 0, result.stderr);
    assert.deepEqual(config(result.content), { VITE_OMEGA_CONSOLE: "false" });
  }
});
test("values remain literal JavaScript data, never executable source", () => {
  const value = 'quote"\\\n</script><script>throw 1</script>\u2028\u2029';
  const result = generate({ DEV_VITE_HELP_LINK: value });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(config(result.content).VITE_HELP_LINK, value);
  assert.ok(!result.content.includes("</script>"));
});
test("empty variables do not override compiled defaults", () => {
  const result = generate({
    DEV_VITE_HOST_API: "",
    DEV_VITE_OMEGA_CONSOLE: "",
  });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.content, placeholder);
});
test("public origin, path and explicit OFF values are accepted", () => {
  const vars = {
    DEV_VITE_OMEGA_CONSOLE: "false",
    DEV_VITE_OMEGA_CONSOLE_URL: "/omega/console/",
    DEV_VITE_OMEGA_EMBED_PARENT_ORIGINS:
      "https://app.example.invalid,https://dev.example.invalid",
  };
  const result = generate(vars);
  assert.equal(result.status, 0, result.stderr);
  assert.equal(config(result.content).VITE_OMEGA_CONSOLE, "false");
  assert.equal(
    config(result.content).VITE_OMEGA_CONSOLE_URL,
    "/omega/console/",
  );
});
test("secret-shaped public variables are refused without printing their values or writing output", () => {
  const values = [
    "sk-" + "x".repeat(48),
    "ghp_" + "x".repeat(36),
    "AKIA" + "A".repeat(16),
    "-----BEGIN " + "PRIVATE KEY-----",
    "Bearer " + "x".repeat(30),
    "https://user:password@example.invalid",
    "https://example.invalid?api_key=synthetic",
    "eyJhbGciOiJIUzI1NiJ9." + "e30." + "x".repeat(40),
  ];
  for (const value of values) {
    const result = generate({ DEV_VITE_HELP_LINK: value });
    assert.equal(result.status, 1);
    assert.match(result.stderr, /refused DEV_VITE_HELP_LINK/);
    assert.ok(!result.stderr.includes(value));
    assert.equal(result.content, undefined);
  }
});
test("invalid switch and invalid prefix are refused", () => {
  for (const value of ["yes", "TRUE", "1"]) {
    const result = generate({ DEV_VITE_OMEGA_CONSOLE: value });
    assert.equal(result.status, 1);
    assert.match(
      result.stderr,
      /Runtime config refused DEV_VITE_OMEGA_CONSOLE/,
    );
  }
  const result = generate({}, "OTHER_");
  assert.equal(result.status, 1);
  assert.match(result.stderr, /Invalid runtime config environment prefix/);
});
test("validation failure preserves a previously generated file", () => {
  const result = generate(
    { DEV_VITE_HELP_LINK: "Bearer " + "x".repeat(30) },
    "DEV_",
    placeholder,
  );
  assert.equal(result.status, 1);
  assert.match(result.stderr, /Runtime config refused DEV_VITE_HELP_LINK/);
  assert.equal(result.content, placeholder);
});
test("CDN workflows bind public vars, preserve the dev hold and offer config-only dispatch", () => {
  for (const [file, prefix] of [
    ["frontend-dev-cdn.yaml", "DEV_"],
    ["frontend-prod-deploy.yaml", "PROD_"],
    ["frontend-deploy-us2.yaml", "US2_"],
  ]) {
    const workflow = readFileSync(
      new URL(`../.github/workflows/${file}`, root),
      "utf8",
    );
    assert.match(workflow, /runtime_config_only:/);
    assert.match(
      workflow,
      /RUNTIME_CONFIG_VARIABLES_JSON: \$\{\{ toJSON\(vars\) \}\}/,
    );
    assert.ok(
      workflow.includes(
        `node scripts/write-runtime-config.mjs --prefix ${prefix} --output dist/config.js`,
      ),
    );
    assert.match(workflow, /no-cache, no-store, must-revalidate/);
    assert.match(workflow, /["']\/config\.js["']/);
    assert.match(workflow, /if: inputs.runtime_config_only != true/);
    if (prefix === "DEV_")
      assert.match(
        workflow,
        /github.event_name == 'workflow_dispatch' \|\| vars.DEV_CDN_AUTO_DEPLOY != 'false'/,
      );
  }
});
