// HR-JRN-01: THE publication choke point of value-free mode. Every consumer that lets anything leave the machine
// (the walk job, run-walks.sh's export, the CI upload, an external pull of a publish directory, and the seal CLI kept for
// callers outside this tree) goes through this one program, and this module is the only one of the value-free CLIs that
// writes a byte to stdout (value-free-seal.mjs, run as a program, forwards here). Its three invariants:
//   (I1) the registries are read whole, or the publication FAILS CLOSED: nothing is transferred (every pull is moved
//        aside), the held output is never printed, the exit is 1, and the one thing printed is STATUS.registry, a fixed
//        constant. A registry that is not read whole is never treated as an empty one.
//   (I2) the scan runs over the EXACT bytes that are emitted, after every transformation (the newline a held output
//        that does not end in one is given, and the status constant after it): emit() writes exactly the buffer
//        scanned, in full. What it prints of its own is only a STATUS constant: never a reason, a count, a path or
//        anything it scanned. Every constant is shorter than VALUE_FREE_MIN_LENGTH bytes, the shortest value a run can
//        register, and a registry holding a shorter string is not read whole (register() never writes one), so no
//        registered value fits in any of them: they are value-free by construction, registry or not.
//   (I3) every consumer goes through it (harness/value-free-consumers.spec.ts keeps the inventory): no script or
//        workflow runs the seal or boundary internals, captures their output or echoes it.
// What crosses (a pull pattern is `<dir>/**`, the whole tree there, dotfiles included, links never followed, or an exact
// relative path inside the pull root, neither holding a blank): every path component of every entry a pull transfers,
// relative to the pull root, and that whole relative path (checkPath: a value across components too); every such file's
// content through every layer (lib/value-free-reading.mjs's Scan); the held output read as its own document (a
// pretty-printed JSON document is one only without the status after it); and the emitted bytes. Any hit; any problem the
// scan reports; any link, hard link or special file; any pattern it cannot enumerate exactly; registries it cannot read
// whole; and any error refuse the WHOLE export: every pull's entry is moved to a sibling `.<name>-withheld-*` on this
// machine (never deleted). I1: when a declared pattern is one it cannot enumerate exactly (a glob, a blank, an
// absolute or escaping path, an empty pattern), everything ANY declared pattern reaches, read the way the walk rig reads a
// pull, also leaves the pull root, into one quarantine outside it (lib/value-free-reach.mjs). It
// also covers what a declaration outside the pull root reaches (an absolute path, an escaping one, the root's
// parent) wherever the walk rig transfers from (/Users, /Volumes), never only a failed check; and the walk launcher runs
// `declare` on the pulls it is about to declare BEFORE dispatch: any pull that is not one exact path below the pull root
// (patternRoot) refuses the launch, so no job is created to collect it. Plain Node:
//
//   node e2e/lib/value-free-boundary.mjs declare -- <pull>...                                       the launcher
//   node e2e/lib/value-free-boundary.mjs publish --registries <dir> [--print <held>] -- <pull>...   the final check
//   node e2e/lib/value-free-boundary.mjs export <publish-dir> <export-dir>                         run-walks.sh
//   node e2e/lib/value-free-boundary.mjs ci --registries <dir> --print <held> --publish <dir> --github-output <file>
//   node e2e/lib/value-free-boundary.mjs seal <publish-dir> [--export <export-dir>]                the seal CLI's form
//
// (`--registries ...` with no mode is `publish`.) publish and ci remove the registries (they hold the raw values) when
// they end, whatever the verdict. The exit is 0 (clean) or 1, never another.
import {
  appendFileSync,
  lstatSync,
  readFileSync,
  rmSync,
  writeSync,
} from "node:fs";
import path from "node:path";
import {
  canonical,
  homeOf,
  inside,
  placeOf,
  withholdReach,
} from "./value-free-reach.mjs";
import { Scan, VALUE_FREE_MIN_LENGTH } from "./value-free-reading.mjs";
import { recordLayer } from "./value-free-record.mjs";
import {
  checkBeforePull,
  exportForPull,
  isMainModule,
  isProtectedPath,
  listTree,
  REFUSED,
  verifySeal,
  withholdDir,
} from "./value-free-seal.mjs";

/** Everything this program prints of its own: one of these, once. Each is shorter than VALUE_FREE_MIN_LENGTH bytes. */
export const STATUS = Object.freeze({
  clean: "VFOK\n", // what crosses was scanned clean (publish, ci, export, seal: an intact publication)
  record: "VFRR\n", // a refused run: only its intact refusal record crosses (exit 1)
  registry: "VFE1\n", // I1: the registries cannot be read whole, or a run's registry was never adopted
  value: "VFE2\n", // a registered value, or a layer the scan cannot read, in what would cross
  entry: "VFE3\n", // a link, a hard link, a special file, or a pull it cannot enumerate exactly (declare: refused)
  seal: "VFE4\n", // no intact publication (or refusal record) to transfer
  failed: "VFE5\n", // usage, an unreadable held output, or the check failed
});
/** When several reasons hold, the one printed. */
const PRECEDENCE = ["registry", "failed", "value", "entry", "seal"];
const GLOB = /[*?[\]{}]/;
/** A blank: the walk rig splits a pull pattern on blanks into several. */
const BLANK = /\s/;

/** Every value the runs registered: each `*.jsonl` file at any depth below `dir`, every line complete and JSON (a string
 * is a value; an object is a refusal, a length and a kind). Each directory directly below `dir` is one run's registry,
 * which its runner adopted (lib/value-free.ts records itself there with its own file): one holding no `.jsonl` file is a
 * run that registered somewhere else. A string shorter than VALUE_FREE_MIN_LENGTH is one register() never writes. null
 * for any of these, or when the registries cannot be read whole: never an empty list (I1). */
export function registeredValues(dir) {
  try {
    if (!lstatSync(dir).isDirectory()) return null;
    const values = new Set();
    const tree = listTree(dir);
    const runs = tree.filter((e) => e.type === "dir" && !e.path.includes("/"));
    const adopted = (r) =>
      tree.some(
        (e) =>
          e.type === "file" &&
          e.path.startsWith(`${r.path}/`) &&
          e.path.endsWith(".jsonl"),
      );
    if (!runs.every(adopted)) return null;
    for (const e of tree) {
      if (e.type === "dir") continue;
      if (e.type !== "file" || !e.path.endsWith(".jsonl") || (e.nlink ?? 1) > 1)
        return null;
      const text = readFileSync(path.join(dir, e.path), "utf8");
      if (text !== "" && !text.endsWith("\n")) return null;
      for (const line of text.split("\n").slice(0, -1)) {
        const entry = JSON.parse(line);
        if (typeof entry === "string") {
          if (entry.length < VALUE_FREE_MIN_LENGTH) return null;
          values.add(entry);
        } else if (!entry || typeof entry !== "object" || Array.isArray(entry))
          return null;
      }
    }
    return [...values];
  } catch {
    return null;
  }
}

/** The entry a pull pattern transfers, relative and POSIX: `<dir>/**` or an exact path; null for one this cannot
 * enumerate exactly (a glob anywhere else, a blank, an absolute path, `.` or `..`, an empty component, an empty pattern). */
export function patternRoot(pattern) {
  const rel = pattern.endsWith("/**") ? pattern.slice(0, -3) : pattern;
  if (
    !rel ||
    GLOB.test(rel) ||
    BLANK.test(rel) ||
    rel.startsWith("/") ||
    rel.includes("\\")
  )
    return null;
  return rel.split("/").some((p) => p === "" || p === "." || p === "..")
    ? null
    : rel;
}

/** The walk launcher's check BEFORE dispatch: true when the declaration is one this check refuses, a pull that
 * is not one exact path below the pull root (patternRoot: an absolute, escaping, root or above, empty, glob or blank
 * pattern) or no pull at all. It reads no registry and no file: what the launcher is about to declare is all it sees. */
export function declarationRefused(pulls) {
  return pulls.length === 0 || pulls.some((p) => patternRoot(p) === null);
}

/** A transferred path, as the scan reads it: every component, and the whole relative path. The reporter reads the publish
 * directory's own path and every published entry's path below the checkout with it too (lib/value-free-publish.ts). */
export function checkPath(scan, rel) {
  for (const part of rel.split("/")) scan.check(part, "name");
  scan.check(rel, "name");
}

/** The bytes a clean verdict emits: the held output exactly, a newline when it does not end in one, then `status`. */
export function emittedBytes(held, status) {
  const parts = [];
  if (held && held.length) {
    parts.push(held);
    if (held.at(-1) !== 0x0a) parts.push(Buffer.from("\n"));
  }
  parts.push(Buffer.from(status));
  return Buffer.concat(parts);
}

/** Writes exactly `buf` to stdout, every byte (a partial write is retried, never left as a cut). */
export function emit(buf) {
  for (let at = 0; at < buf.length; ) {
    try {
      at += writeSync(1, buf, at, buf.length - at);
    } catch (error) {
      if (error?.code !== "EAGAIN") throw error;
    }
  }
}

const overlaps = (a, b) => a === b || inside(a, b) || inside(b, a);

/** S1: where the entry a pull transfers canonically lies, or null when it cannot be admitted by its spelling:
 * the deepest existing ancestor of `rel` below the pull root, resolved (realpath: every link, `.` and `..`), must be
 * exactly where `rel` spells it, so no link below the root lies on the way. The entry itself is never followed (a link
 * there is the link, refused as an entry when it is listed). `rootC`: the pull root's canonical path (null: it does not
 * resolve, and nothing is admitted). */
export function canonicalEntry(root, rootC, rel) {
  if (rootC === null) return null;
  const parts = rel.split("/");
  for (let n = parts.length - 1; n >= 0; n--) {
    const prefix = parts.slice(0, n);
    const spelled = path.join(root, ...prefix);
    try {
      lstatSync(spelled);
    } catch {
      continue; // not there: its parent decides
    }
    return canonical(spelled) === path.join(rootC, ...prefix)
      ? path.join(rootC, ...parts)
      : null;
  }
  return null;
}

/** S1: the private side, canonical: the registries, and this machine's home when it is left unread
 * (value-free-reach.mjs homeOf). Nothing a pull transfers may lie at, in or above one of them. */
function privateRoots(root, registries) {
  const roots = [canonical(registries) ?? path.resolve(registries)];
  try {
    const home = homeOf(placeOf(root));
    if (home.sealed && home.at) roots.push(home.at);
  } catch {
    /* the pull root does not resolve: canonicalEntry admits nothing */
  }
  return roots;
}

const refusal = (reasons) =>
  STATUS[PRECEDENCE.find((r) => reasons.has(r)) ?? "failed"];

/** The decision, from `root` (the pull root). `held`: the held output's path, emitted only when everything is clean.
 * `side`: other bytes a clean verdict releases (the CI step output line), scanned exactly as they will be written.
 * `ok`: the status (and exit) of a clean verdict. Returns what to emit: the scanned buffer, or a STATUS constant alone. */
export function finalCheck({
  root,
  registries,
  held,
  pulls,
  side = [],
  ok = { status: STATUS.clean, code: 0 },
}) {
  const reasons = new Set();
  const roots = [];
  let unenumerable = false; // a declared pattern it cannot enumerate exactly
  // S1: every pull is classified where it CANONICALLY lies, before anything else, on
  // the path that accepts it too: every existing ancestor below the pull root resolved (a link on the way refuses it), and
  // what crosses disjoint from the private side (the registries, an unread home). Refused so, it is withheld the way the
  // rig reads it (withholdReach: a link is moved where the rig would take it, the private side is never moved or read).
  const rootC = canonical(root);
  const privates = privateRoots(root, registries);
  for (const pattern of pulls) {
    const rel = patternRoot(pattern);
    const at = rel === null ? null : canonicalEntry(root, rootC, rel);
    if (
      at === null ||
      privates.some((p) => overlaps(p, at)) ||
      isProtectedPath(path.join(root, rel))
    ) {
      reasons.add("entry");
      unenumerable = true;
    } else roots.push(rel);
  }
  let out = null;
  try {
    const values = registeredValues(registries);
    let output = Buffer.alloc(0);
    if (held !== undefined) {
      try {
        output = readFileSync(held);
      } catch {
        reasons.add("failed");
      }
    }
    if (values === null) reasons.add("registry");
    else {
      const scan = new Scan(values);
      for (const rel of roots) {
        let st;
        try {
          st = lstatSync(path.join(root, rel));
        } catch {
          continue; // nothing there: nothing is transferred
        }
        const entries = st.isDirectory()
          ? [{ path: "", type: "dir" }, ...listTree(path.join(root, rel))]
          : [
              {
                path: "",
                type: st.isFile()
                  ? "file"
                  : st.isSymbolicLink()
                    ? "symlink"
                    : "special",
                nlink: st.nlink,
              },
            ];
        for (const e of entries) {
          const at = e.path ? `${rel}/${e.path}` : rel;
          checkPath(scan, at);
          if (
            e.type === "symlink" ||
            e.type === "special" ||
            (e.type === "file" && (e.nlink ?? 1) > 1)
          )
            reasons.add("entry");
          // S2: a report record is read against its closed schema (value-free-record.mjs recordLayer).
          else if (e.type === "file")
            scan.bytes(
              readFileSync(path.join(root, at)),
              0,
              "content",
              recordLayer(at),
            );
        }
      }
      // I2: the held output read as its own document(s) first: a pretty-printed JSON
      // document is one only without the status after it, and its layers (a base64 field) are read only as one. Then the
      // exact bytes stdout gets, and each side output exactly as written.
      if (output.length) scan.bytes(output);
      out = emittedBytes(output, ok.status);
      scan.bytes(out);
      for (const buf of side) scan.bytes(buf);
      if (scan.findings.size) reasons.add("value");
    }
  } catch {
    reasons.add("failed");
  }
  if (!reasons.size) return { code: ok.code, out, side };
  // The whole export is refused: every pull's entry leaves the pull path, never deleted.
  for (const rel of roots) {
    const at = path.join(root, rel);
    try {
      lstatSync(at);
    } catch {
      continue;
    }
    try {
      withholdDir(at);
    } catch {
      reasons.add("failed");
    }
  }
  // I1: a pattern it cannot enumerate exactly still reaches what the transport's reading of it
  // reaches, a withheld sibling above included (the rig globs with dotglob): all of it leaves the pull root, into one
  // quarantine outside it. So does what it reaches outside the pull root that the rig transfers (below /Users or
  // /Volumes). Anything it cannot withhold so (no bash, no quarantine, a move that failed) is a check that failed.
  if (unenumerable && !withholdReach({ root, patterns: pulls, registries }))
    reasons.add("failed");
  return { code: 1, out: Buffer.from(refusal(reasons)), side: [] };
}

/** The CI consumer: the publish directory's seal decides what the upload may take (an intact publication's html/, or an
 * intact refusal record alone); the final check then reads it, the held suite output and the step output line. */
export function ciCheck({ registries, held, publish, githubOutput }) {
  const why = verifySeal(publish);
  let pulls = null;
  let ok = null;
  let line = null;
  if (why === null && lstatOrNull(path.join(publish, REFUSED))) {
    [pulls, ok, line] = [
      [REFUSED],
      { status: STATUS.record, code: 1 },
      `path=${path.join(publish, REFUSED)}\n`,
    ];
  } else if (
    why === null &&
    lstatOrNull(path.join(publish, "html"))?.isDirectory()
  ) {
    [pulls, ok, line] = [
      ["html/**"],
      { status: STATUS.clean, code: 0 },
      `path=${path.join(publish, "html")}\n`,
    ];
  } else if (why !== null && why !== "missing") checkBeforePull(publish); // a broken seal leaves the upload path
  if (!pulls) {
    const status =
      registeredValues(registries) === null ? STATUS.registry : STATUS.seal;
    return { code: 1, out: Buffer.from(status), side: [] };
  }
  const verdict = finalCheck({
    root: publish,
    registries,
    held,
    pulls,
    side: [Buffer.from(line)],
    ok,
  });
  if (verdict.side.length) appendFileSync(githubOutput, verdict.side[0]);
  return verdict;
}

/** The seal check and the walk export (run-walks.sh), as a STATUS: an intact publication (0), an intact refusal record
 * (1), anything else (1). `exportDir`: the copy run-walks.sh's launch pulls (value-free-seal.mjs's exportForPull). */
export function sealCheck(dir, exportDir) {
  if (exportDir === undefined) {
    const { status } = checkBeforePull(dir);
    if (status === "published")
      return { code: 0, out: Buffer.from(STATUS.clean) };
    return {
      code: 1,
      out: Buffer.from(status === "refused" ? STATUS.record : STATUS.seal),
    };
  }
  const r = exportForPull(dir, exportDir);
  if (!r.exported) return { code: 1, out: Buffer.from(STATUS.seal) };
  return r.code === 0
    ? { code: 0, out: Buffer.from(STATUS.clean) }
    : { code: 1, out: Buffer.from(STATUS.record) };
}

function lstatOrNull(file) {
  try {
    return lstatSync(file);
  } catch {
    return null;
  }
}

const plain = (a) => typeof a === "string" && a !== "" && !a.startsWith("--");

/** `--<name> <value>` options up to `--` (or the end), each once; null for anything else. */
function options(args, names) {
  const found = {};
  let i = 0;
  for (; i < args.length && args[i] !== "--"; i += 2) {
    if (
      !names.includes(args[i]) ||
      !plain(args[i + 1]) ||
      found[args[i]] !== undefined
    )
      return null;
    found[args[i]] = args[i + 1];
  }
  return { found, rest: args.slice(i + 1), sawEnd: args[i] === "--" };
}

/** One run of the program: `args` without the mode's `node <file>`. Returns { code, out }. */
export function run(args) {
  const [mode, ...rest] = args[0]?.startsWith("--")
    ? ["publish", ...args]
    : args;
  let registries;
  try {
    // The launcher's declaration check. Clean, it prints nothing (the launch goes on); refused, one constant.
    if (mode === "declare") {
      if (rest[0] !== "--") return failed();
      return declarationRefused(rest.slice(1))
        ? { code: 1, out: Buffer.from(STATUS.entry) }
        : { code: 0, out: Buffer.alloc(0) };
    }
    if (mode === "publish") {
      const o = options(rest, ["--registries", "--print"]);
      // Every pull as given, an empty one included (the rig reads it as the whole pull root): the check classifies them.
      if (!o || !o.sawEnd || !o.found["--registries"] || o.rest.length === 0)
        return failed();
      registries = path.resolve(o.found["--registries"]);
      const held = o.found["--print"] && path.resolve(o.found["--print"]);
      return finalCheck({
        root: process.cwd(),
        registries,
        held,
        pulls: o.rest,
      });
    }
    if (mode === "ci") {
      const o = options(rest, [
        "--registries",
        "--print",
        "--publish",
        "--github-output",
      ]);
      if (!o || o.sawEnd || Object.keys(o.found).length !== 4) return failed();
      registries = path.resolve(o.found["--registries"]);
      return ciCheck({
        registries,
        held: path.resolve(o.found["--print"]),
        publish: path.resolve(o.found["--publish"]),
        githubOutput: path.resolve(o.found["--github-output"]),
      });
    }
    if (mode === "export" && rest.length === 2 && rest.every(plain))
      return sealCheck(path.resolve(rest[0]), path.resolve(rest[1]));
    if (mode === "seal" && rest.length === 1 && plain(rest[0]))
      return sealCheck(path.resolve(rest[0]));
    if (
      mode === "seal" &&
      rest.length === 3 &&
      plain(rest[0]) &&
      rest[1] === "--export" &&
      plain(rest[2])
    )
      return sealCheck(path.resolve(rest[0]), path.resolve(rest[2]));
    return failed();
  } catch {
    return failed();
  } finally {
    // The registries hold the raw values: removed once read, whatever the verdict (never a protected path).
    try {
      if (registries && !isProtectedPath(registries))
        rmSync(registries, { recursive: true, force: true });
    } catch {
      /* best effort */
    }
  }
}
const failed = () => ({ code: 1, out: Buffer.from(STATUS.failed) });

if (isMainModule(import.meta.url)) {
  let code = 1;
  try {
    const verdict = run(process.argv.slice(2));
    code = verdict.code;
    emit(verdict.out);
  } catch {
    code = 1;
  }
  process.exitCode = code;
}
