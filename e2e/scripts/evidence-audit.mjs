#!/usr/bin/env node
// The evidence gate. Run it before ANY evidence lands (a pull from
// a VM, a copy into an evidence directory, a publish). It audits every byte of
// the given files/directories (and of the files the --glob patterns match),
// inside archives, with the independent detectors in evidence-audit/.
//
//   node e2e/scripts/evidence-audit.mjs [--report <file>] [--quarantine <dir>]
//     [--stage <dir>] [--nonce <id>] [--registry <file>] [--root <dir>]
//     [--glob <pattern>]... [--] [path...]
//   node e2e/scripts/evidence-audit.mjs --check-report <file> [--nonce <id>]
//
//   --report      write the value-free JSON report there
//   --quarantine  move every refused file under <dir> (keep <dir> OUTSIDE any
//                 tree that is pulled or published)
//   --stage       copy every file WITHOUT a finding to <dir> (a fresh
//                 directory), at its path relative to --root, byte-identical
//                 to what was audited (sha256 checked before and after the
//                 copy). Only when the audit is clean, or refused with EVERY
//                 refused file quarantined; never otherwise. walks/vm/land.sh
//                 lands only what was staged.
//   --nonce       echoed in the report: the caller's proof the report is the
//                 one this run wrote
//   --registry    newline-separated values the run minted; matched exactly
//   --glob        e.g. 'e2e/walk-results/**/steps.txt', relative to --root
//   --check-report  may this report land? Its shape (evidence-audit/
//                 report-check.mjs), its nonce, and a fresh audit of its own
//                 bytes. Prints clean | refused | incomplete (exit 0), or
//                 invalid (exit 1).
// Exit 0: clean. 1: REFUSED. 3: refused and the quarantine was INCOMPLETE.
// 4: staging incomplete. 2: usage. A crash also exits non-zero (often 1):
// callers decide from the report (and its nonce), never the exit code alone.
// Output never contains a flagged value.
import { createHash } from "node:crypto";
import {
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  statSync,
  writeFileSync,
} from "node:fs";
import path from "node:path";
import {
  auditBuffer,
  auditedFiles,
  auditPaths,
  quarantineAll,
} from "./evidence-audit/audit.mjs";
import { checkReport, landingState } from "./evidence-audit/report-check.mjs";

const NONCE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;

function usage(message) {
  console.error(`evidence-audit: ${message}`);
  process.exit(2);
}

function parse(argv) {
  const opts = { paths: [], globs: [], root: process.cwd() };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    const value = () => argv[++i] ?? usage(`${a} needs a value`);
    if (a === "--") opts.paths.push(...argv.slice(i + 1)), (i = argv.length);
    else if (a === "--report") opts.report = value();
    else if (a === "--quarantine") opts.quarantine = value();
    else if (a === "--stage") opts.stage = value();
    else if (a === "--nonce") opts.nonce = value();
    else if (a === "--check-report") opts.checkReport = value();
    else if (a === "--registry") opts.registry = value();
    else if (a === "--root") opts.root = path.resolve(value());
    else if (a === "--glob") opts.globs.push(value());
    else if (a.startsWith("--")) usage(`unknown option ${a}`);
    else opts.paths.push(a);
  }
  if (opts.nonce !== undefined && !NONCE.test(opts.nonce))
    usage("--nonce must be 1-64 of [A-Za-z0-9._-]");
  if (!opts.checkReport && !opts.paths.length && !opts.globs.length)
    usage("no paths or --glob given");
  return opts;
}

/** A glob with `**` (any directories) and `*` (within one segment) as a RegExp. */
function globRegExp(glob) {
  let re = "";
  for (let i = 0; i < glob.length; i++) {
    const c = glob[i];
    if (c === "*" && glob[i + 1] === "*") {
      re += glob[i + 2] === "/" ? "(?:.*/)?" : ".*";
      i += glob[i + 2] === "/" ? 2 : 1;
    } else if (c === "*") re += "[^/]*";
    else if (c === "?") re += "[^/]";
    else re += c.replace(/[.+^${}()|[\]\\]/g, "\\$&");
  }
  return new RegExp(`^${re}$`);
}

function globFiles(root, glob) {
  const base = glob
    .split("/")
    .filter((s, i, all) => !/[*?]/.test(all.slice(0, i + 1).join("/")));
  const start = path.join(root, ...base);
  const re = globRegExp(glob);
  const out = [];
  const walk = (dir) => {
    let names;
    try {
      names = readdirSync(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const d of names) {
      const p = path.join(dir, d.name);
      if (d.isDirectory()) walk(p);
      else if (
        // Files and symlinks: a matching symlink is reported (never followed).
        !d.isDirectory() &&
        re.test(path.relative(root, p).split(path.sep).join("/"))
      )
        out.push(p);
    }
  };
  try {
    if (statSync(start).isFile()) return re.test(base.join("/")) ? [start] : [];
  } catch {
    return [];
  }
  walk(start);
  return out;
}

const sha256 = (buf) => createHash("sha256").update(buf).digest("hex");

/** Copy the audited files without findings into `dir`; every copy re-hashed. */
function stage(report, dir) {
  const refused = new Set(
    [...report.hits, ...report.problems].map((r) => r.file),
  );
  let files = 0;
  let failed = 0;
  if (existsSync(dir)) return { files, failed: 1, complete: false };
  mkdirSync(dir, { recursive: true });
  for (const [label, f] of auditedFiles(report)) {
    if (refused.has(label) || !f.sha256) continue;
    try {
      if (f.rel.startsWith("..") || path.isAbsolute(f.rel))
        throw new Error("outside the root");
      const bytes = readFileSync(f.abs);
      if (sha256(bytes) !== f.sha256) throw new Error("changed since audit");
      const to = path.join(dir, f.rel);
      mkdirSync(path.dirname(to), { recursive: true });
      writeFileSync(to, bytes, { flag: "wx", mode: 0o644 });
      if (sha256(readFileSync(to)) !== f.sha256)
        throw new Error("copy differs");
      files++;
    } catch {
      failed++;
    }
  }
  return { files, failed, complete: failed === 0 };
}

function checkReportFile(file, nonce) {
  let report;
  let bytes;
  try {
    bytes = readFileSync(file);
    report = JSON.parse(bytes.toString("utf8"));
  } catch {
    return null;
  }
  if (checkReport(report).length) return null;
  if (nonce !== undefined && report.nonce !== nonce) return null;
  if (auditBuffer(bytes, "evidence-audit.json").refused) return null;
  return landingState(report);
}

const opts = parse(process.argv.slice(2));
if (opts.checkReport) {
  const state = checkReportFile(opts.checkReport, opts.nonce);
  console.log(state ?? "invalid");
  process.exit(state ? 0 : 1);
}
const registry = opts.registry
  ? readFileSync(opts.registry, "utf8")
      .split("\n")
      .map((l) => l.trim())
      .filter(Boolean)
  : undefined;
const files = [
  ...new Set([
    ...opts.paths,
    ...opts.globs.flatMap((g) => globFiles(opts.root, g)),
  ]),
];
const report = auditPaths(files, { registry, root: opts.root });
report.registryUsed = Boolean(registry?.length);
report.inputs = {
  paths: opts.paths.length,
  globs: opts.globs,
  files: files.length,
};
if (opts.nonce !== undefined) report.nonce = opts.nonce;
let quarantineComplete = !report.refused;
if (report.refused && opts.quarantine) {
  const { moved, failed } = quarantineAll(
    report,
    opts.root,
    path.resolve(opts.quarantine),
  );
  report.quarantined = moved;
  report.quarantineFailed = failed;
  quarantineComplete = failed.length === 0;
}
if (opts.stage) {
  report.stage = quarantineComplete
    ? stage(report, path.resolve(opts.stage))
    : { files: 0, failed: 0, complete: false };
}
if (opts.report) {
  mkdirSync(path.dirname(path.resolve(opts.report)), { recursive: true });
  writeFileSync(opts.report, JSON.stringify(report, null, 2) + "\n");
}
const line =
  `evidence-audit: ${report.refused ? "REFUSED" : "clean"} files=${report.scanned.files} ` +
  `entries=${report.scanned.archiveEntries} hits=${report.hits.length} problems=${report.problems.length}` +
  (report.quarantineFailed?.length
    ? ` quarantine-failed=${report.quarantineFailed.length}`
    : "") +
  (report.stage
    ? ` staged=${report.stage.files} stage-complete=${report.stage.complete}`
    : "");
console.log(line);
for (const r of [...report.hits, ...report.problems].slice(0, 50))
  console.log(JSON.stringify(r));
const stageIncomplete = Boolean(report.stage && !report.stage.complete);
if (report.refused)
  process.exit(report.quarantineFailed?.length ? 3 : stageIncomplete ? 4 : 1);
process.exit(stageIncomplete ? 4 : 0);
