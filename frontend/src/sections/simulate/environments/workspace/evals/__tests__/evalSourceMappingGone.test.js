// frontend/src/sections/simulate/environments/workspace/evals/__tests__/evalSourceMappingGone.test.js
//
// F1 / P24: the frontend never computes which source fills a required key, and
// the local tables that used to do it are gone. This is the test that keeps them
// gone — a re-introduced copy (under any name that still imports the module, or
// the file itself) fails here rather than silently drifting from the API.
import { describe, it, expect } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const self = fileURLToPath(import.meta.url);
const evalsDir = path.resolve(path.dirname(self), "..");
const srcRoot = (() => {
  let dir = path.dirname(self);
  while (path.basename(dir) !== "src") {
    const parent = path.dirname(dir);
    if (parent === dir) {
      throw new Error("evalSourceMappingGone: no src/ ancestor");
    }
    dir = parent;
  }
  return dir;
})();

const SKIP_DIRS = new Set(["node_modules", "generated", "__snapshots__"]);
const EXTENSIONS = new Set([".js", ".jsx", ".ts", ".tsx"]);
const MAX_BYTES = 1_000_000; // the generated contract bundles; nothing hand-written

// The deleted module's exports (verified against
// `git show b157d333f:frontend/src/sections/simulate/environments/workspace/evals/evalSourceMapping.js`).
// `modalityOf` is left out: it's too generic a name to police on its own.
const FORBIDDEN = [
  "evalSourceMapping",
  "SOURCE_BY_KEY",
  "sourceForKey",
  "mappingRowsFor",
  "humanizeMappingTerm",
];

// L4: word-boundary, not a raw substring match — `\b` either side, so an
// unrelated identifier that merely CONTAINS a forbidden token (SOURCE_BY_KEYS,
// sourceForKeyword, mappingRowsForTable) doesn't false-positive the guard.
const FORBIDDEN_PATTERNS = FORBIDDEN.map((token) => ({
  token,
  re: new RegExp(`\\b${token}\\b`),
}));

function* sourceFiles(dir, skipFile = self) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (!SKIP_DIRS.has(entry.name)) yield* sourceFiles(full, skipFile);
      continue;
    }
    if (!EXTENSIONS.has(path.extname(entry.name))) continue;
    if (full === skipFile) continue;
    yield full;
  }
}

// Scan `root` for any forbidden token (word-boundary matched), reporting each
// hit as `relativePath → token`. L3: takes a root so the self-test below can
// scan a throwaway temp tree instead of writing a probe file into src/ — a
// leftover probe from a killed process would otherwise fail test 2
// permanently and get committed by an unfiltered `git add` of this directory.
function scanForForbiddenTokens(root = srcRoot) {
  const offenders = [];
  // Only skip the guard's own file when scanning its real home (srcRoot); a
  // temp-tree scan has no reason to exclude anything.
  const skipFile = root === srcRoot ? self : null;
  for (const file of sourceFiles(root, skipFile)) {
    if (fs.statSync(file).size > MAX_BYTES) continue;
    const contents = fs.readFileSync(file, "utf8");
    for (const { token, re } of FORBIDDEN_PATTERNS) {
      if (re.test(contents)) {
        offenders.push(`${path.relative(root, file)} → ${token}`);
      }
    }
  }
  return offenders;
}

describe("the client-side eval source mapping is gone (F1, P24)", () => {
  it("no longer exists on disk", () => {
    expect(fs.existsSync(path.join(evalsDir, "evalSourceMapping.js"))).toBe(false);
  });

  it("is named by no file under src/, under any of the deleted module's exports", () => {
    expect(scanForForbiddenTokens()).toEqual([]);
  });

  it("the scan itself catches a re-introduced mapping under a new name, and does not false-positive on a longer identifier that merely contains one (L3, L4)", () => {
    const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), "evalSourceMappingGone-"));
    try {
      const hitFile = path.join(tmpRoot, "probe-hit.js");
      fs.writeFileSync(
        hitFile,
        "// planted by evalSourceMappingGone.test.js\nexport const sourceForKey = () => null;\n",
      );
      const nonHitFile = path.join(tmpRoot, "probe-non-hit.js");
      fs.writeFileSync(
        nonHitFile,
        // A longer identifier that CONTAINS a forbidden token as a substring —
        // the guard must not flag this (word-boundary, not substring).
        "// planted by evalSourceMappingGone.test.js\nexport const sourceForKeyword = () => null;\n",
      );

      const offenders = scanForForbiddenTokens(tmpRoot);

      expect(offenders).toContain(`${path.relative(tmpRoot, hitFile)} → sourceForKey`);
      expect(offenders).not.toContain(
        `${path.relative(tmpRoot, nonHitFile)} → sourceForKey`,
      );
    } finally {
      fs.rmSync(tmpRoot, { recursive: true, force: true });
    }
  });
});
