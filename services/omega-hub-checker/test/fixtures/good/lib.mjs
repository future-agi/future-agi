// Small helpers: isMain, tempDir and JSON-lines files. Plain Node, nothing Omega-specific.
import { appendFileSync, existsSync, mkdtempSync, readFileSync, realpathSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

/**
 * True when the module at `importMetaUrl` is the entry script (`node path/to/index.mjs`), false when
 * it was imported, for example by its test. Lets one file be both a runnable recipe and a library.
 */
export function isMain(importMetaUrl) {
  const entry = process.argv[1];
  if (!entry) return false;
  try {
    return realpathSync(fileURLToPath(importMetaUrl)) === realpathSync(resolve(entry));
  } catch {
    return false;
  }
}

const tempDirs = new Set();
let cleanupRegistered = false;

/** Create a temporary directory that is deleted (recursively) when the process exits. */
export function tempDir(prefix = "omega-cookbook-") {
  const dir = mkdtempSync(join(tmpdir(), prefix));
  tempDirs.add(dir);
  if (!cleanupRegistered) {
    cleanupRegistered = true;
    process.on("exit", () => {
      for (const path of tempDirs) rmSync(path, { recursive: true, force: true });
    });
  }
  return dir;
}

/** Append one JSON record as a line to `file` (JSONL). */
export function appendJsonLine(file, record) {
  appendFileSync(file, `${JSON.stringify(record)}\n`);
}

/** Read a JSONL file into an array of records. A missing file reads as []. */
export function readJsonLines(file) {
  if (!existsSync(file)) return [];
  return readFileSync(file, "utf8")
    .split("\n")
    .filter((line) => line.trim() !== "")
    .map((line) => JSON.parse(line));
}
