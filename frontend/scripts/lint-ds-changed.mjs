/* eslint-env node */
/* eslint-disable no-console */
// Runs the design-system lint (.eslintrc.ds.cjs) on the lines this branch adds,
// so older screens don't fail CI but new code has to follow the system.
//
//   node scripts/lint-ds-changed.mjs                  # base: nearest of origin/dev, origin/main
//   node scripts/lint-ds-changed.mjs --base up/dev    # explicit base ref
//
// Compares the working tree with the merge-base, so uncommitted edits count too.
import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { ESLint } from "eslint";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(__dirname, "..");
const DEFAULT_BASES = ["origin/dev", "origin/main"];

const git = (...args) =>
  execFileSync("git", args, {
    cwd: frontendRoot,
    encoding: "utf8",
    maxBuffer: 512 * 1024 * 1024,
  }).trim();

const tryGit = (...args) => {
  try {
    return git(...args);
  } catch {
    return null;
  }
};

const argBase = (() => {
  const i = process.argv.indexOf("--base");
  return i > -1 ? process.argv[i + 1] : process.env.DS_LINT_BASE;
})();

// The nearest base wins, so a hotfix branch cut from main isn't diffed against dev.
const resolveMergeBase = () => {
  const candidates = argBase ? [argBase] : DEFAULT_BASES;
  let best = null;
  for (const ref of candidates) {
    const mergeBase = tryGit("merge-base", "HEAD", ref);
    if (!mergeBase) continue;
    const distance = Number(tryGit("rev-list", "--count", `${mergeBase}..HEAD`));
    if (!best || distance < best.distance) best = { ref, mergeBase, distance };
  }
  if (!best) {
    console.error(`lint:ds:changed: no merge-base with ${candidates.join(" or ")}`);
    process.exit(2);
  }
  return best;
};

// Added line numbers per file, from a zero-context diff.
const addedLines = (mergeBase) => {
  const diff = git(
    "diff",
    "-U0",
    "--diff-filter=AM",
    "--no-color",
    mergeBase,
    "--",
    // A plain pathspec `*` crosses directories, so this covers all of src.
    "src/*.js",
    "src/*.jsx",
  );
  const repoRoot = git("rev-parse", "--show-toplevel");
  const byFile = new Map();
  let current = null;
  for (const line of diff.split("\n")) {
    if (line.startsWith("+++ b/")) {
      current = path.resolve(repoRoot, line.slice(6));
      byFile.set(current, new Set());
    } else if (current && line.startsWith("@@")) {
      const [, start, count = "1"] = line.match(/\+(\d+)(?:,(\d+))?/);
      for (let n = 0; n < Number(count); n += 1) {
        byFile.get(current).add(Number(start) + n);
      }
    }
  }
  return byFile;
};

const { ref, mergeBase } = resolveMergeBase();
const changed = addedLines(mergeBase);
const files = [...changed.keys()].filter((f) => changed.get(f).size > 0);

const eslint = new ESLint({
  cwd: frontendRoot,
  useEslintrc: false,
  overrideConfigFile: path.join(frontendRoot, ".eslintrc.ds.cjs"),
});
const results = files.length ? await eslint.lintFiles(files) : [];

// An import rule fires on the whole import line, so reformatting or extending an
// existing import would look new. Count it only if the base file didn't have it.
const baseImportMessages = async (filePath) => {
  const rel = path.relative(git("rev-parse", "--show-toplevel"), filePath);
  const base = tryGit("show", `${mergeBase}:${rel}`);
  if (base === null) return new Set();
  const [result] = await eslint.lintText(base, { filePath });
  return new Set(
    result.messages
      .filter((m) => m.ruleId === "no-restricted-imports")
      .map((m) => m.message),
  );
};

let count = 0;
let warnings = 0;
for (const result of results) {
  const lines = changed.get(result.filePath);
  let hits = result.messages.filter(
    (m) => m.message.includes("[DS]") && lines?.has(m.line),
  );
  if (hits.some((m) => m.ruleId === "no-restricted-imports")) {
    const before = await baseImportMessages(result.filePath);
    hits = hits.filter(
      (m) => m.ruleId !== "no-restricted-imports" || !before.has(m.message),
    );
  }
  for (const m of hits) {
    const isError = m.severity === 2;
    if (isError) count += 1;
    else warnings += 1;
    console.log(
      `${path.relative(frontendRoot, result.filePath)}:${m.line}:${m.column}  ${isError ? "error" : "warning"}  ${m.message}`,
    );
  }
}

console.log(
  `\nlint:ds:changed: ${count} design-system error(s) and ${warnings} warning(s) on lines added since ${ref} (${mergeBase.slice(0, 9)}), ${files.length} file(s) checked.`,
);
process.exit(count ? 1 : 0);
