#!/usr/bin/env node
// H5: bin/e2e asks this script what a gateway profile mounts and whether the
// checkout can run it at all (e2e/stack/gateway-profiles.json). It prints one
// `key=value` line per item for bin/e2e to read and exits:
//   0 ready: `file=`, then any `overlay=` and `service=` lines
//   2 an unknown profile, or a stack the profile does not run on
//   3 refused: a requirement is missing on this head (stderr names it and the
//     head that has it). Nothing has started: bin/e2e stops before compose.
// Node built-ins only, so `bin/e2e up` needs no yarn install.
import { loadCloudModeProfile } from "./cloud-mode-profile.mjs";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
  "..",
);
export const TABLE_FILE = path.join(
  ROOT,
  "e2e",
  "stack",
  "gateway-profiles.json",
);

export function loadTable(file = TABLE_FILE) {
  const table = JSON.parse(readFileSync(file, "utf8"));
  delete table._comment;
  return table;
}

/** Pure: the missing requirements of `profile` on `stack`, read through `read`
 * (null for a file that does not exist). Throws on an unknown profile or stack. */
export function readiness(table, profile, stack, read) {
  const entry = Object.hasOwn(table, profile) ? table[profile] : undefined;
  if (!entry)
    throw new Error(
      `E2E_GATEWAY_PROFILE=${profile} is not one of: ${Object.keys(table).join(", ")}`,
    );
  if (!entry.stacks.includes(stack)) {
    throw new Error(
      `E2E_GATEWAY_PROFILE=${profile} runs on the ${entry.stacks.join(" or ")} stack only (E2E_STACK=${stack})`,
    );
  }
  const missing = [];
  for (const req of entry.requires) {
    const text = read(req.file);
    if (text === null) missing.push(req.file);
    else if (req.contains !== undefined && !text.includes(req.contains))
      missing.push(`${req.file} contains ${req.contains}`);
  }
  return { entry, missing };
}

const readFrom = (root) => (rel) => {
  const file = path.join(root, rel);
  return existsSync(file) ? readFileSync(file, "utf8") : null;
};

if (
  process.argv[1] &&
  path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  const [profile = "stock", stack = "distributed"] = process.argv.slice(2);
  let result;
  try {
    if (profile === "cloud-mode") {
      const overlay = process.env.E2E_CLOUD_MODE_OVERLAY;
      if (!overlay)
        throw new Error("cloud-mode requires E2E_CLOUD_MODE_OVERLAY");
      const format = existsSync(path.join(overlay, "profile.env"))
        ? "env"
        : "json";
      loadCloudModeProfile(overlay, ROOT, format);
      process.stderr.write(
        "REFUSED cloud-mode: not-accepted; waiting for main acceptance head and job (713)\n",
      );
      process.exit(3);
    }
    result = readiness(loadTable(), profile, stack, readFrom(ROOT));
  } catch (error) {
    process.stderr.write(`${error.message}\n`);
    process.exit(2);
  }
  if (result.missing.length) {
    process.stderr.write(
      `REFUSED gateway profile ${profile} on this head: missing ${result.missing.join("; ")}. ` +
        `Unblocked by: ${result.entry.unblockedBy || "(not named)"}\n`,
    );
    process.exit(3);
  }
  const lines = [
    `file=${result.entry.file}`,
    ...result.entry.overlays.map((o) => `overlay=${o}`),
    ...result.entry.services.map((s) => `service=${s}`),
  ];
  process.stdout.write(`${lines.join("\n")}\n`);
}
