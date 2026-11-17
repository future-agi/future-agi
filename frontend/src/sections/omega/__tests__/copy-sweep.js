import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Shared by the copy sweeps (no-in-progress-copy, no-unreleased-cli-copy,
// no-internal-copy): every non-test source file under sections/omega, read
// with its comments removed, so a doc comment may still explain what is
// hidden and why while any word a customer can read is checked.

export const ROOT = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);
const SKIP_DIRS = new Set(["__tests__", "__fixtures__", "e2e", "node_modules"]);

export function sources(dir = ROOT, out = []) {
  for (const name of fs.readdirSync(dir)) {
    const full = path.join(dir, name);
    if (fs.statSync(full).isDirectory()) {
      if (!SKIP_DIRS.has(name)) sources(full, out);
    } else if (/\.(jsx?|mjs)$/.test(name) && !/\.test\.jsx?$/.test(name)) {
      out.push(full);
    }
  }
  return out;
}

export const relative = (file) => path.relative(ROOT, file);

// Line and block comments out; string contents kept (a URL's "//" inside a
// string is rare in these files and would only make the sweep stricter).
export function withoutComments(text) {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|[^:"'`])\/\/.*$/gm, "$1");
}

// Regular-expression literals out (a log filter such as /Docker daemon/ is a
// pattern, not copy). A "/" opens one only where an expression starts, the
// usual way to tell a regex from a division.
const REGEX_LITERAL =
  /(^\s*|[=(,:[!&|?{};]\s*)\/(?![*/])(?:\\.|\[(?:\\.|[^\]\\\n])*\]|[^/\\\n[])+\/[dgimsuyv]*/gm;

export function withoutRegexLiterals(text) {
  return text.replace(REGEX_LITERAL, "$1/RE/");
}

/**
 * The source of a file with its comments and regex literals removed.
 * @param {string} file
 * @param {(source: string, file: string) => string} [transformSource]
 */
export function readCode(file, transformSource = (source) => source) {
  const source = transformSource(fs.readFileSync(file, "utf8"), file);
  return withoutRegexLiterals(withoutComments(source));
}

/**
 * Every match of `patterns` in `files` (comments removed). A pattern runs over
 * the whole file, so prose that JSX wraps onto the next line ("Your
 * deployment's\n operator sets ...") is found when the pattern spells its
 * spaces as \s+. Each hit names the line the match starts on.
 * @param {string[]} files
 * @param {RegExp[]} patterns
 * @param {(source: string, file: string) => string} [transformSource]
 */
export function sweep(files, patterns, transformSource) {
  const hits = [];
  for (const file of files) {
    const code = readCode(file, transformSource);
    const lines = code.split("\n");
    for (const pattern of patterns) {
      const flags = pattern.flags.includes("g")
        ? pattern.flags
        : `${pattern.flags}g`;
      for (const match of code.matchAll(new RegExp(pattern.source, flags))) {
        const line = code.slice(0, match.index).split("\n").length;
        hits.push({
          file: relative(file),
          line,
          text: lines[line - 1].trim(),
          match: match[0],
          pattern: pattern.source,
        });
      }
    }
  }
  return hits;
}
