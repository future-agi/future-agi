/**
 * The canonical bundle `omega-hub-bundle/v1` (SPEC §4.6): a gzip tar with every entry file under
 * `entry/` (and an Official entry's source folder under `source/`), byte-sorted paths, mode 0644,
 * uid and gid 0, mtime 0, no directories and no links. Same input, same bytes.
 */
import { createHash } from "node:crypto";
import { writeTarGz } from "./archive.mjs";

const byBytes = (a, b) => Buffer.compare(Buffer.from(a), Buffer.from(b));

export function packCanonical(entry, sourceFiles = new Map()) {
  const files = [];
  for (const [rel, data] of entry) files.push({ name: `entry/${rel}`, data });
  for (const [rel, data] of sourceFiles) files.push({ name: `source/${rel}`, data });
  files.sort((a, b) => byBytes(a.name, b.name));
  const bundle = writeTarGz(files);
  return { bundle, bundleSha256: createHash("sha256").update(bundle).digest("hex"), bundleBytes: bundle.length };
}

/** `@omega/hub` digestFiles over in-memory files: sha256 of sorted `rel\0sha256(file)` lines. */
export function digestFiles(map) {
  const lines = [...map].map(([rel, data]) => `${rel}\u0000${createHash("sha256").update(data).digest("hex")}`).sort();
  return `sha256:${createHash("sha256").update(lines.join("\n")).digest("hex")}`;
}

/** The catalog digest: the copyable files (the source folder for Official entries), without the cast. */
export function catalogDigest(entry, sourceFiles, manifest) {
  if (typeof manifest.source === "string" && sourceFiles.size > 0) return digestFiles(sourceFiles);
  const cast = typeof manifest.demo?.cast === "string" ? manifest.demo.cast : "demo.cast";
  return digestFiles(new Map([...entry].filter(([rel]) => rel !== cast)));
}
