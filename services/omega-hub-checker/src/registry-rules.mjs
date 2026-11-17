/**
 * Step 2, manifest and format (SPEC §4.2 row 2): the vendored `@omega/hub` R1-R13 on a temporary
 * hub tree, plus the registry rules U1-U8. The vendored judge is pinned; nothing from the submission
 * is ever imported or executed here.
 */
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { FIELDS, validateEntry } from "../vendor/omega-hub/src/index.mjs";
import { block } from "./findings.mjs";

const VENDOR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "vendor");
const RESERVED = JSON.parse(readFileSync(path.join(VENDOR, "reserved.json"), "utf8"));
const PROSE_RULES = readFileSync(path.join(VENDOR, "omega-hub", "prose-rules.json"));
// Strict SemVer core (no leading zeros), at most 6 digits a part: the same pattern as Django's
// omega_hub/services/refs.py SEMVER, so the checker never passes a version Django refuses to publish.
const PART = "(0|[1-9][0-9]{0,5})";
const VERSION = `${PART}\\.${PART}\\.${PART}`;
export const SEMVER = new RegExp(`^${VERSION}$`);
export const REF = new RegExp(`^([a-z][a-z0-9-]{1,38})\\/([a-z][a-z0-9-]{2,39})@(${VERSION})$`);
const PARTS = ["network", "files", "shell", "background", "credentials"];
const normalise = (word) => String(word).toLowerCase().replace(/[\s_-]+/g, "");
const STATUS_WORDS = new Set(RESERVED.statusWords.map(normalise));
const TAG_WORDS = new Set(RESERVED.tagOnlyWords.map(normalise));

/** Lay the entry out as `<root>/hub/<id>/` and `<root>/<source>/`, the shape `filesRootOf` expects. */
export function writeTree(root, { id, source, entry, sourceFiles }, manifestForValidation) {
  const write = (base, map) => {
    for (const [rel, data] of map) {
      const file = path.join(base, rel);
      mkdirSync(path.dirname(file), { recursive: true });
      writeFileSync(file, data);
    }
  };
  const entryDir = path.join(root, "hub", id);
  write(entryDir, entry);
  if (manifestForValidation) writeFileSync(path.join(entryDir, "omega.hub.json"), `${JSON.stringify(manifestForValidation, null, 2)}\n`);
  if (source) write(path.join(root, source), sourceFiles);
  mkdirSync(path.join(root, "scripts", "docs", "gates"), { recursive: true });
  writeFileSync(path.join(root, "scripts", "docs", "gates", "prose-rules.json"), PROSE_RULES);
  return entryDir;
}

/**
 * The manifest the pinned R1-R13 judge sees: registry-only fields it predates (`version`,
 * `disclosure`) are checked by U1/U2 instead, and a registry `remix_of` (publisher/name@version) by U5.
 */
export function forValidation(manifest) {
  const copy = { ...manifest };
  for (const field of ["version", "disclosure"]) if (!FIELDS.includes(field)) delete copy[field];
  if (typeof copy.remix_of === "string" && copy.remix_of.includes("/")) delete copy.remix_of;
  return copy;
}

export function disclosureErrors(disclosure) {
  if (disclosure === null || typeof disclosure !== "object" || Array.isArray(disclosure)) return ["`disclosure` is missing"];
  const errors = [];
  const extra = Object.keys(disclosure).filter((key) => key !== "summary" && !PARTS.includes(key));
  if (extra.length) errors.push(`unknown disclosure fields: ${extra.join(", ")}`);
  const length = (value) => (typeof value === "string" ? value.trim().length : -1);
  if (!(length(disclosure.summary) >= 20 && length(disclosure.summary) <= 200)) errors.push("disclosure.summary must be 20-200 characters");
  for (const part of PARTS) {
    const max = ["network", "files", "credentials"].includes(part) ? 1200 : 300;
    if (!(length(disclosure[part]) >= 4 && length(disclosure[part]) <= max)) errors.push(`disclosure.${part} must be 4-${max} characters`);
  }
  return errors;
}

export function tagIsReserved(tag) {
  const word = normalise(tag);
  return STATUS_WORDS.has(word) || TAG_WORDS.has(word);
}

/** U1-U8 (and a removed entry), from the manifest and Django's context answers. */
export function registryRules(manifest, { tier, hints = {}, context = {} }) {
  const out = [];
  const fail = (rule, message, fix) => out.push(block("manifest", rule, "omega.hub.json", message, { fix }));
  if (typeof manifest.version !== "string" || !SEMVER.test(manifest.version)) fail("U1", "`version` must be plain MAJOR.MINOR.PATCH with no leading zeros", 'use "version": "1.0.0"');
  else if (context.versionTaken) fail("U1", `version ${manifest.version} is already published for this entry`, "bump version");
  for (const message of disclosureErrors(manifest.disclosure)) fail("U2", message, "write the disclosure: summary, network, files, shell, background, credentials");
  if (manifest.author?.kind === "maintainer" && tier !== "official") fail("U3", '`author.kind: "maintainer"` is for Official entries only', 'set author.kind to "community"');
  if (hints.name && hints.name !== manifest.id) fail("U4", `the manifest id ${manifest.id} is not ${hints.name}, the name sent with the upload`, "publish the folder you meant to");
  if (hints.version && manifest.version && hints.version !== manifest.version) fail("U4", `the manifest version ${manifest.version} is not ${hints.version}`, "publish the folder you meant to");
  if (manifest.remix_of !== undefined && manifest.remix_of !== null) {
    if (typeof manifest.remix_of !== "string" || !REF.test(manifest.remix_of)) fail("U5", "`remix_of` must be publisher/name@version", "use the reference omega hub new --from wrote");
    else if (context.remixTargetOk !== true) fail("U5", `${manifest.remix_of} is not an approved version on the Hub`, "remix an approved version");
    else if (context.remixTargetBlocked) fail("U6", `${manifest.remix_of} is on the blocklist`, "do not remix a removed entry");
  }
  for (const tag of Array.isArray(manifest.tags) ? manifest.tags : []) {
    if (tagIsReserved(tag)) fail("U7", `the tag "${tag}" is a reserved trust word`, "remove it");
  }
  if (context.movedTo) {
    const name = String(context.movedTo).split("/")[1];
    fail("U8", `This entry is now \`${context.movedTo}\`: set \`id\` to \`${name}\``, `set "id": "${name}" and rename the folder`);
  }
  if (context.entryRemoved) fail("U8", "this entry was removed from the Hub", "publish under a new name");
  return out;
}

/** R1-R13 from the vendored judge, split into the manifest step and the licence step (R13). */
export function vendoredRules(unpacked, manifest) {
  const root = mkdtempSync(path.join(tmpdir(), "ohc-validate-"));
  try {
    const entryDir = writeTree(root, unpacked, forValidation(manifest));
    const { findings } = validateEntry(root, entryDir, { ids: new Set([unpacked.id]), where: unpacked.id });
    const toFinding = (item) => block(item.rule === "R13" ? "licence" : "manifest", item.rule, String(item.file).replace(`${unpacked.id}/`, ""), item.message, { fix: item.fix });
    return {
      manifest: findings.filter((item) => item.rule !== "R13").map(toFinding),
      licence: findings.filter((item) => item.rule === "R13").map(toFinding),
    };
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
}
