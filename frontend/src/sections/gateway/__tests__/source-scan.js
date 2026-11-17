// Source scans shared by the gateway's consistency tests: walk the gateway's
// sources (and the gateway's own part of central Billing) and find JSX tags.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const SRC = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../../..",
);

export const GATEWAY_DIRS = [
  "sections/gateway",
  "sections/settings/BillingV2/gateway-credits",
];

const isSource = (file) =>
  /\.(jsx?|tsx?)$/.test(file) &&
  !/\.(test|spec)\.[jt]sx?$/.test(file) &&
  !file.split(path.sep).includes("__tests__") &&
  !file.split(path.sep).includes("testing");

/** Every source file under the given src-relative dirs (or files), as [relPath, text]. */
export function sources(dirs = GATEWAY_DIRS) {
  const out = [];
  const walk = (dir) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) walk(full);
      else if (isSource(full))
        out.push([path.relative(SRC, full), fs.readFileSync(full, "utf8")]);
    }
  };
  dirs.forEach((dir) => {
    const full = path.join(SRC, dir);
    if (fs.statSync(full).isDirectory()) walk(full);
    else out.push([dir, fs.readFileSync(full, "utf8")]);
  });
  return out;
}

/** The 1-based line of an offset. */
export const lineOf = (text, offset) =>
  text.slice(0, offset).split("\n").length;

/**
 * Each opening tag of the named components, with its props as written
 * (braces balanced, so an arrow function's ">" does not end the tag).
 */
export function openingTags(text, names) {
  const re = new RegExp(`<(${names.join("|")})\\b`, "g");
  const tags = [];
  let match;
  while ((match = re.exec(text))) {
    let i = match.index + match[0].length;
    let depth = 0;
    let quote = null;
    for (; i < text.length; i += 1) {
      const c = text[i];
      if (quote) {
        if (c === quote && text[i - 1] !== "\\") quote = null;
      } else if (depth > 0 && (c === '"' || c === "'" || c === "`")) {
        quote = c;
      } else if (c === "{") depth += 1;
      else if (c === "}") depth -= 1;
      else if (c === ">" && depth === 0) break;
    }
    tags.push({
      name: match[1],
      start: match.index,
      end: i + 1,
      text: text.slice(match.index, i + 1),
    });
  }
  return tags;
}

/**
 * An opening tag with every braced expression emptied ("{...}" -> "{}"), so
 * a prop test sees the tag's own props, not those of an element nested in a
 * prop (a spinner's color="inherit" inside startIcon).
 */
export function ownProps(tagText) {
  let out = "";
  let depth = 0;
  for (const c of tagText) {
    if (c === "{") {
      if (depth === 0) out += c;
      depth += 1;
    } else if (c === "}") {
      depth -= 1;
      if (depth === 0) out += c;
    } else if (depth === 0) out += c;
  }
  return out;
}

/** The text of a prop's braced value inside an opening tag, or null. */
export function bracedProp(tagText, prop) {
  const at = tagText.search(new RegExp(`\\b${prop}=\\{`));
  if (at < 0) return null;
  let i = tagText.indexOf("{", at);
  const from = i;
  let depth = 0;
  for (; i < tagText.length; i += 1) {
    if (tagText[i] === "{") depth += 1;
    else if (tagText[i] === "}") {
      depth -= 1;
      if (depth === 0) break;
    }
  }
  return tagText.slice(from, i + 1);
}
