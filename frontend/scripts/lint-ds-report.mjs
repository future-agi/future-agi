/* eslint-env node */
import { ESLint } from "eslint";
import { createRequire } from "node:module";
import { mkdir, writeFile } from "node:fs/promises";
import { dirname, resolve, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { execFileSync } from "node:child_process";

// A separate report, not an extension of the existing blocking lint config.
// --root permits same-rule before/after measurement on an exact base export.
const home = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const args = process.argv.slice(2);
const value = (name, fallback) =>
  args.includes(name) ? args[args.indexOf(name) + 1] : fallback;
const root = resolve(value("--root", home));
const output = resolve(value("--output", resolve(home, "ds-report.json")));
const require = createRequire(import.meta.url);
const config = require("../.eslintrc.ds.cjs");
const lint = new ESLint({
  cwd: root,
  useEslintrc: false,
  baseConfig: config,
  resolvePluginsRelativeTo: home,
  allowInlineConfig: false,
});
const results = await lint.lintFiles(["src/**/*.{js,jsx,ts,tsx}"]);
const findings = results.flatMap((result) =>
  result.messages.map((message) => ({
    file: relative(root, result.filePath),
    line: message.line,
    column: message.column,
    rule: message.ruleId,
    message: message.message,
    fatal: Boolean(message.fatal),
  })),
);
const counts = {};
for (const finding of findings) {
  const key = finding.message;
  counts[key] = (counts[key] || 0) + 1;
}
let revision;
try {
  revision = execFileSync("git", ["-C", root, "rev-parse", "HEAD"], {
    encoding: "utf8",
  }).trim();
} catch {
  revision = "exported tree";
}
const report = {
  mode: "report-only",
  revision,
  root,
  filesScanned: results.length,
  filesWithFindings: results.filter((r) => r.messages.length).length,
  violations: findings.filter((f) => f.message.includes("[DS]")).length,
  parserFailures: findings.filter((f) => f.fatal).length,
  otherDiagnostics: findings.filter(
    (f) => !f.fatal && !f.message.includes("[DS]"),
  ).length,
  counts,
  findings,
};
await mkdir(dirname(output), { recursive: true });
await writeFile(output, `${JSON.stringify(report, null, 2)}\n`);
console.log(
  `lint:ds REPORT ONLY: ${report.violations} violations, ${report.parserFailures} parser failures, ${report.filesScanned} files; ${output}`,
);
// Existing debt never fails CI. Configuration/runtime errors still throw so an
// interrupted scan cannot masquerade as a completed report.
