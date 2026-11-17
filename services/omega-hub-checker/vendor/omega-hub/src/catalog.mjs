/**
 * @module @omega/hub/catalog
 *
 * The catalog (`omega-hub-catalog/v1`): every visible entry with what the CLI, the docs sync and the
 * Discussions Action need. A pure function of the files and the commit it is given: no git log, no
 * clock, so a shallow CI checkout and a full clone build the same bytes. Hidden entries are left out.
 */

import { readFileSync } from "node:fs";
import path from "node:path";
import { castOutput, normalizeOutput, parseCast } from "./cast.mjs";
import { checkSelfContained, copyableFiles, digestFiles, isText, langOf, lineCount, walk } from "./files.mjs";
import { readManifest } from "./manifest.mjs";
import { CATALOG_SCHEMA, DEFAULT_CAPTIONS, HUB_DIR, REPO, isObject } from "./schema.mjs";
import { entryIds, filesRootOf } from "./validate.mjs";

const posix = (value) => value.split(path.sep).join("/");

/** Files in the order the Files tab shows them: `files` globs first, then the run entry, then the rest. */
export function orderFiles(files, manifest, runFile) {
  const order = Array.isArray(manifest.files) ? manifest.files : [];
  const rank = (rel) => {
    const at = order.findIndex((pattern) => (pattern.includes("*") ? new RegExp(`^${pattern.replace(/[.+?^${}()|[\]\\]/g, "\\$&").replace(/\*\*/g, "\u0000").replace(/\*/g, "[^/]*").replace(/\u0000/g, ".*")}$`).test(rel) : pattern === rel));
    if (at >= 0) return at;
    return rel === runFile ? order.length : order.length + 1;
  };
  return [...files].sort((left, right) => rank(left.rel) - rank(right.rel) || (left.rel < right.rel ? -1 : left.rel > right.rel ? 1 : 0));
}

/** One catalog entry from a manifest and its folder. */
export function catalogEntry(repoRoot, entryDir, manifest, remixes = []) {
  const filesRoot = filesRootOf(repoRoot, entryDir, manifest);
  const castRel = typeof manifest.demo?.cast === "string" ? manifest.demo.cast : "demo.cast";
  const walked = walk(filesRoot);
  const copyable = filesRoot === entryDir ? copyableFiles(walked, castRel) : walked.files;
  const keylessRun = Array.isArray(manifest.run?.keyless) ? manifest.run.keyless : [];
  const runFile = keylessRun.find((arg, index) => index > 0 && /\.(?:ts|mts|mjs|js)$/.test(arg)) ?? null;
  const ordered = orderFiles(copyable, manifest, runFile);
  const imports = new Set(checkSelfContained(walk(entryDir), entryDir, "").imports);
  if (filesRoot !== entryDir) for (const specifier of checkSelfContained(walked, filesRoot, "").imports) imports.add(specifier);
  const entryPath = posix(path.relative(repoRoot, entryDir));
  let cast = null;
  try {
    const parsed = parseCast(readFileSync(path.join(entryDir, castRel), "utf8"));
    const output = normalizeOutput(castOutput(parsed));
    cast = { path: `${entryPath}/${castRel}`, lines: output === "" ? 0 : output.split("\n").length, caption: manifest.demo?.caption ?? DEFAULT_CAPTIONS[manifest.models?.keyless] ?? DEFAULT_CAPTIONS.none };
  } catch {
    cast = null;
  }
  const live = Array.isArray(manifest.models?.live) ? manifest.models.live : [];
  return {
    id: manifest.id,
    title: manifest.title,
    tagline: manifest.tagline,
    kind: manifest.kind,
    cover: manifest.cover,
    author: { github: manifest.author?.github, ...(manifest.author?.name ? { name: manifest.author.name } : {}), kind: manifest.author?.kind },
    tags: manifest.tags ?? [],
    difficulty: manifest.difficulty,
    license: manifest.license,
    added: manifest.added,
    remix_of: manifest.remix_of ?? null,
    remixes,
    models: {
      keyless: manifest.models?.keyless,
      live: live.map((item) => ({ model: item.model, keys: item.keys ?? [], checked: item.checked ?? null })),
      maxCalls: manifest.models?.maxCalls ?? null
    },
    keys: (Array.isArray(manifest.keys) ? manifest.keys : []).map((key) => ({ name: key.name, why: key.why, get: key.get, for: key.for, secret: key.secret })),
    requires: isObject(manifest.requires) ? manifest.requires : {},
    run: { keyless: keylessRun, live: Array.isArray(manifest.run?.live) ? manifest.run.live : keylessRun },
    test: manifest.test,
    install: isObject(manifest.install) ? manifest.install : null,
    featured: isObject(manifest.featured) ? manifest.featured : null,
    imports: [...imports].sort(),
    path: entryPath,
    filesRoot: posix(path.relative(repoRoot, filesRoot)),
    files: ordered.map((file) => {
      const text = isText(file.rel) ? readFileSync(file.abs, "utf8") : null;
      return { path: file.rel, bytes: file.bytes, lines: text === null ? 0 : lineCount(text), lang: langOf(file.rel) };
    }),
    digest: digestFiles(copyable),
    cast
  };
}

/**
 * The catalog of a repository at `commit`. `options.hubDir` defaults to `hub`. Entries whose
 * manifest does not parse are left out (`omega hub check` reports them).
 */
export function buildCatalog(repoRoot, { commit = "0".repeat(40), repo = REPO, hubDir = HUB_DIR } = {}) {
  const manifests = new Map();
  for (const id of entryIds(repoRoot, hubDir)) {
    const { manifest } = readManifest(path.join(repoRoot, hubDir, id));
    if (manifest && manifest.hidden !== true && manifest.id === id) manifests.set(id, manifest);
  }
  const remixesOf = (id) => [...manifests.values()].filter((manifest) => manifest.remix_of === id).map((manifest) => manifest.id).sort();
  const entries = [...manifests.keys()].sort().map((id) => catalogEntry(repoRoot, path.join(repoRoot, hubDir, id), manifests.get(id), remixesOf(id)));
  return { schema: CATALOG_SCHEMA, repo, commit, entries };
}

/** Search the catalog: case-insensitive over id, title, tagline, tags and author, ranked title > tags > tagline. */
export function searchCatalog(entries, words) {
  const terms = String(words).toLowerCase().split(/\s+/).filter(Boolean);
  if (terms.length === 0) return [...entries];
  const scored = [];
  for (const entry of entries) {
    let score = 0;
    let matchedAll = true;
    for (const term of terms) {
      let best = 0;
      if (entry.title.toLowerCase().includes(term) || entry.id.includes(term)) best = 3;
      else if (entry.tags.some((tag) => tag.includes(term)) || entry.kind === term) best = 2;
      else if (entry.tagline.toLowerCase().includes(term) || String(entry.author.github).toLowerCase().includes(term) || String(entry.author.name ?? "").toLowerCase().includes(term)) best = 1;
      if (best === 0) matchedAll = false;
      score += best;
    }
    if (matchedAll) scored.push({ entry, score });
  }
  return scored.sort((left, right) => right.score - left.score || (left.entry.id < right.entry.id ? -1 : 1)).map((item) => item.entry);
}
