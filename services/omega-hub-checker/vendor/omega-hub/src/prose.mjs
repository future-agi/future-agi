/**
 * @module @omega/hub/prose
 *
 * The wording rules (R7), ported from the gallery generator: no markup in a title or tagline, no
 * exclamation marks, no emoji (except `cover.emoji`), none of the words the docs prose gate bans
 * (scripts/docs/gates/prose-rules.json, read when the repository has it), and none of the adoption or
 * guarantee claims. And the README rules (R8): required sections in order, size, relative links.
 */

import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { BANNED_CLAIMS, LIMITS, README_FILE, README_SECTIONS, finding, isObject } from "./schema.mjs";

export const PROSE_RULES_PATH = "scripts/docs/gates/prose-rules.json";
const MARKUP = /[`<>[\]\r\n]/;
const EMOJI = /\p{Extended_Pictographic}/u;
const EXCLAMATION = /!(?=\s|$)/;

const escapeRegExp = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/**
 * `[word, RegExp]` pairs for a list of banned words or phrases (same matching as the docs prose gate).
 * @param {readonly string[] | undefined} list
 * @returns {[string, RegExp][]}
 */
export function wordMatchers(list) {
  return (list ?? []).map((entry) => {
    const source = escapeRegExp(String(entry).toLowerCase()).replace(/'/g, "['’]").replace(/ +/g, "\\s+");
    return [String(entry).toLowerCase(), new RegExp(`(?<![\\w-])${source}(?![\\w-])`, "iu")];
  });
}

/** The banned word matchers of a repository (empty when it has no prose rules file). */
export function readProseRules(repoRoot) {
  const file = repoRoot ? path.join(repoRoot, PROSE_RULES_PATH) : null;
  if (!file || !existsSync(file)) return [];
  const rules = JSON.parse(readFileSync(file, "utf8"));
  return wordMatchers([...(rules.bannedWords ?? []), ...(rules.aiTells ?? [])]);
}

/**
 * Wording findings for one piece of text.
 * @param {string} text
 * @param {string} where
 * @param {string} file
 * @param {readonly (readonly [string, RegExp])[]} words
 */
export function wordingFindings(text, where, file, words) {
  const out = [];
  for (const [claim, pattern] of BANNED_CLAIMS) {
    if (pattern.test(text)) out.push(finding("R7", file, `${where} says "${claim}"`, "state what the entry does; never adoption or guarantees"));
  }
  for (const [word, pattern] of words) {
    if (pattern.test(text)) out.push(finding("R7", file, `${where} uses "${word}", which the docs prose rules ban`, `reword it without "${word}"`));
  }
  if (EXCLAMATION.test(text)) out.push(finding("R7", file, `${where} has an exclamation mark`, "end the sentence with a period"));
  if (EMOJI.test(text)) out.push(finding("R7", file, `${where} has an emoji`, "only cover.emoji may be an emoji"));
  return out;
}

/** Markdown with fenced blocks and inline code blanked (line numbers kept). */
export function stripCode(markdown) {
  const lines = [];
  let fence = null;
  for (const line of markdown.split("\n")) {
    const open = /^\s*(`{3,}|~{3,})/.exec(line);
    if (fence) {
      if (open && open[1][0] === fence[0] && open[1].length >= fence.length) fence = null;
      lines.push("");
      continue;
    }
    if (open) {
      fence = open[1];
      lines.push("");
      continue;
    }
    lines.push(line.replace(/`[^`]*`/g, " "));
  }
  return lines.join("\n");
}

/** The prose of a Markdown file: no code, no comments, no link targets, so commands and URLs are not read as words. */
export function proseOf(markdown) {
  return stripCode(markdown).replace(/<!--[\s\S]*?-->/g, " ").replace(/\]\([^)]*\)/g, "]");
}

/** R7 over the manifest's text fields and the README. */
export function checkProse(manifest, readmeText, where, words) {
  const out = [];
  const manifestFile = `${where}/omega.hub.json`;
  for (const field of ["title", "tagline"]) {
    const text = manifest[field];
    if (typeof text !== "string") continue;
    if (MARKUP.test(text)) out.push(finding("R7", manifestFile, `\`${field}\` has markup or a line break`, "plain text only: no backticks, angle or square brackets"));
    out.push(...wordingFindings(text, `\`${field}\``, manifestFile, words));
  }
  if (typeof manifest.author?.name === "string" && MARKUP.test(manifest.author.name)) out.push(finding("R7", manifestFile, "`author.name` has markup", "plain text only"));
  if (isObject(manifest.featured) && typeof manifest.featured.why === "string") out.push(...wordingFindings(manifest.featured.why, "`featured.why`", manifestFile, words));
  if (isObject(manifest.demo) && typeof manifest.demo.caption === "string") out.push(...wordingFindings(manifest.demo.caption, "`demo.caption`", manifestFile, words));
  if (Array.isArray(manifest.keys)) {
    manifest.keys.forEach((key, index) => {
      if (typeof key?.why === "string") out.push(...wordingFindings(key.why, `\`keys[${index}].why\``, manifestFile, words));
    });
  }
  if (typeof readmeText === "string") {
    const prose = proseOf(readmeText).split("\n");
    prose.forEach((line, index) => {
      if (line.trim() === "") return;
      out.push(...wordingFindings(line, `line ${index + 1}`, `${where}/${README_FILE}`, words));
    });
  }
  return out;
}

/** The H2 headings of a Markdown file, outside fences. */
export function h2Headings(markdown) {
  return stripCode(markdown)
    .split("\n")
    .map((line) => /^##\s+(.+?)\s*#*\s*$/.exec(line)?.[1])
    .filter((heading) => heading !== undefined);
}

/** The first section's prose (what `omega hub show` prints): the text under the first H2, until the next. */
export function firstSection(markdown) {
  const lines = markdown.split("\n");
  const start = lines.findIndex((line) => /^##\s+/.test(line));
  if (start < 0) return "";
  const rest = lines.slice(start + 1);
  const end = rest.findIndex((line) => /^##\s+/.test(line));
  return rest.slice(0, end < 0 ? undefined : end).join("\n").trim();
}

/** R8: README sections in order, size, and relative links that stay inside the entry. */
export function checkReadme(manifest, entryDir, where) {
  const file = `${where}/${README_FILE}`;
  const readmePath = path.join(entryDir, README_FILE);
  if (!existsSync(readmePath)) return [finding("R8", file, `${README_FILE} is missing`, "omega hub new writes one with the five sections")];
  const text = readFileSync(readmePath, "utf8");
  const out = [];
  if (Buffer.byteLength(text) > LIMITS.readmeBytes) out.push(finding("R8", file, `${README_FILE} is ${Buffer.byteLength(text)} bytes; at most ${LIMITS.readmeBytes}`, "move detail into the code comments"));
  const live = Array.isArray(manifest.models?.live) && manifest.models.live.length > 0;
  const wanted = README_SECTIONS.filter((section) => live || section !== "Run it with your key");
  const headings = h2Headings(text);
  let cursor = 0;
  for (const section of wanted) {
    const at = headings.indexOf(section, cursor);
    if (at < 0) {
      out.push(finding("R8", file, `the section "## ${section}" is missing or out of order`, `the sections are, in order: ${wanted.join(", ")}`));
    } else cursor = at + 1;
  }
  if (!live && headings.includes("Run it with your key")) out.push(finding("R8", file, '"## Run it with your key" is there, but `models.live` lists no model', "remove the section or list the models in models.live"));
  for (const match of stripCode(text).matchAll(/\]\(([^)\s]+)(?:\s+"[^"]*")?\)|(?:href|src)="([^"]+)"/g)) {
    const target = match[1] ?? match[2] ?? "";
    if (/^(?:https?:|mailto:|#)/.test(target)) {
      if (target.startsWith("http:")) out.push(finding("R8", file, `links ${target} over http`, "use https"));
      continue;
    }
    const clean = decodeURIComponent(target.split("#")[0] ?? "");
    const resolved = path.resolve(entryDir, clean);
    const relative = path.relative(entryDir, resolved);
    if (relative.startsWith("..") || path.isAbsolute(relative)) out.push(finding("R8", file, `links ${target}, outside the entry`, "link outside files with a full https://github.com/ URL"));
    else if (!existsSync(resolved)) out.push(finding("R8", file, `links ${target}, which does not exist`, "fix the link"));
  }
  return out;
}
