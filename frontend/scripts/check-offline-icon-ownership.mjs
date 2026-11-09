import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import { performance } from "node:perf_hooks";

// Preserve the complete live ownership contract outside the DOM worker, as
// check-offline-icon-source does for inventory. Never substitute a cached scan.
export function assertOfflineIconOwnership(root, inventory, consumers, owner) {
  assert.deepEqual(
    Object.keys(consumers).sort(),
    inventory.backend.names,
    "Every backend default must have an explicit owner",
  );
  for (const files of Object.values(consumers)) {
    for (const file of files)
      assert.ok(fs.existsSync(path.join(root, file)), `Missing owner: ${file}`);
  }
  const sources = new Map();
  for (const { file, icon } of inventory.sources.filter(({ file }) =>
    file.startsWith("src/"),
  )) {
    if (!sources.has(file)) sources.set(file, new Set());
    sources.get(file).add(icon);
  }
  for (const [file, names] of sources) {
    const owned = owner(
      root,
      file,
      fs.readFileSync(path.join(root, file), "utf8"),
    );
    for (const name of names)
      assert.ok(owned.includes(name), `Missing owned icon: ${file}: ${name}`);
  }
  assert.ok(
    [...sources.keys()].some((file) => file.includes(".stories.")),
    "Story consumers must remain in the inventory",
  );
  // Prototype consumers are scanned regardless of the build importing them.
  assert.deepEqual(inventory.unresolved, [], "Unresolved computed icon names");
  return sources.size;
}

export async function checkOfflineIconOwnership(root) {
  const started = performance.now();
  const { collectOfflineIcons } = await import("./collect-offline-icons.mjs");
  const { iconsForConsumer } = await import("./offline-icon-plugin.mjs");
  const { backendIconConsumers } = await import("./backend-icon-consumers.mjs");
  const inventory = collectOfflineIcons(root);
  const sources = assertOfflineIconOwnership(
    root,
    inventory,
    backendIconConsumers,
    iconsForConsumer,
  );
  return {
    status: "PASS",
    sources,
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
  console.log(JSON.stringify(await checkOfflineIconOwnership(root)));
}
