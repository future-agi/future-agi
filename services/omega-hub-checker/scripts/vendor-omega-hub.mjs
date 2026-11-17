#!/usr/bin/env node
/**
 * Vendor the judge: copy `@omega/hub` (src, schema, template, package.json, index.d.ts) and the prose
 * rules from an Omega tree exported at one commit, and copy the registry's reserved lists, so the
 * checker never judges a submission with code the submission could influence (SPEC §0.8, §4.1).
 *
 *   node scripts/vendor-omega-hub.mjs --omega-dir <exported tree> --commit <sha>
 */
import { cpSync, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..");
const args = process.argv.slice(2);
const value = (flag) => {
  const at = args.indexOf(flag);
  return at >= 0 ? args[at + 1] : undefined;
};
const omegaDir = value("--omega-dir");
const commit = value("--commit");
if (!omegaDir || !commit || !/^[0-9a-f]{40}$/.test(commit)) {
  console.error("usage: vendor-omega-hub.mjs --omega-dir <exported tree> --commit <40-hex sha>");
  process.exit(2);
}
const hub = path.join(omegaDir, "packages", "hub");
if (!existsSync(path.join(hub, "src", "index.mjs"))) {
  console.error(`${hub} is not the @omega/hub package`);
  process.exit(2);
}
const target = path.join(root, "vendor", "omega-hub");
rmSync(target, { recursive: true, force: true });
mkdirSync(target, { recursive: true });
for (const part of ["src", "schema", "template", "package.json", "index.d.ts", "README.md"]) {
  if (existsSync(path.join(hub, part))) cpSync(path.join(hub, part), path.join(target, part), { recursive: true });
}
cpSync(path.join(omegaDir, "scripts", "docs", "gates", "prose-rules.json"), path.join(target, "prose-rules.json"));
writeFileSync(path.join(target, "VENDORED_FROM"), `${commit}\n`);
const reserved = path.resolve(root, "..", "..", "futureagi", "omega_hub", "rules", "reserved.json");
writeFileSync(path.join(root, "vendor", "reserved.json"), readFileSync(reserved));
console.log(`vendored @omega/hub from ${commit.slice(0, 9)} and reserved.json`);
