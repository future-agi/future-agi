import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import { performance } from "node:perf_hooks";

// Keep the complete source contract outside the DOM test worker. The collector
// parses the entire frontend and backend; it needs native Node, not jsdom or
// Vite's module runner. No cached inventory or subset replaces the live scan.
export function assertOfflineIconInventory(inventory, registry) {
  assert.ok(inventory.names.length > 1000, "Expected the full icon inventory");
  for (const name of [
    "mdi:database-plus",
    "mdi:bug-check-outline",
    "mdi:compare-arrows",
    "mdi:star",
  ]) {
    assert.ok(inventory.backend.names.includes(name), `Backend icon: ${name}`);
  }
  assert.ok(
    inventory.backend.sources.some(({ file }) =>
      file.includes("builtin_skills/"),
    ),
    "Expected backend skill icon sources",
  );
  assert.deepEqual(inventory.unresolved, [], "Unresolved computed icon names");
  assert.deepEqual(
    inventory.names.filter((name) => !registry[name]),
    [],
    "Icons missing from the offline registry",
  );
  assert.ok(
    inventory.templates.some((v) => v.names.includes("circle-flags:us")),
    "Expected computed country flags",
  );
  assert.ok(
    inventory.templates.some((v) =>
      v.names.includes("fluent:chevron-up-16-filled"),
    ),
    "Expected computed chevrons",
  );
}

export async function checkOfflineIconSource(root) {
  const started = performance.now();
  const { collectOfflineIcons } = await import("./collect-offline-icons.mjs");
  const inventory = collectOfflineIcons(root);
  const registryFile = path.join(
    root,
    "src/components/iconify/offline-icons.json",
  );
  const registry = fs.existsSync(registryFile)
    ? JSON.parse(fs.readFileSync(registryFile, "utf8"))
    : {};
  assertOfflineIconInventory(inventory, registry);
  return {
    status: "PASS",
    names: inventory.names.length,
    backendNames: inventory.backend.names.length,
    milliseconds: performance.now() - started,
  };
}

if (
  process.argv[1] &&
  path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  const root =
    process.argv[2] || process.env.OFFLINE_ASSET_SOURCE_ROOT || process.cwd();
  console.log(JSON.stringify(await checkOfflineIconSource(root)));
}
