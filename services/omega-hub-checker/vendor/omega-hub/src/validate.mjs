/**
 * @module @omega/hub/validate
 *
 * The static rules R1-R13 over one entry or every entry of a repository. R14 (the test passes) and
 * R15 (the recording is fresh) need to run code, so the CLI's `omega hub check` adds them.
 */

import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import path from "node:path";
import { checkCast } from "./cast.mjs";
import { checkEnvExample, checkFolder, checkKeys, checkSelfContained, walk } from "./files.mjs";
import { checkDates, checkIdFolder, checkLicense, checkRemix, checkShape, checkSource, findRemixCycles, readManifest } from "./manifest.mjs";
import { checkProse, checkReadme, readProseRules } from "./prose.mjs";
import { HUB_DIR, README_FILE, finding, todayUtc } from "./schema.mjs";

/** The entry folders under `<repoRoot>/hub`: every directory not starting with `_` or `.`, sorted. */
export function entryIds(repoRoot, hubDir = HUB_DIR) {
  const root = path.join(repoRoot, hubDir);
  if (!existsSync(root)) return [];
  return readdirSync(root, { withFileTypes: true })
    .filter((entry) => entry.isDirectory() && !entry.name.startsWith("_") && !entry.name.startsWith("."))
    .map((entry) => entry.name)
    .sort();
}

/** The folder an entry's copyable files come from: its `source` (maintainer only) or the entry folder. */
export function filesRootOf(repoRoot, entryDir, manifest) {
  if (typeof manifest?.source === "string" && manifest.author?.kind === "maintainer") {
    const folder = path.join(repoRoot, manifest.source);
    if (existsSync(folder) && statSync(folder).isDirectory()) return folder;
  }
  return entryDir;
}

/**
 * R1-R13 on one entry folder. `options`: `{ today, ids (Set of every entry id), words (prose
 * matchers), where (path used in findings) }`. Returns `{ id, manifest, findings, imports }`.
 */
export function validateEntry(repoRoot, entryDir, options = {}) {
  const folderName = path.basename(entryDir);
  const where = options.where ?? path.relative(repoRoot, entryDir).split(path.sep).join("/");
  const manifestFile = `${where}/omega.hub.json`;
  const today = options.today ?? todayUtc();
  const words = options.words ?? readProseRules(repoRoot);
  const { manifest, findings } = readManifest(entryDir, manifestFile);
  if (!manifest) return { id: folderName, manifest: null, findings, imports: [] };

  findings.push(...checkShape(manifest, manifestFile));
  findings.push(...checkIdFolder(manifest, folderName, manifestFile));

  const walked = walk(entryDir);
  const filesRoot = filesRootOf(repoRoot, entryDir, manifest);
  findings.push(...checkFolder(manifest, walked, where, filesRoot));
  findings.push(...checkEnvExample(manifest, entryDir, where));
  const own = checkSelfContained(walked, entryDir, where);
  findings.push(...own.findings);
  const imports = new Set(own.imports);
  findings.push(...checkKeys(manifest, walked, where));

  const readmePath = path.join(entryDir, README_FILE);
  const readme = existsSync(readmePath) ? readFileSync(readmePath, "utf8") : null;
  findings.push(...checkProse(manifest, readme, where, words));
  findings.push(...checkReadme(manifest, entryDir, where));

  const castRel = manifest.demo?.cast;
  if (typeof castRel === "string" && existsSync(path.join(entryDir, castRel))) {
    findings.push(...checkCast(readFileSync(path.join(entryDir, castRel), "utf8"), `${where}/${castRel}`));
  }

  findings.push(...checkRemix(manifest, options.ids ?? new Set(), manifestFile));
  findings.push(...checkSource(manifest, repoRoot, manifestFile));
  if (filesRoot !== entryDir) {
    const sourceWhere = path.relative(repoRoot, filesRoot).split(path.sep).join("/");
    const sourceWalked = walk(filesRoot);
    const source = checkSelfContained(sourceWalked, filesRoot, sourceWhere);
    findings.push(...source.findings.map((item) => ({ ...item, rule: "R11", code: "source" })));
    for (const specifier of source.imports) imports.add(specifier);
    for (const rel of [...sourceWalked.symlinks, ...sourceWalked.nodeModules]) {
      findings.push(finding("R11", `${sourceWhere}/${rel}`, "the source folder holds a symlink or node_modules", "remove it"));
    }
    for (const file of sourceWalked.files) {
      if (/^\.env(?:\..*)?$/.test(path.posix.basename(file.rel)) && !file.rel.endsWith(".env.example")) {
        findings.push(finding("R11", `${sourceWhere}/${file.rel}`, "the source folder holds an env file", "delete it"));
      }
    }
  }
  findings.push(...checkDates(manifest, today, manifestFile));
  findings.push(...checkLicense(manifest, manifestFile));
  return { id: folderName, manifest, findings, imports: [...imports].sort() };
}

/**
 * R1-R13 on every entry (or the ids given). Adds the cross-entry rules: remix cycles (R10).
 * Returns `{ entries: [{ id, manifest, findings, imports }], findings }`.
 */
export function validateAll(repoRoot, options = {}) {
  const all = entryIds(repoRoot, options.hubDir);
  const ids = new Set(all);
  const selected = options.ids && options.ids.length > 0 ? options.ids : all;
  const words = options.words ?? readProseRules(repoRoot);
  const entries = [];
  for (const id of selected) {
    const entryDir = path.join(repoRoot, options.hubDir ?? HUB_DIR, id);
    if (!existsSync(entryDir)) {
      entries.push({ id, manifest: null, findings: [finding("R2", `${options.hubDir ?? HUB_DIR}/${id}`, `no entry folder ${id}`, "check the id with omega hub list")], imports: [] });
      continue;
    }
    entries.push(validateEntry(repoRoot, entryDir, { ...options, ids, words }));
  }
  const manifests = new Map();
  for (const id of all) {
    const read = readManifest(path.join(repoRoot, options.hubDir ?? HUB_DIR, id));
    if (read.manifest) manifests.set(id, read.manifest);
  }
  const cyclic = findRemixCycles(manifests);
  for (const entry of entries) {
    if (cyclic.has(entry.id)) entry.findings.push(finding("R10", `${options.hubDir ?? HUB_DIR}/${entry.id}/omega.hub.json`, "`remix_of` forms a cycle", "point remix_of at the entry you started from"));
  }
  return { entries, findings: entries.flatMap((entry) => entry.findings) };
}
