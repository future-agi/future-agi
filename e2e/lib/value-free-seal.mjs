// HR-JRN-01: the seal of a value-free publish directory (lib/value-free-publish.ts)
// and the check every launcher runs on it before anything leaves the machine.
//
// A publish directory is exactly one of:
//   - a publication: regular files only, MANIFEST.json listing every other file with its
//     size and its sha256 digest (written `sha256:<hex>` under the neutral field `digest`);
//   - a refusal: REFUSED.json and nothing else, `{ format, status, reasons }`, each reason
//     made only of the fixed vocabulary below (codes, kinds, categories), lengths, depths
//     and file:line, never a value, a path or a location string. The reporter
//     scans the record and its name like every published file before writing it, and
//     publishes nothing when no clean record can be written.
// verifySeal() says whether a directory still is what its seal says. Anything written
// after assembly (a late reporter, an exit hook, a stray process), any link, hard link
// or special file, any changed byte, and a refusal record with anything outside the
// vocabulary break it. Plain Node, no dependencies, so a launcher runs it as it is:
//
//   node e2e/lib/value-free-seal.mjs <publish-dir> [--export <export-dir>]
//
// exits 0 for an intact publication; 1 for an intact refusal (REFUSED.json alone), for a
// missing directory, and for a broken seal. This check holds no registry, so it cannot
// scan a record of its own: a broken seal publishes NOTHING. The directory is moved off
// the pull path to a sibling `.<name>-withheld-*` on this machine (never deleted, never
// pulled), except one that holds sources, which is never touched and so stays where it
// is. A consumer therefore transfers only on the check's verdict: the intact publication
// (exit 0) or the intact refusal record alone, never a directory the check rejected.
// With --export (the walk consumer, e2e/walks/vm/run-walks.sh) that verdict is
// the export directory, the only path the launcher pulls: a fresh copy of an intact
// publication or refusal record, sealed again there; for anything else, nothing.
// The export directory's name is value-free by construction: shorter than
// VALUE_FREE_MIN_LENGTH, the shortest value a run can register, so no registered value fits
// in it (a longer name exports nothing). This check still holds no registry: the walk job
// runs the publication boundary's final check (lib/value-free-boundary.mjs) over exactly what
// it pulls, after this export and every other write.
// That module is the publication choke point. Run as a program, this file forwards to
// it (mode `seal`): what a consumer reads is one of its STATUS constants, never the `line` the
// functions here return (fixed words a run could register, such as `intact`). A consumer
// in this tree runs the choke point itself (harness/value-free-consumers.spec.ts).
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import {
  existsSync,
  lstatSync,
  mkdirSync,
  mkdtempSync,
  readdirSync,
  readFileSync,
  realpathSync,
  renameSync,
  rmdirSync,
  rmSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { VALUE_FREE_MIN_LENGTH } from "./value-free-reading.mjs";
import { PUBLISH_FORMAT, REFUSAL_FORMAT } from "./value-free-record.mjs";
import { cleanReason, isCleanReason } from "./value-free-vocabulary.mjs";

export const MANIFEST = "MANIFEST.json";
export const REFUSED = "REFUSED.json";
// The two formats are defined with the closed record schema they name (value-free-record.mjs).
export { PUBLISH_FORMAT, REFUSAL_FORMAT };

/** The refusal record's text: fixed fields, every reason cut to the vocabulary. */
export function refusalRecordText(reasons) {
  return `${JSON.stringify({ format: REFUSAL_FORMAT, status: "refused", reasons: reasons.map(cleanReason) }, null, 2)}\n`;
}

const digestOf = (buf) =>
  `sha256:${createHash("sha256").update(buf).digest("hex")}`;
const readJson = (file) => {
  try {
    return JSON.parse(readFileSync(file, "utf8"));
  } catch {
    return null;
  }
};

/** Every entry under `dir` (relative POSIX paths, sorted), never following a link. */
export function listTree(dir) {
  const entries = [];
  const walk = (rel) => {
    for (const name of readdirSync(rel ? path.join(dir, rel) : dir).sort()) {
      const at = rel ? `${rel}/${name}` : name;
      const st = lstatSync(path.join(dir, at));
      if (st.isDirectory()) {
        entries.push({ path: at, type: "dir" });
        walk(at);
      } else if (st.isFile())
        entries.push({
          path: at,
          type: "file",
          bytes: st.size,
          nlink: st.nlink,
        });
      else
        entries.push({
          path: at,
          type: st.isSymbolicLink() ? "symlink" : "special",
        });
    }
  };
  walk("");
  return entries;
}

/** Seals a directory of regular files: MANIFEST.json lists every other file (path, bytes, digest). */
export function writeManifest(dir, extra = {}) {
  const files = listTree(dir)
    .filter((e) => e.type === "file" && e.path !== MANIFEST)
    .map((e) => ({
      path: e.path,
      bytes: e.bytes,
      digest: digestOf(readFileSync(path.join(dir, e.path))),
    }));
  writeFileSync(
    path.join(dir, MANIFEST),
    `${JSON.stringify({ format: PUBLISH_FORMAT, status: "published", ...extra, files }, null, 2)}\n`,
  );
}

/** null when `dir` is exactly what its seal says; otherwise why not (fixed words, never a path or a value). */
export function verifySeal(dir) {
  let st;
  try {
    st = lstatSync(dir);
  } catch {
    return "missing";
  }
  if (!st.isDirectory()) return "not a directory";
  const tree = listTree(dir);
  if (tree.some((e) => e.type === "symlink" || e.type === "special"))
    return "it holds a link or a special file";
  if (tree.some((e) => e.type === "file" && e.nlink > 1))
    return "it holds a hard link";
  const names = new Set(tree.map((e) => e.path));
  if (names.has(REFUSED)) {
    if (tree.length !== 1) return "a refusal record with other entries";
    return isRefusalRecord(readJson(path.join(dir, REFUSED)))
      ? null
      : "not a refusal record";
  }
  if (!names.has(MANIFEST)) return "no seal";
  const manifest = readJson(path.join(dir, MANIFEST));
  if (manifest?.format !== PUBLISH_FORMAT || !Array.isArray(manifest.files))
    return "not a seal";
  const listed = new Map(manifest.files.map((f) => [f.path, f]));
  const files = tree.filter((e) => e.type === "file" && e.path !== MANIFEST);
  if (files.length !== listed.size)
    return "a file was added or removed after assembly";
  for (const f of files) {
    const want = listed.get(f.path);
    if (
      !want ||
      want.bytes !== f.bytes ||
      want.digest !== digestOf(readFileSync(path.join(dir, f.path)))
    )
      return "a file was added or changed after assembly";
  }
  const needed = new Set();
  for (const p of listed.keys()) {
    const parts = p.split("/");
    for (let i = 1; i < parts.length; i++)
      needed.add(parts.slice(0, i).join("/"));
  }
  if (tree.some((e) => e.type === "dir" && !needed.has(e.path)))
    return "a directory was added after assembly";
  return null;
}

/** Whether `dir` is a publish directory this harness wrote (its seal or its refusal record at the top). */
export function isPublishDir(dir) {
  try {
    if (!lstatSync(dir).isDirectory()) return false;
  } catch {
    return false;
  }
  return (
    readJson(path.join(dir, MANIFEST))?.format === PUBLISH_FORMAT ||
    readJson(path.join(dir, REFUSED))?.format === REFUSAL_FORMAT
  );
}

/** A refusal record this harness writes: exactly format, status and reasons, every reason in the vocabulary. */
function isRefusalRecord(record) {
  return (
    Boolean(record) &&
    typeof record === "object" &&
    Object.keys(record).sort().join(",") === "format,reasons,status" &&
    record.format === REFUSAL_FORMAT &&
    record.status === "refused" &&
    Array.isArray(record.reasons) &&
    record.reasons.length <= 1000 &&
    record.reasons.every(isCleanReason)
  );
}

/** The reasons of the refusal record at `dir`, if there is one. */
export function refusalReasons(dir) {
  const record = readJson(path.join(dir, REFUSED));
  return record?.format === REFUSAL_FORMAT && Array.isArray(record.reasons)
    ? record.reasons
    : [];
}

/** Removes `dir` without following any link (a link at the path is removed, never its target). */
export function removeTree(dir) {
  let st;
  try {
    st = lstatSync(dir);
  } catch {
    return;
  }
  if (st.isDirectory()) rmSync(dir, { recursive: true, force: true });
  else unlinkSync(dir);
}

/** A fresh sibling directory to assemble into, renamed over the publish path when complete. */
export function stagingFor(dir, what) {
  mkdirSync(path.dirname(dir), { recursive: true });
  return mkdtempSync(
    path.join(path.dirname(dir), `.${path.basename(dir)}-${what}-`),
  );
}

/** Replaces `dir` with a fresh directory that holds only REFUSED.json, `text` exactly (refusalRecordText(), scanned). */
export function writeRefusalDir(dir, text) {
  const staging = stagingFor(dir, "refusal");
  writeFileSync(path.join(staging, REFUSED), text);
  removeTree(dir);
  renameSync(staging, dir);
}

const SOURCE_ENTRY = /(^package\.json$|^\.git$|\.(c|m)?[jt]sx?$|\.py$)/;

/** S1: `p` resolved: its deepest existing ancestor's real path (every link, `.` and `..`
 * resolved; the home and the working directory too) and the rest as spelled below it. */
export function resolvedPath(p) {
  const spelled = path.resolve(p);
  const rest = [];
  for (let head = spelled; ; ) {
    try {
      return path.join(realpathSync(head), ...rest);
    } catch {
      /* not there (or a dangling link): its parent decides */
    }
    const up = path.dirname(head);
    if (up === head) return spelled;
    rest.unshift(path.basename(head));
    head = up;
  }
}
/** A path as spelled and as resolved: a check that reads both is never fooled by a link either way. */
const spellings = (p) => [...new Set([path.resolve(p), resolvedPath(p)])];

/** The root, the working directory or home, or an ancestor of one: never moved. S1: read both as spelled and
 * resolved (a link to the working directory is protected too). */
export function isProtectedPath(dir) {
  const guarded = [process.cwd(), os.homedir()].flatMap(spellings);
  return spellings(dir).some((real) => {
    const isAncestor = (p) => {
      const rel = path.relative(real, p);
      return rel === "" || (!rel.startsWith("..") && !path.isAbsolute(rel));
    };
    return path.dirname(real) === real || guarded.some(isAncestor);
  });
}

/** A directory this check must never move: a checkout or a package, one holding sources at its top, or a protected
 * path (isProtectedPath). */
function holdsSources(dir) {
  if (isProtectedPath(dir)) return true;
  try {
    return readdirSync(path.resolve(dir)).some((name) =>
      SOURCE_ENTRY.test(name),
    );
  } catch {
    return false;
  }
}

/** Moves `dir` off the pull path, to a fresh sibling `.<name>-withheld-*` on this machine; never deletes it. */
export function withholdDir(dir) {
  const aside = mkdtempSync(
    path.join(path.dirname(dir), `.${path.basename(dir)}-withheld-`),
  );
  rmdirSync(aside);
  renameSync(dir, aside);
  return aside;
}

/** The launcher's check: 0 for an intact publication, 1 otherwise. A broken seal publishes nothing (this check holds no
 * registry, so it cannot scan a record of its own): the directory leaves the pull path, unless it holds sources.
 * `status` names the verdict: `published` and `refused` (intact) are the only ones a consumer may transfer. */
export function checkBeforePull(dir) {
  const why = verifySeal(dir);
  if (why === "missing")
    return {
      code: 1,
      status: "missing",
      line: "value-free seal: no publish directory, nothing to pull",
    };
  if (why) {
    if (holdsSources(dir))
      return {
        code: 1,
        status: "untouched",
        line: `value-free seal: REFUSED (${why}); it holds sources, so it is left untouched: pull nothing from it`,
      };
    withholdDir(dir);
    return {
      code: 1,
      status: "withheld",
      line: `value-free seal: REFUSED (${why}); nothing is published (moved off the pull path on this machine)`,
    };
  }
  if (existsSync(path.join(dir, REFUSED)))
    return {
      code: 1,
      status: "refused",
      line: `value-free seal: a refused run; ${REFUSED} is its only file`,
    };
  const files = readJson(path.join(dir, MANIFEST)).files.length;
  return {
    code: 0,
    status: "published",
    line: `value-free seal: intact, ${files} files and ${MANIFEST}`,
  };
}

const textOrNull = (file) => {
  try {
    return readFileSync(file, "utf8");
  } catch {
    return null;
  }
};
const existsNoFollow = (file) => {
  try {
    lstatSync(file);
    return true;
  } catch {
    return false;
  }
};

/** The walk consumer's transfer: `exportDir` is the only path its launcher pulls. Whatever is there first (an
 * earlier job's export on a reused machine) is moved off the pull path, never deleted. Then the check runs on `dir`, and
 * only its intact verdicts are exported: a publication (0) or a refusal record alone (1), copied into a fresh sibling,
 * sealed again there against the seal as it was when checked, and renamed into place, so nothing written to `dir` after
 * the check reaches the export. A broken seal, a missing directory, and a directory the check left untouched because it
 * holds sources export nothing (1); the check itself never moves or deletes what holds sources. An `exportDir`
 * whose name is VALUE_FREE_MIN_LENGTH characters or longer could hold a registered value in every pulled path, so it
 * exports nothing (1) and touches nothing. */
export function exportForPull(dir, exportDir) {
  const result = (code, line, exported) => ({
    code,
    exported,
    line: `${line}; ${exported ? "exported for the pull" : "nothing is exported"}`,
  });
  if (path.basename(path.resolve(exportDir)).length >= VALUE_FREE_MIN_LENGTH)
    return result(
      1,
      `value-free export: the export directory's name could hold a registered value (a value-free name is shorter than ${VALUE_FREE_MIN_LENGTH} characters)`,
      false,
    );
  // S1: `a` at or below `b`, by either spelling of each (a link into the publish directory overlaps it).
  const within = (a, b) =>
    spellings(a).some((x) =>
      spellings(b).some((y) => {
        const rel = path.relative(y, x);
        return rel === "" || (!rel.startsWith("..") && !path.isAbsolute(rel));
      }),
    );
  if (
    isProtectedPath(exportDir) ||
    within(exportDir, dir) ||
    within(dir, exportDir)
  )
    return result(
      1,
      "value-free export: the export path is protected (the root, the working directory, home or an ancestor of one) or overlaps the publish directory",
      false,
    );
  if (existsNoFollow(exportDir)) withholdDir(exportDir);
  const seals = [MANIFEST, REFUSED].map((name) =>
    textOrNull(path.join(dir, name)),
  );
  const check = checkBeforePull(dir);
  if (check.status !== "published" && check.status !== "refused")
    return result(1, check.line, false);
  let staging = stagingFor(exportDir, "copy");
  try {
    for (const e of listTree(dir)) {
      const to = path.join(staging, e.path);
      if (e.type === "dir") mkdirSync(to);
      else if (e.type === "file" && e.nlink === 1)
        writeFileSync(to, readFileSync(path.join(dir, e.path)), {
          flag: "wx",
        });
      else throw new Error("not a regular file");
    }
    const copied = [MANIFEST, REFUSED].map((name) =>
      textOrNull(path.join(staging, name)),
    );
    if (verifySeal(staging) !== null || copied.some((t, i) => t !== seals[i]))
      throw new Error("the copy is not what was checked");
    renameSync(staging, exportDir);
    staging = null;
    return result(check.code, check.line, true);
  } catch {
    if (existsNoFollow(exportDir)) withholdDir(exportDir);
    return result(
      1,
      `${check.line}; value-free export: the copy does not match the seal checked`,
      false,
    );
  } finally {
    if (staging) removeTree(staging);
  }
}

/** Whether this module is the program Node runs, however it was reached: Node resolves the main module's URL through any
 * link, so a path through a linked directory is compared by its real path too (never a silent exit 0). */
export function isMainModule(url, argv1 = process.argv[1]) {
  try {
    return (
      Boolean(argv1) && realpathSync(argv1) === realpathSync(fileURLToPath(url))
    );
  } catch {
    return false;
  }
}

// Run as a program, this check prints nothing of its own. It forwards to the publication choke point
// (lib/value-free-boundary.mjs, mode `seal`), whose stdout is the only one a consumer gets: a fixed STATUS constant shorter
// than any registered value, never a status word, a count or a path. Same arguments and exit codes as before (0 for an
// intact publication, 1 otherwise); a forward that cannot run exits 1 and prints nothing.
if (isMainModule(import.meta.url)) {
  const args = process.argv.slice(2);
  const boundary = fileURLToPath(
    new URL("./value-free-boundary.mjs", import.meta.url),
  );
  const r = spawnSync(process.execPath, [boundary, "seal", ...args], {
    stdio: ["ignore", "inherit", "ignore"],
  });
  process.exitCode = r.status === 0 ? 0 : 1;
}
