/**
 * @module @omega/hub/template
 *
 * What `omega hub new` writes: the files of `template/<folder>/` for the kind (template/kinds.json),
 * with `{{placeholders}}` filled in. A copy of an existing entry (`--from`) is a remix instead.
 */

import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { walk } from "./files.mjs";
import { KINDS, MANIFEST_FILE, PLACEHOLDER_AUTHOR, todayUtc } from "./schema.mjs";

export const TEMPLATE_ROOT = fileURLToPath(new URL("../template/", import.meta.url));

/** The kinds table: per kind, the template folder and the values that fill it. */
export function readKinds(templateRoot = TEMPLATE_ROOT) {
  return JSON.parse(readFileSync(path.join(templateRoot, "kinds.json"), "utf8"));
}

const fill = (text, values) => text.replace(/\{\{(\w+)\}\}/g, (match, key) => (key in values ? values[key] : match));

/**
 * The files of a new entry: `[{ rel, content }]`. `options`: `{ id, kind, author, added }`; the
 * author defaults to the placeholder that `omega hub check` refuses until it is changed.
 */
export function scaffoldFiles({ id, kind = "agent", author = PLACEHOLDER_AUTHOR, added = todayUtc() }, templateRoot = TEMPLATE_ROOT) {
  if (!KINDS.includes(kind)) throw new Error(`unknown kind "${kind}"; one of ${KINDS.join(", ")}`);
  const spec = readKinds(templateRoot)[kind];
  const folder = path.join(templateRoot, spec.folder);
  const values = { id, kind, author, added, title: spec.title, tagline: spec.tagline, emoji: spec.emoji, from: spec.from, to: spec.to, tags: JSON.stringify(spec.tags) };
  return walk(folder).files.map((file) => ({ rel: file.rel, content: fill(readFileSync(file.abs, "utf8"), values) }));
}

/**
 * The files of a remix of `sourceDir`: every file except the recording, with the manifest's `id`
 * and `remix_of` set and `added`, `featured`, `hidden` and every `checked` stamp reset.
 */
export function remixFiles(sourceDir, { id, author = PLACEHOLDER_AUTHOR, added = todayUtc() }) {
  const manifestPath = path.join(sourceDir, MANIFEST_FILE);
  if (!existsSync(manifestPath)) throw new Error(`${sourceDir} has no ${MANIFEST_FILE}`);
  const source = JSON.parse(readFileSync(manifestPath, "utf8"));
  const { featured, hidden, source: sourceFolder, ...rest } = source;
  const manifest = { ...rest, id, remix_of: source.id, added, author: { github: author, kind: "community" } };
  if (Array.isArray(manifest.models?.live)) manifest.models = { ...manifest.models, live: manifest.models.live.map(({ checked, ...item }) => item) };
  const castRel = typeof source.demo?.cast === "string" ? source.demo.cast : "demo.cast";
  const files = walk(sourceDir).files.filter((file) => file.rel !== castRel && file.rel !== MANIFEST_FILE).map((file) => ({ rel: file.rel, content: readFileSync(file.abs, "utf8") }));
  return [{ rel: MANIFEST_FILE, content: `${JSON.stringify(manifest, null, 2)}\n` }, ...files];
}
