/**
 * Step 10, similarity (SPEC §4.2 row 10, Hermes rule 16): flag a new entry whose code files are at
 * least half byte-identical to one listed entry that it does not name in `remix_of`.
 */
import { createHash } from "node:crypto";
import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { flag } from "./findings.mjs";

const SIMILAR_CODE = [".ts", ".mts", ".mjs", ".js", ".tsx", ".html"];
const TEMPLATE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "vendor", "omega-hub", "template");
const sha = (data) => createHash("sha256").update(data).digest("hex");

function templateHashes(dir = TEMPLATE, out = new Set()) {
  try {
    for (const name of readdirSync(dir)) {
      const file = path.join(dir, name);
      if (statSync(file).isDirectory()) templateHashes(file, out);
      else out.add(sha(readFileSync(file)));
    }
  } catch {
    // no vendored template: nothing to leave out
  }
  return out;
}
const TEMPLATE_HASHES = templateHashes();

export function similarityFindings(entry, { codeHashes = {}, ownRef = null, remixOf = null }) {
  const mine = new Map();
  for (const [rel, data] of entry) {
    if (!SIMILAR_CODE.some((ext) => rel.endsWith(ext))) continue;
    const hash = sha(data);
    if (!TEMPLATE_HASHES.has(hash)) mine.set(hash, rel);
  }
  if (mine.size === 0) return [];
  const remixRef = typeof remixOf === "string" ? remixOf.split("@")[0] : null;
  const out = [];
  for (const [ref, hashes] of Object.entries(codeHashes)) {
    if (ref === ownRef || ref === remixRef) continue;
    const theirs = new Set(hashes);
    const shared = [...mine].filter(([hash]) => theirs.has(hash)).map(([, rel]) => rel).sort();
    if (shared.length > 0 && shared.length * 2 >= mine.size) {
      out.push(flag("similarity", "undeclared-copy", shared[0], `${shared.length} of ${mine.size} code files are identical to ${ref}: ${shared.join(", ")}`, { fix: `name ${ref} in remix_of, or make the entry materially different (C6)` }));
    }
  }
  return out;
}
