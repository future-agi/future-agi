/**
 * @module @omega/hub/manifest
 *
 * Reads `omega.hub.json` and checks its shape (R1), its id against its folder (R2), `remix_of` (R10),
 * `source` (R11, existence and owner; its files are checked by files.mjs), dates (R12) and the
 * license (R13). Every finding names its rule, the file and the one fix.
 */

import { existsSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import {
  AUTHOR_KINDS, BIN, COLORS, COUNTS, DIFFICULTIES, GITHUB, ID, INSTALL_KINDS, KEY_NAME, KEY_PURPOSES, KEYLESS, KINDS,
  LENGTHS, LICENSES, MAINTAINER_ACCOUNT, MANIFEST_FILE, MODEL, NODE_RANGE, PLACEHOLDER_AUTHOR, SCHEMA, TAG,
  finding, isCalendarDate, isObject
} from "./schema.mjs";

export const FIELDS = [
  "$schema", "schema", "id", "title", "tagline", "kind", "cover", "author", "tags", "difficulty", "license", "added",
  "remix_of", "models", "keys", "requires", "run", "test", "demo", "install", "source", "files", "featured", "hidden"
];
export const REQUIRED = ["schema", "id", "title", "tagline", "kind", "cover", "author", "tags", "difficulty", "license", "added", "models", "run", "test", "demo"];

/** Read and parse a manifest. Returns `{ manifest, findings }`; `manifest` is null when it cannot be read. */
export function readManifest(entryDir, file = `${path.basename(entryDir)}/${MANIFEST_FILE}`) {
  const manifestPath = path.join(entryDir, MANIFEST_FILE);
  if (!existsSync(manifestPath)) {
    return { manifest: null, findings: [finding("R1", file, `${MANIFEST_FILE} is missing`, "omega hub new <id> writes one")] };
  }
  let manifest;
  try {
    manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
  } catch (error) {
    return { manifest: null, findings: [finding("R1", file, `not valid JSON: ${error instanceof Error ? error.message : String(error)}`, "fix the JSON syntax")] };
  }
  if (!isObject(manifest)) return { manifest: null, findings: [finding("R1", file, "the manifest is one JSON object", "wrap the fields in { }")] };
  return { manifest, findings: [] };
}

const isString = (value) => typeof value === "string";
const lengthIn = (value, [min, max]) => isString(value) && value.trim().length >= min && value.length <= max;
const isHttps = (value) => {
  if (!isString(value) || !/^https:\/\/[^\s/]+\.[^\s/]+/.test(value)) return false;
  try {
    return new URL(value).protocol === "https:";
  } catch {
    return false;
  }
};

/** A path inside the entry: relative, forward slashes, no `..`. */
export function isRelativePath(value) {
  if (!isString(value) || value.length === 0 || value.length > 200) return false;
  if (value.startsWith("/") || value.includes("\\") || /^[a-z][a-z0-9+.-]*:/i.test(value)) return false;
  return !value.split("/").some((part) => part === "..");
}

function isArgv(value) {
  return Array.isArray(value) && value.length > 0 && value.length <= 20 && value.every((item) => isString(item) && item.length > 0 && item.length <= 200);
}

function isEmoji(value) {
  if (!isString(value) || value.length === 0 || value.length > 16) return false;
  const segments = [...new Intl.Segmenter("en", { granularity: "grapheme" }).segment(value)];
  return segments.length === 1 && /\p{Extended_Pictographic}/u.test(value);
}

function unknownKeys(object, allowed) {
  return Object.keys(object).filter((key) => !allowed.includes(key));
}

/** R1: the manifest's shape. */
export function checkShape(manifest, file) {
  const out = [];
  const fail = (message, fix) => out.push(finding("R1", file, message, fix));
  for (const field of REQUIRED) if (!(field in manifest)) fail(`\`${field}\` is required`, `add \`${field}\` (see packages/hub/schema/omega.hub.schema.json)`);
  for (const field of unknownKeys(manifest, FIELDS)) fail(`\`${field}\` is not a hub field`, `remove it, or use one of ${FIELDS.slice(1).join(", ")}`);

  if ("schema" in manifest && manifest.schema !== SCHEMA) fail(`\`schema\` must be "${SCHEMA}"`, `set "schema": "${SCHEMA}"`);
  if ("id" in manifest && !(isString(manifest.id) && ID.test(manifest.id))) fail("`id` must be 3-40 lowercase letters, digits and hyphens, starting with a letter", "rename the id and its folder");
  if ("title" in manifest && !lengthIn(manifest.title, LENGTHS.title)) fail(`\`title\` must have ${LENGTHS.title[0]} to ${LENGTHS.title[1]} characters`, "shorten or lengthen the title");
  if ("tagline" in manifest && !lengthIn(manifest.tagline, LENGTHS.tagline)) fail(`\`tagline\` must have ${LENGTHS.tagline[0]} to ${LENGTHS.tagline[1]} characters`, "write one sentence of 20 to 120 characters");
  if ("kind" in manifest && !KINDS.includes(manifest.kind)) fail(`\`kind\` must be one of ${KINDS.join(", ")}`, "pick the closest kind");
  if ("difficulty" in manifest && !DIFFICULTIES.includes(manifest.difficulty)) fail(`\`difficulty\` must be one of ${DIFFICULTIES.join(", ")}`, "pick one");
  if ("license" in manifest && !isString(manifest.license)) fail("`license` must be a string", 'set "license": "Apache-2.0" or another allowed SPDX id');
  if ("added" in manifest && !isString(manifest.added)) fail("`added` must be a date string, YYYY-MM-DD", "set today's date");
  if ("remix_of" in manifest && !isString(manifest.remix_of)) fail("`remix_of` must be a hub id or a GitHub URL", "set the id you remixed");
  if ("source" in manifest && !isRelativePath(manifest.source)) fail("`source` must be a repository-relative folder", 'for example "skills"');
  if ("hidden" in manifest && typeof manifest.hidden !== "boolean") fail("`hidden` must be true or false", "remove it or set a boolean");
  if ("test" in manifest && !(isRelativePath(manifest.test) && /\.test\.(?:ts|mjs)$/.test(manifest.test))) fail("`test` must name a *.test.ts or *.test.mjs file inside the entry", 'for example "main.test.ts"');
  if ("files" in manifest && !(Array.isArray(manifest.files) && manifest.files.length <= 60 && manifest.files.every(isRelativePath))) fail("`files` is a list of paths or globs inside the entry", "list the files in the order the Files tab shows them");

  const cover = manifest.cover;
  if ("cover" in manifest) {
    if (!isObject(cover)) fail("`cover` must be an object", '{ "emoji": "...", "from": "blue", "to": "indigo" }');
    else {
      if (!isEmoji(cover.emoji)) fail("`cover.emoji` must be one emoji", "pick one emoji");
      for (const side of ["from", "to"]) if (!COLORS.includes(cover[side])) fail(`\`cover.${side}\` must be one of ${COLORS.join(", ")}`, "pick a color");
      for (const key of unknownKeys(cover, ["emoji", "from", "to"])) fail(`\`cover.${key}\` is not a cover field`, "remove it");
    }
  }

  const author = manifest.author;
  if ("author" in manifest) {
    if (!isObject(author)) fail("`author` must be an object", '{ "github": "<handle>", "kind": "community" }');
    else {
      if (author.github === PLACEHOLDER_AUTHOR) fail(`\`author.github\` is still "${PLACEHOLDER_AUTHOR}"`, "set it to your GitHub handle");
      else if (!(isString(author.github) && GITHUB.test(author.github))) fail("`author.github` must be a GitHub account name", "set your GitHub handle");
      if ("name" in author && !lengthIn(author.name, LENGTHS.authorName)) fail("`author.name` must have 1 to 60 characters", "shorten it");
      if (!AUTHOR_KINDS.includes(author.kind)) fail(`\`author.kind\` must be one of ${AUTHOR_KINDS.join(", ")}`, 'use "community" unless you maintain Omega');
      else if (author.kind === "maintainer" && author.github !== MAINTAINER_ACCOUNT) fail(`a maintainer entry's \`author.github\` is "${MAINTAINER_ACCOUNT}"`, 'set "kind": "community"');
      for (const key of unknownKeys(author, ["github", "name", "kind"])) fail(`\`author.${key}\` is not an author field`, "remove it");
    }
  }

  if ("tags" in manifest) {
    const tags = manifest.tags;
    const [min, max] = COUNTS.tags;
    if (!Array.isArray(tags) || tags.length < min || tags.length > max || new Set(tags).size !== tags.length || !tags.every((tag) => isString(tag) && TAG.test(tag))) {
      fail(`\`tags\` must be ${min} to ${max} distinct lowercase tags (letters, digits, hyphens; 2-24 characters)`, 'for example ["chat", "web"]');
    }
  }

  checkModels(manifest, fail);
  checkKeys(manifest, fail);
  checkRequires(manifest, fail);
  checkRunAndDemo(manifest, fail);
  checkInstall(manifest, fail);
  if ("featured" in manifest) {
    const featured = manifest.featured;
    if (!isObject(featured) || !isString(featured.week) || !lengthIn(featured.why, LENGTHS.featuredWhy) || unknownKeys(featured, ["week", "why"]).length > 0) {
      fail("`featured` is { week: a Monday, YYYY-MM-DD; why: 20-140 characters }", "only a maintainer sets it");
    }
  }
  return out;
}

function checkModels(manifest, fail) {
  if (!("models" in manifest)) return;
  const models = manifest.models;
  if (!isObject(models)) return fail("`models` must be an object", '{ "keyless": "scripted", "live": [] }');
  if (!KEYLESS.includes(models.keyless)) fail(`\`models.keyless\` must be one of ${KEYLESS.join(", ")}`, "say what plays the model when no key is set");
  for (const key of unknownKeys(models, ["keyless", "live", "maxCalls"])) fail(`\`models.${key}\` is not a models field`, "remove it");
  const live = models.live ?? [];
  if (!Array.isArray(live) || live.length > COUNTS.live[1]) return fail("`models.live` must be a list of at most 8 models", "list the models the code is written for");
  live.forEach((item, index) => {
    const where = `models.live[${index}]`;
    if (!isObject(item)) return fail(`\`${where}\` must be an object`, '{ "model": "openai:gpt-4o-mini", "keys": ["OPENAI_API_KEY"] }');
    if (!(isString(item.model) && MODEL.test(item.model))) fail(`\`${where}.model\` must be vendor:model`, "for example openai:gpt-4o-mini");
    if (!(Array.isArray(item.keys) && item.keys.every((name) => isString(name) && KEY_NAME.test(name)) && new Set(item.keys).size === item.keys.length)) fail(`\`${where}.keys\` must list environment variable names`, 'for example ["OPENAI_API_KEY"]');
    if ("checked" in item && !isString(item.checked)) fail(`\`${where}.checked\` must be a date`, "only omega hub check --live <model> --stamp writes it");
    for (const key of unknownKeys(item, ["model", "keys", "checked"])) fail(`\`${where}.${key}\` is not a field`, "remove it");
  });
  if (new Set(live.map((item) => item?.model)).size !== live.length) fail("`models.live` lists a model twice", "remove the duplicate");
  const [min, max] = COUNTS.maxCalls;
  if (live.length > 0 && !(Number.isInteger(models.maxCalls) && models.maxCalls >= min && models.maxCalls <= max)) fail(`\`models.maxCalls\` must be an integer from ${min} to ${max} when \`models.live\` lists a model`, "count the model calls one live run makes");
  if (live.length === 0 && "maxCalls" in models && !(Number.isInteger(models.maxCalls) && models.maxCalls >= min && models.maxCalls <= max)) fail(`\`models.maxCalls\` must be an integer from ${min} to ${max}`, "remove it");
}

function checkKeys(manifest, fail) {
  if (!("keys" in manifest)) return;
  const keys = manifest.keys;
  if (!Array.isArray(keys) || keys.length > 16) return fail("`keys` must be a list of at most 16 keys", '[{ "name": "OPENAI_API_KEY", "why": "...", "get": "https://...", "for": "live", "secret": true }]');
  keys.forEach((key, index) => {
    const where = `keys[${index}]`;
    if (!isObject(key)) return fail(`\`${where}\` must be an object`, "name, why, get, for, secret");
    if (!(isString(key.name) && KEY_NAME.test(key.name))) fail(`\`${where}.name\` must be an UPPER_SNAKE environment variable name`, "for example OPENAI_API_KEY");
    if (!lengthIn(key.why, LENGTHS.keyWhy)) fail(`\`${where}.why\` must have 10 to 100 characters`, "say what the key is used for");
    if (!isHttps(key.get)) fail(`\`${where}.get\` must be an https URL`, "link the page where the key is created");
    if (!KEY_PURPOSES.includes(key.for)) fail(`\`${where}.for\` must be "live" or "service"`, "live: a model key; service: a key for a real service");
    if (typeof key.secret !== "boolean") fail(`\`${where}.secret\` must be true or false`, "true for API keys and tokens");
    for (const extra of unknownKeys(key, ["name", "why", "get", "for", "secret"])) fail(`\`${where}.${extra}\` is not a key field (values never go in the manifest)`, "remove it");
  });
  const names = keys.filter(isObject).map((key) => key.name);
  if (new Set(names).size !== names.length) fail("`keys` names a key twice", "remove the duplicate");
}

function checkRequires(manifest, fail) {
  if (!("requires" in manifest)) return;
  const requires = manifest.requires;
  if (!isObject(requires)) return fail("`requires` must be an object", '{ "node": ">=22.13" }');
  if ("node" in requires && !(isString(requires.node) && NODE_RANGE.test(requires.node))) fail("`requires.node` must look like >=22.13", "set the lowest Node version");
  if ("bins" in requires && !(Array.isArray(requires.bins) && requires.bins.length <= 8 && requires.bins.every((bin) => isString(bin) && BIN.test(bin)))) fail("`requires.bins` must list program names", 'for example ["bash"]');
  if ("playwright" in requires && typeof requires.playwright !== "boolean") fail("`requires.playwright` must be true or false", "remove it or set a boolean");
  for (const key of unknownKeys(requires, ["node", "bins", "playwright"])) fail(`\`requires.${key}\` is not a requires field`, "remove it");
}

function checkRunAndDemo(manifest, fail) {
  if ("run" in manifest) {
    const run = manifest.run;
    if (!isObject(run)) fail("`run` must be an object", '{ "keyless": ["node", "--import", "tsx", "main.ts"] }');
    else {
      for (const mode of ["keyless", "live"]) {
        if (!(mode in run)) continue;
        if (!isArgv(run[mode]) || run[mode][0] !== "node") fail(`\`run.${mode}\` must be an argv whose first item is "node"`, '["node", "--import", "tsx", "main.ts"]');
      }
      if (!("keyless" in run)) fail("`run.keyless` is required", '["node", "--import", "tsx", "main.ts"]');
      for (const key of unknownKeys(run, ["keyless", "live"])) fail(`\`run.${key}\` is not a run field`, "remove it");
    }
  }
  if ("demo" in manifest) {
    const demo = manifest.demo;
    if (!isObject(demo)) return fail("`demo` must be an object", '{ "cast": "demo.cast" }');
    if (!(isRelativePath(demo.cast) && demo.cast.endsWith(".cast"))) fail("`demo.cast` must name a .cast file inside the entry", '"demo.cast"');
    if ("caption" in demo && !lengthIn(demo.caption, LENGTHS.caption)) fail("`demo.caption` must have 20 to 140 characters", "remove it to use the default caption");
    if ("redact" in demo) {
      const valid = Array.isArray(demo.redact) && demo.redact.length <= COUNTS.redact[1] && demo.redact.every((source) => {
        try {
          return isString(source) && source.length > 0 && Boolean(new RegExp(source, "g"));
        } catch {
          return false;
        }
      });
      if (!valid) fail("`demo.redact` must list at most 8 regular expressions", "escape special characters");
    }
    for (const key of unknownKeys(demo, ["cast", "caption", "redact"])) fail(`\`demo.${key}\` is not a demo field`, "remove it");
  }
}

function checkInstall(manifest, fail) {
  const needed = INSTALL_KINDS.includes(manifest.kind);
  if (!("install" in manifest)) {
    if (needed) fail(`a ${manifest.kind} entry needs \`install\``, manifest.kind === "skill" ? '{ "skillpack": "skills" }' : manifest.kind === "plugin" ? '{ "plugin": "plugin" }' : '{ "mcp": { "name": "notes", "command": ["node", "server.mjs"] } }');
    return;
  }
  const install = manifest.install;
  if (!isObject(install)) return fail("`install` must be an object", '{ "skillpack": "skills" }');
  for (const key of ["plugin", "skillpack"]) if (key in install && !isRelativePath(install[key])) fail(`\`install.${key}\` must be a folder inside the entry`, '"skills"');
  if ("mcp" in install && !(isObject(install.mcp) && isString(install.mcp.name) && /^[a-z][a-z0-9-]{1,39}$/.test(install.mcp.name) && isArgv(install.mcp.command))) fail("`install.mcp` is { name, command: argv }", '{ "name": "notes", "command": ["node", "server.mjs"] }');
  for (const key of unknownKeys(install, ["plugin", "skillpack", "mcp"])) fail(`\`install.${key}\` is not an install field`, "remove it");
  const want = { plugin: "plugin", skill: "skillpack", mcp: "mcp" }[manifest.kind];
  if (want && !(want in install)) fail(`a ${manifest.kind} entry needs \`install.${want}\``, `add install.${want}`);
}

/** R2: the id is the folder name. */
export function checkIdFolder(manifest, folderName, file) {
  if (isString(manifest.id) && manifest.id !== folderName) return [finding("R2", file, `\`id\` "${manifest.id}" differs from its folder "${folderName}"`, `rename the folder to hub/${manifest.id}, or set "id": "${folderName}"`)];
  return [];
}

/** R10: `remix_of` names another entry or a GitHub URL. Cycles are found across entries by findRemixCycles. */
export function checkRemix(manifest, ids, file) {
  const value = manifest.remix_of;
  if (!isString(value)) return [];
  if (value.startsWith("https://")) {
    return /^https:\/\/github\.com\/[^\s]+$/.test(value) ? [] : [finding("R10", file, "`remix_of` URLs must be on https://github.com/", "link the GitHub repository")];
  }
  if (value === manifest.id) return [finding("R10", file, "`remix_of` names the entry itself", "name the entry you remixed")];
  if (!ids.has(value)) return [finding("R10", file, `\`remix_of\` names "${value}", which is not a hub entry`, "name an existing hub id or a github.com URL")];
  return [];
}

/** Every id whose remix_of chain loops back to itself. `manifests` maps id to manifest. */
export function findRemixCycles(manifests) {
  const cyclic = new Set();
  for (const start of manifests.keys()) {
    const seen = new Set([start]);
    let current = manifests.get(start)?.remix_of;
    while (typeof current === "string" && manifests.has(current)) {
      if (seen.has(current)) {
        if (current === start) cyclic.add(start);
        break;
      }
      seen.add(current);
      current = manifests.get(current)?.remix_of;
    }
  }
  return cyclic;
}

/** R11: `source` only on maintainer entries, naming an existing folder (its files are checked by R5 in files.mjs). */
export function checkSource(manifest, repoRoot, file) {
  if (!isString(manifest.source)) return [];
  const out = [];
  if (manifest.author?.kind !== "maintainer") out.push(finding("R11", file, "`source` is for maintainer entries only", "put the files in the entry folder and remove `source`"));
  const folder = path.join(repoRoot, manifest.source);
  if (!existsSync(folder) || !statSync(folder).isDirectory()) out.push(finding("R11", file, `\`source\` names "${manifest.source}", which is not a folder in the repository`, "name an existing folder"));
  return out;
}

const isMonday = (date) => new Date(`${date}T00:00:00Z`).getUTCDay() === 1;

/** R12: dates are real and not in the future; `featured.week` is a Monday. */
export function checkDates(manifest, today, file) {
  const out = [];
  const check = (value, where, monday = false) => {
    if (!isString(value)) return;
    if (!isCalendarDate(value)) out.push(finding("R12", file, `\`${where}\` "${value}" is not a date, YYYY-MM-DD`, "write the date as YYYY-MM-DD"));
    else if (value > today) out.push(finding("R12", file, `\`${where}\` "${value}" is in the future`, `use a date on or before ${today}`));
    else if (monday && !isMonday(value)) out.push(finding("R12", file, `\`${where}\` "${value}" is not a Monday`, "use the Monday that starts the week"));
  };
  check(manifest.added, "added");
  if (isObject(manifest.featured)) check(manifest.featured.week, "featured.week", true);
  const live = isObject(manifest.models) && Array.isArray(manifest.models.live) ? manifest.models.live : [];
  live.forEach((item, index) => check(item?.checked, `models.live[${index}].checked`));
  return out;
}

/** R13: the license is on the allowlist. */
export function checkLicense(manifest, file) {
  if (isString(manifest.license) && !LICENSES.includes(manifest.license)) {
    return [finding("R13", file, `\`license\` "${manifest.license}" is not one of ${LICENSES.join(", ")}`, 'use "Apache-2.0" or "MIT" unless you need another')];
  }
  return [];
}
