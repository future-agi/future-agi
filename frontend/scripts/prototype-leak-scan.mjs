#!/usr/bin/env node
// Prototype leak scan (tests F2): a production build (prototype mode off)
// must ship no prototype code, fixture data or prototype statics.
//
//   node scripts/prototype-leak-scan.mjs dist
//
// Exits 1 and lists every hit when a pattern or a prototype-only file is
// found anywhere under the folder; prints `clean` and exits 0 otherwise.
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { Buffer } from "node:buffer";
import { fileURLToPath } from "node:url";

/** Strings only prototype code, fixtures or prototype statics contain. */
export const LEAK_PATTERNS = Object.freeze([
  // fixture ids (datasets, experiments, runs, rows)
  "d5a1e7c2-0b3f-4a8d-9e61",
  "e6000000-0000-4000-8000",
  "e1000000-0000-4000-8000",
  "a1000000-0000-4000-8000",
  "b1000000-0000-4000-8000",
  // prototype runtime
  "proto_stage",
  "startPrototypeMode",
  "installUnmockedRegistry",
  "WORLD_EPOCH",
  "buildWorld",
  "fi_proto_",
  "fs_proto_",
  // prototype statics
  "mock/multimodal",
  "prototype-mock-sw",
  // the prototype label (the capability contract)
  "Prototype: mock data, resets per browser",
]);

/** Paths (relative to dist) that only prototype builds may contain. */
export const PROTOTYPE_ONLY_PATHS = Object.freeze([
  "mock",
  "prototype-mock-sw.js",
  // the legacy local mock's worker (vite.config.js shipsMockWorker)
  "mockServiceWorker.js",
]);

/** File-name prefixes of chunks only prototype builds emit, under assets/. */
export const PROTOTYPE_ONLY_CHUNKS = Object.freeze([
  "R4Lab-",
  "Leaderboards-",
  "OneViewGallery-",
  "PrototypeLabel-",
  "ReviewLinkPage-",
  "WorkspaceAuditLog-",
]);

/** Pure: the files under assets/ named by a prototype-only chunk prefix. */
export function prototypeChunksOf(files, prefixes = PROTOTYPE_ONLY_CHUNKS) {
  return files.filter((file) => {
    const parts = file.split(/[\\/]/);
    return (
      parts.length === 2 &&
      parts[0] === "assets" &&
      prefixes.some((prefix) => parts[1].startsWith(prefix))
    );
  });
}

/** Pure: the hits of `patterns` in `[{file, content}]` (content: Buffer|string). */
export function leakHits(entries, patterns = LEAK_PATTERNS) {
  const hits = [];
  for (const pattern of patterns) {
    const needle = Buffer.from(pattern);
    const files = entries
      .filter(({ content }) => Buffer.from(content).includes(needle))
      .map(({ file }) => file);
    if (files.length) hits.push({ pattern, files });
  }
  return hits;
}

function filesOf(dir) {
  const out = [];
  const walk = (rel) => {
    for (const entry of fs.readdirSync(path.join(dir, rel), {
      withFileTypes: true,
    })) {
      const next = rel ? path.join(rel, entry.name) : entry.name;
      if (entry.isDirectory()) walk(next);
      else if (entry.isFile()) out.push(next);
    }
  };
  walk("");
  return out.sort();
}

export function scanDist(dir) {
  const files = filesOf(dir);
  const present = [
    ...PROTOTYPE_ONLY_PATHS.filter((p) => fs.existsSync(path.join(dir, p))),
    ...prototypeChunksOf(files),
  ];
  const hits = leakHits(
    files.map((file) => ({
      file,
      content: fs.readFileSync(path.join(dir, file)),
    })),
  );
  return { files: files.length, present, hits };
}

const isMain =
  process.argv[1] &&
  path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);

if (isMain) {
  const dir = process.argv[2];
  if (!dir || !fs.existsSync(dir)) {
    console.error("usage: node scripts/prototype-leak-scan.mjs <distDir>");
    process.exit(2);
  }
  const { files, present, hits } = scanDist(dir);
  console.log(`scanned ${files} files under ${dir}`);
  present.forEach((p) => console.log(`[prototype-only path] ${p}`));
  hits.forEach(({ pattern, files: where }) => {
    console.log(`[${where.length} files] ${pattern}`);
    where.slice(0, 6).forEach((f) => console.log(`    ${f}`));
  });
  if (present.length || hits.length) process.exit(1);
  console.log("clean");
}
