/* eslint-env node */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { CONFIG_NAMES } from "./write-runtime-config.mjs";

// Importing CONFIG_NAMES reads the shell list, but does not run the writer CLI.
const entrypoint = readFileSync(
  new URL("../docker-entrypoint.sh", import.meta.url),
  "utf8",
);
const match = entrypoint.match(/^CONFIG_NAMES="\n([\s\S]*?)\n"/m);
assert.ok(match, "Cannot read the container runtime configuration allowlist");
const shellNames = new Set(match[1].trim().split(/\s+/));

const missingNames = (names) => names.filter((name) => !shellNames.has(name));

// VITE_OMEGA_EMBED_PARENT_ORIGINS is excluded: no app runtime reader.
const hostedOnlyNames = new Set(["VITE_OMEGA_EMBED_PARENT_ORIGINS"]);

test("container allowlist contains every app-consumed hosted runtime configuration key", () => {
  assert.deepEqual(
    missingNames(CONFIG_NAMES.filter((name) => !hostedOnlyNames.has(name))),
    [],
    "App-consumed hosted runtime keys must also be configurable in the container",
  );
});

test("container allowlist explicitly includes the Omega Console URL", () => {
  assert.ok(shellNames.has("VITE_OMEGA_CONSOLE_URL"));
});

test("container and hosted allowlists include the grid licence and deploy control plane", () => {
  const names = [
    "VITE_AG_GRID_LICENSE_KEY",
    "VITE_OMEGA_DEPLOY_CONTROL_PLANE",
  ];
  assert.deepEqual(
    missingNames(names),
    [],
    "Prebuilt images must accept the grid licence and deploy control plane at runtime",
  );
  assert.deepEqual(names.filter((name) => !CONFIG_NAMES.includes(name)), []);
});

test("container allowlist includes both Omega landing runtime flags", () => {
  assert.deepEqual(
    missingNames(["VITE_OMEGA_HUB_CONSOLE", "VITE_OMEGA_CONSOLE_REMIX"]),
    [],
    "The runtime flags read by console-landing.js must reach the browser",
  );
});
