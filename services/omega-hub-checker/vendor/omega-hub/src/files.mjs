/**
 * @module @omega/hub/files
 *
 * The files of an entry: the walk, the folder rules (R3), `.env.example` (R4), the self-contained
 * import rule (R5), key documentation and secret scan (R6), and the digest the catalog and the lock
 * record. Nothing here runs entry code.
 */

import { createHash } from "node:crypto";
import { existsSync, lstatSync, readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { CODE_EXTENSIONS, ENV_EXAMPLE_FILE, IMAGE_EXTENSIONS, LIMITS, TEXT_EXTENSIONS, finding, isObject } from "./schema.mjs";

/** Folders the walk never enters: run output kept out of the entry by hub/.gitignore. */
const SKIP_DIRS = new Set([".omega", ".DS_Store"]);

/**
 * Every file below `root`, sorted: `{ files: [{ rel, abs, bytes }], symlinks: [rel], nodeModules: [rel] }`.
 * Symlinks are reported, never followed.
 */
export function walk(root) {
  const files = [];
  const symlinks = [];
  const nodeModules = [];
  const visit = (relative) => {
    const absolute = path.join(root, relative);
    for (const name of readdirSync(absolute).sort()) {
      if (SKIP_DIRS.has(name)) continue;
      const rel = relative ? `${relative}/${name}` : name;
      const abs = path.join(root, rel);
      const stat = lstatSync(abs);
      if (stat.isSymbolicLink()) symlinks.push(rel);
      else if (stat.isDirectory()) {
        if (name === "node_modules") nodeModules.push(rel);
        else visit(rel);
      } else if (stat.isFile()) files.push({ rel, abs, bytes: stat.size });
    }
  };
  if (existsSync(root)) visit("");
  return { files, symlinks, nodeModules };
}

const extensionOf = (rel) => {
  const base = path.posix.basename(rel);
  if (base === ".env.example" || base === ".gitignore") return base.slice(base.lastIndexOf("."));
  return path.posix.extname(base).toLowerCase();
};
export const isCode = (rel) => CODE_EXTENSIONS.includes(extensionOf(rel));
export const isImage = (rel) => IMAGE_EXTENSIONS.includes(extensionOf(rel));
export const isText = (rel) => TEXT_EXTENSIONS.includes(extensionOf(rel)) || rel === "LICENSE";

/** The language label the catalog and the site use for a file. */
export function langOf(rel) {
  const ext = extensionOf(rel);
  const map = { ".ts": "ts", ".mts": "ts", ".tsx": "tsx", ".mjs": "js", ".js": "js", ".json": "json", ".md": "md", ".txt": "text", ".csv": "csv", ".html": "html", ".css": "css", ".svg": "svg", ".cast": "cast", ".yml": "yaml", ".yaml": "yaml", ".png": "png", ".jpg": "jpg", ".jpeg": "jpg" };
  return map[ext] ?? "text";
}

/** The file `run.keyless` starts: the first argument after `node` that names a code file. */
export function runEntryFile(argv) {
  if (!Array.isArray(argv)) return null;
  for (let index = 1; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--import" || arg === "--require" || arg === "-r" || arg === "--env-file") {
      index += 1;
      continue;
    }
    if (arg.startsWith("-")) continue;
    return CODE_EXTENSIONS.some((ext) => arg.endsWith(ext)) ? arg.replace(/^\.\//, "") : null;
  }
  return null;
}

/** The folder a walk was made of (its first file's absolute path minus its relative path). */
function entryDirOf(walked, where) {
  const first = walked.files[0];
  return first ? first.abs.slice(0, first.abs.length - first.rel.length) : where;
}

const lineCount = (text) => (text.length === 0 ? 0 : text.split("\n").length - (text.endsWith("\n") ? 1 : 0));

/** R3: what the entry folder holds. `walked` is walk(entryDir); paths in findings are prefixed by `where`. */
export function checkFolder(manifest, walked, where, entryDir) {
  const out = [];
  const fail = (message, fix, file = where) => out.push(finding("R3", file, message, fix));
  const present = new Set(walked.files.map((file) => file.rel));
  const runFile = runEntryFile(manifest.run?.keyless);
  if (Array.isArray(manifest.run?.keyless) && !runFile) fail("`run.keyless` names no .ts or .mjs file to start", '["node", "--import", "tsx", "main.ts"]');
  else if (runFile && !present.has(runFile)) fail(`\`run.keyless\` starts ${runFile}, which is not in the entry`, `add ${runFile}`);
  if (typeof manifest.test === "string" && !present.has(manifest.test)) fail(`\`test\` names ${manifest.test}, which is not in the entry`, `add ${manifest.test}`);
  if (!present.has("package.json")) fail("package.json is missing", 'add { "private": true, "type": "module" } so the entry runs as ES modules wherever it is copied');
  else {
    let type;
    try {
      type = JSON.parse(readFileSync(path.join(entryDirOf(walked, where), "package.json"), "utf8"))?.type;
    } catch {
      type = undefined;
    }
    if (type !== "module") fail('package.json must set "type": "module"', 'add "type": "module"');
  }
  const cast = manifest.demo?.cast;
  if (typeof cast === "string" && !present.has(cast)) fail(`${cast} is missing`, `omega hub check ${manifest.id ?? "<id>"} --record`);
  for (const rel of walked.symlinks) fail(`${rel} is a symlink`, "copy the file in instead", `${where}/${rel}`);
  for (const rel of walked.nodeModules) fail(`${rel} is a node_modules folder`, "delete it; entries import only node:* and @omega/* packages", `${where}/${rel}`);
  let total = 0;
  for (const file of walked.files) {
    total += file.bytes;
    const base = path.posix.basename(file.rel);
    const at = `${where}/${file.rel}`;
    if (/^\.env(?:\..*)?$/.test(base) && base !== ENV_EXAMPLE_FILE) {
      fail(`${file.rel} may hold real values`, `delete it; list names only in ${ENV_EXAMPLE_FILE}`, at);
      continue;
    }
    if (isImage(file.rel) && file.bytes > LIMITS.imageBytes) fail(`${file.rel} is ${file.bytes} bytes; images are at most ${LIMITS.imageBytes}`, "shrink the image", at);
    else if (!isImage(file.rel) && !isText(file.rel)) fail(`${file.rel} is not a text file or a png, jpg or svg image`, "keep code, data and docs as text", at);
    else if (isText(file.rel) && file.bytes > LIMITS.textBytes) fail(`${file.rel} is ${file.bytes} bytes; text files are at most ${LIMITS.textBytes}`, "split it or trim it", at);
    if (isCode(file.rel)) {
      const lines = lineCount(readFileSync(file.abs, "utf8"));
      if (lines > LIMITS.codeLines) fail(`${file.rel} has ${lines} lines; code files have at most ${LIMITS.codeLines}`, "split it into modules", at);
    }
  }
  if (walked.files.length > LIMITS.files) fail(`the entry has ${walked.files.length} files; at most ${LIMITS.files}`, "remove files the entry does not need");
  if (total > LIMITS.totalBytes) fail(`the entry has ${total} bytes; at most ${LIMITS.totalBytes}`, "remove large data files");
  for (const rel of [manifest.install?.plugin, manifest.install?.skillpack].filter((value) => typeof value === "string")) {
    const base = entryDir ?? "";
    if (!existsSync(path.join(base, rel))) fail(`\`install\` names ${rel}, which does not exist`, "point it at the folder to install");
  }
  return out;
}

/** The names a `.env.example` lists, and any line that sets a value. */
export function parseEnvExample(text) {
  const names = [];
  const valued = [];
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (line === "" || line.startsWith("#")) continue;
    const match = /^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$/.exec(line);
    if (!match) continue;
    names.push(match[1]);
    const value = match[2].trim().replace(/^["']|["']$/g, "");
    if (value !== "") valued.push(match[1]);
  }
  return { names, valued };
}

/** R4: `.env.example` lists exactly the documented keys, with empty values. */
export function checkEnvExample(manifest, entryDir, where) {
  const out = [];
  const envPath = path.join(entryDir, ENV_EXAMPLE_FILE);
  const live = Array.isArray(manifest.models?.live) ? manifest.models.live : [];
  const documented = (Array.isArray(manifest.keys) ? manifest.keys : []).filter(isObject).map((key) => key.name).sort();
  const file = `${where}/${ENV_EXAMPLE_FILE}`;
  if (!existsSync(envPath)) {
    if (live.length > 0) out.push(finding("R4", file, `${ENV_EXAMPLE_FILE} is missing`, `add one NAME= line per key: ${documented.join(", ")}`));
    return out;
  }
  const parsed = parseEnvExample(readFileSync(envPath, "utf8"));
  for (const name of parsed.valued) out.push(finding("R4", file, `${name} has a value`, `leave it empty: ${name}=`));
  const listed = [...new Set(parsed.names)].sort();
  if (listed.join(",") !== documented.join(",")) {
    out.push(finding("R4", file, `lists ${listed.join(", ") || "nothing"} but \`keys\` documents ${documented.join(", ") || "nothing"}`, "list exactly the names in `keys`"));
  }
  return out;
}

const IMPORT_PATTERNS = [
  /\bimport\s+(?:type\s+)?(?:[\w*{}\s,$]+?\s+from\s+)?["']([^"']+)["']/g,
  /\bexport\s+(?:type\s+)?(?:\*|\{[^}]*\})(?:\s+as\s+\w+)?\s+from\s+["']([^"']+)["']/g,
  /\bimport\s*\(\s*["']([^"']+)["']\s*\)/g,
  /\brequire\s*\(\s*["']([^"']+)["']\s*\)/g,
  /\bnew\s+URL\s*\(\s*["']([^"']+)["']\s*,\s*import\.meta\.url\s*\)/g
];

/** Every module specifier a source file names: static, dynamic and `new URL(..., import.meta.url)`. */
export function specifiersOf(source) {
  const code = source.replace(/\/\*[\s\S]*?\*\//g, " ").replace(/(^|[^:"'`\\])\/\/.*$/gm, "$1");
  const out = [];
  for (const pattern of IMPORT_PATTERNS) {
    pattern.lastIndex = 0;
    for (const match of code.matchAll(pattern)) out.push(match[1]);
  }
  return out;
}

/** The workspace package a bare `@omega/...` specifier names, with its subpath (`@omega/core/testing`). */
const omegaSpecifier = (specifier) => (/^@omega\/[a-z0-9-]+(?:\/[A-Za-z0-9._/-]+)?$/.test(specifier) ? specifier : null);

/**
 * R5: every relative import stays inside `root`; bare imports are `node:*` or `@omega/*`. Returns
 * `{ findings, imports }`, `imports` being the sorted `@omega/*` specifiers the code uses.
 */
export function checkSelfContained(walked, root, where) {
  const findings = [];
  const imports = new Set();
  for (const file of walked.files) {
    if (!isCode(file.rel)) continue;
    const at = `${where}/${file.rel}`;
    for (const specifier of specifiersOf(readFileSync(file.abs, "utf8"))) {
      if (specifier.startsWith("node:")) continue;
      const omega = omegaSpecifier(specifier);
      if (omega) {
        imports.add(omega);
        continue;
      }
      if (specifier.startsWith(".") || specifier.startsWith("/")) {
        const target = path.resolve(path.dirname(file.abs), specifier);
        const relative = path.relative(root, target);
        if (relative.startsWith("..") || path.isAbsolute(relative) || specifier.startsWith("/")) {
          findings.push(finding("R5", at, `imports ${specifier}, which is outside the entry`, "copy what you need into the entry folder"));
        }
        continue;
      }
      findings.push(finding("R5", at, `imports ${specifier}; entries import only node:* and @omega/* packages`, "use a node: built-in or an @omega/* package, or copy the code in"));
    }
  }
  return { findings, imports: [...imports].sort() };
}

/**
 * Values that look like credentials. Names (OPENAI_API_KEY) are fine; values never belong in an entry.
 * @type {[string, RegExp][]}
 */
export const SECRET_PATTERNS = [
  ["an OpenAI or Anthropic key", /\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{24,}/],
  ["an AWS access key id", /\bAKIA[0-9A-Z]{16}\b/],
  ["a GitHub token", /\bgh[pousr]_[A-Za-z0-9]{36,}\b/],
  ["a Google API key", /\bAIza[0-9A-Za-z_-]{35}\b/],
  ["a Slack token", /\bxox[abprs]-[A-Za-z0-9-]{10,}/],
  ["a private key", new RegExp("-----BEGIN [A-Z ]*" + "PRIVATE KEY-----")],
  ["a personal home path", new RegExp("(?:/" + "Users/|/home/)[A-Za-z0-9._-]+/")]
];

/** R6: every live key is documented in `keys`, and no file holds a credential value. */
export function checkKeys(manifest, walked, where) {
  const out = [];
  const documented = new Set((Array.isArray(manifest.keys) ? manifest.keys : []).filter(isObject).map((key) => key.name));
  const live = Array.isArray(manifest.models?.live) ? manifest.models.live : [];
  for (const item of live) {
    for (const name of Array.isArray(item?.keys) ? item.keys : []) {
      if (!documented.has(name)) out.push(finding("R6", `${where}/omega.hub.json`, `${item.model} needs ${name}, which \`keys\` does not document`, `add { "name": "${name}", "why": ..., "get": ..., "for": "live", "secret": true } to keys`));
    }
  }
  for (const file of walked.files) {
    if (!isText(file.rel) || file.bytes > LIMITS.textBytes * 4) continue;
    const text = readFileSync(file.abs, "utf8");
    for (const [label, pattern] of SECRET_PATTERNS) {
      if (pattern.test(text)) out.push(finding("R6", `${where}/${file.rel}`, `holds what looks like ${label}`, "remove it; entries name keys, never values or personal paths"));
    }
  }
  return out;
}

/**
 * The digest of a set of files: sha256 over the sorted lines `<rel>\u0000<sha256 of the file>`,
 * joined with newlines, as `sha256:<hex>`.
 */
export function digestFiles(files) {
  const lines = files
    .map((file) => `${file.rel}\u0000${createHash("sha256").update(readFileSync(file.abs)).digest("hex")}`)
    .sort();
  return `sha256:${createHash("sha256").update(lines.join("\n")).digest("hex")}`;
}

/** The files a user copies: every file of `filesRoot` except the recording. */
export function copyableFiles(walked, castRel) {
  return walked.files.filter((file) => file.rel !== castRel);
}

export { lineCount };
