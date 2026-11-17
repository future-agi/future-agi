// HR-JRN-01: what a pull pattern REACHES, and withholding all of it, for the publication choke point
// (lib/value-free-boundary.mjs). Before this, a pattern the choke point cannot enumerate exactly was refused, but the
// files it matched stayed on the pull path, and the walk rig transferred them. When the choke point refuses a publication
// that declares such a pattern (a glob, a blank, an absolute or escaping path, an empty pattern), everything ANY declared
// pattern reaches that the walk rig would transfer leaves the pull path, read the way the rig reads a pull (cloud
// vm/exec/exec-collect.sh): the pattern prefixed with the pull root unless absolute, split on blanks, each word globbed by
// bash with globstar, nullglob and dotglob (so `*` matches a dot entry: a withheld `.<name>-withheld-*` sibling too), each
// match read lexically (realpath -s), a directory whole. It moves into ONE quarantine, by its path below the pull root (or,
// outside it, below `outside/` by its absolute path), never deleted.
// A declaration OUTSIDE the pull root (an absolute path, an escaping one, the root's parent)
// is withheld too, not only failed: the rig transfers a regular file whose lexical path lies below /Users or /Volumes
// (exec-collect.sh add()), so whatever a declared pattern reaches there moves, inside the pull root or not. What the rig
// never transfers (outside the pull root and outside those) is never moved. A directory that holds the pull root, the
// working directory, the registries or the quarantine, or is one the rig transfers from, never moves itself: what it holds
// is read in its place (so `..` reaches the root's siblings as well as the root's entries). The registries, the quarantine
// and this machine's home (unless the pull root lies in it) are never read. The quarantine lies outside the pull root and
// outside every directory the rig transfers from: beside the registries, else the system temporary directory, else /tmp
// (on the walk VM, /tmp), so nothing withheld is ever collectable. Without bash, the deepest literal directory bounding
// each word (its components before the first glob) is read instead, and the check has failed. Plain Node, no
// dependencies. The walk launcher refuses such a declaration before dispatch (value-free-boundary.mjs `declare`); this is
// what still holds if one is ever dispatched.
// Every path is RESOLVED before it is classified (realpath: links, `.`, `..` and
// relative spellings). The pull root, the registries, the quarantine and this machine's home are compared in canonical
// form, and so is where each reached entry lies (its directory resolved, the entry itself never followed: a link is the
// link). Whether the rig would take an entry is read both ways, its lexical path and its canonical one (exec-collect.sh
// add() reads the lexical path, `realpath -s`, below /Users or /Volumes; withholding more is the safe side): a test on the
// canonical form alone would miss `/Volumes/x/link/file` with `link` pointing elsewhere. So:
//   - the quarantine is made in the first candidate whose fresh directory, once resolved, lies neither at or below the pull
//     root nor at, below or above /Users or /Volumes (or their resolved forms); a candidate that fails is removed (it is
//     empty) and the next tried. A TMPDIR spelled relative, through `..` or through a link to such a place is never one;
//   - this machine's home is left unread only when its canonical path (and its spelling) is not one the rig could collect
//     from; else what a pull reaches below it is withheld like any other entry (the home itself never moves);
//   - the rig follows a link a pattern names, so a match reached THROUGH a link (one that is not the pull root's own
//     spelling, nor /Users or /Volumes) is withheld by moving that link, where the rig would take it, never what it
//     points to; a match that resolves into the registries, the quarantine or an unread home but which the rig would take
//     by its lexical path is a check that failed.
import { spawnSync } from "node:child_process";
import {
  cpSync,
  lstatSync,
  mkdirSync,
  mkdtempSync,
  readdirSync,
  realpathSync,
  renameSync,
  rmdirSync,
  rmSync,
} from "node:fs";
import os from "node:os";
import path from "node:path";

/** A character bash globs on (or escapes with), in a word. */
const GLOB = /[*?[\]{}\\]/;
const BLANKS = /[ \t\n]+/;
/** Passes of move-then-read again; what is still reached after them is a failure. */
const PASSES = 3;

// The rig's reading of a pull, in bash itself: the same shell options, the same prefix, the same splitting.
const RIG_READING = [
  "IFS=$' \\t\\n'",
  "shopt -s globstar nullglob dotglob || exit 1",
  "root=$1; shift",
  'cd "$root" || exit 1',
  'for pattern in "$@"; do',
  '  [[ "$pattern" == /* ]] || pattern="$root/$pattern"',
  "  for match in $pattern; do printf '%s\\0' \"$match\"; done",
  "done",
  "exit 0",
].join("\n");

/** `child` strictly below `parent`. */
export const inside = (parent, child) => {
  const rel = path.relative(parent, child);
  return (
    rel !== "" &&
    rel !== ".." &&
    !rel.startsWith(`..${path.sep}`) &&
    !path.isAbsolute(rel)
  );
};
/** `child` is `parent` or below it. */
const within = (child, parent) => child === parent || inside(parent, child);

/** The directories the walk rig transfers from: only a regular file whose lexical path lies below one of these travels
 * (cloud vm/exec/exec-collect.sh add()). */
export const TRANSPORTED = Object.freeze(["/Users", "/Volumes"]);
/** A path below a directory the walk rig transfers from. */
export const transported = (at) => TRANSPORTED.some((t) => inside(t, at));

/** The canonical path of an existing entry (every link, `.`, `..` and relative spelling resolved); null when it does not
 * resolve. */
export function canonical(at) {
  try {
    return realpathSync(at);
  } catch {
    return null;
  }
}

/** Where the directory entry `at` (absolute, lexical) canonically lies: its directory resolved, the entry itself never
 * followed (a link is the link); null when its directory does not resolve. */
function entryOf(at) {
  if (at === path.sep) return at;
  const dir = canonical(path.dirname(at));
  return dir === null ? null : path.join(dir, path.basename(at));
}

/** The pull root as the rig spells it: the working directory's own spelling (PWD) when the root is the working directory
 * and PWD resolves to it, else the root as given. */
function lexicalRoot(root, rootC) {
  const pwd = process.env.PWD;
  if (
    path.resolve(root) === process.cwd() &&
    pwd &&
    path.isAbsolute(pwd) &&
    canonical(pwd) === rootC
  )
    return path.resolve(pwd);
  return path.resolve(root);
}

/** Where a withholding run stands: the pull root (its lexical spelling and its canonical path) and the directories the rig
 * transfers from (as spelled, and resolved where that differs). Throws when the pull root does not resolve. */
export function placeOf(root) {
  const rootC = realpathSync(root);
  const rootL = lexicalRoot(root, rootC);
  const roots = [...new Set([rootL, rootC])];
  const tops = [
    ...new Set([...TRANSPORTED, ...TRANSPORTED.map(canonical).filter(Boolean)]),
  ];
  return {
    rootL,
    rootC,
    roots,
    tops,
    /** At or below the pull root (either spelling), or strictly below a directory the rig transfers from: the rig would
     * take it. */
    transferable: (p) =>
      roots.some((r) => within(p, r)) || tops.some((t) => inside(t, p)),
    /** A directory the rig could collect from: at or below the pull root, or at, below or above a directory the rig
     * transfers from. */
    collectable: (dir) =>
      [...roots, ...tops].some((t) => within(dir, t) || within(t, dir)),
  };
}

/** This machine's home, resolved, and whether it is left unread: only when neither its canonical path nor its spelling is
 * a directory the rig could collect from. */
export function homeOf(place) {
  let spelled = "";
  try {
    spelled = os.homedir();
  } catch {
    /* none */
  }
  const at = spelled ? canonical(path.resolve(spelled)) : null;
  if (at === null) return { at: null, sealed: false };
  return {
    at,
    sealed: !place.collectable(at) && !place.collectable(path.resolve(spelled)),
  };
}

/** Every match of `patterns` read from `root` as the rig reads them, absolute and lexical; null when bash cannot read
 * them (no bash, an error, a name this cannot spell exactly). */
export function rigReading(root, patterns) {
  let r;
  try {
    r = spawnSync(
      "bash",
      [
        "--noprofile",
        "--norc",
        "-c",
        RIG_READING,
        "rig-reading",
        root,
        ...patterns,
      ],
      {
        // Nothing of this environment reaches the shell (BASH_ENV, SHELLOPTS, GLOBIGNORE): only where bash is.
        env: { PATH: process.env.PATH ?? "" },
        stdio: ["ignore", "pipe", "ignore"],
        maxBuffer: 1 << 30,
      },
    );
  } catch {
    return null;
  }
  if (r.error || r.status !== 0) return null;
  const matches = [];
  for (let at = 0, end; (end = r.stdout.indexOf(0, at)) >= 0; at = end + 1) {
    const raw = r.stdout.subarray(at, end);
    const name = raw.toString("utf8");
    if (!Buffer.from(name, "utf8").equals(raw)) return null;
    matches.push(path.resolve(root, name));
  }
  return matches;
}

/** The deepest literal directory bounding every match of each word of `pattern` (no bash: what moves instead). */
export function bounds(root, pattern) {
  const full = pattern.startsWith("/") ? pattern : `${root}/${pattern}`;
  return full
    .split(BLANKS)
    .filter(Boolean)
    .map((word) => {
      const parts = (word.startsWith("/") ? word : `${root}/${word}`).split(
        "/",
      );
      const first = parts.findIndex((p) => GLOB.test(p));
      let dir = path.resolve(
        "/",
        ...(first < 0 ? parts : parts.slice(0, first)),
      );
      // A `..` after the first glob climbs above whatever the glob matched.
      const ups =
        first < 0 ? 0 : parts.slice(first).filter((p) => p === "..").length;
      for (let i = 0; i < ups; i++) dir = path.dirname(dir);
      return dir;
    });
}

/** The first link on the way down to `at` (never `at` itself) that is neither the pull root's own spelling (the root or
 * one of its ancestors) nor a directory the rig transfers from: the rig follows it to reach `at`, so it is what is
 * withheld; null when there is none. */
function door(at, place) {
  const parts = at.split(path.sep).filter(Boolean);
  let p = "";
  for (const part of parts.slice(0, -1)) {
    p += path.sep + part;
    if (within(place.rootL, p) || TRANSPORTED.includes(p)) continue;
    if (lstatSync(p).isSymbolicLink()) return p;
  }
  return null;
}

/** What `matches` reach that the walk rig would transfer: the entries to withhold, and whether any cannot be (`stuck`).
 * `keep.anchors`: directories never moved (the pull root, the working directory, the registries, the quarantine, this
 * home), whose entries are read in their place when a match holds one of them; `keep.sealed`: never read at all (the
 * registries, `keep.registries`, the quarantine, an unread home). Every comparison with them is canonical. Below the pull root every entry
 * reached is withheld; outside it, only what the rig would take (TRANSPORTED), by either spelling. */
function reached(place, matches, keep) {
  const targets = new Set();
  let stuck = false;
  const visit = (at) => {
    // Reached through a link: the rig follows it, so the link is what moves (where the rig would take it), never what it
    // points to.
    const link = door(at, place);
    if (link !== null) {
      if (place.transferable(link)) targets.add(link);
      return;
    }
    const entry = entryOf(at);
    if (entry === null) {
      stuck = true;
      return;
    }
    const hit = keep.sealed.find((s) => within(entry, s));
    if (hit !== undefined) {
      // Never read. The registries are removed once read; what stays (the quarantine, an unread home) and which the rig
      // would still take by its spelling cannot be withheld: the check has failed.
      if (hit !== keep.registries && place.transferable(at)) stuck = true;
      return;
    }
    if (
      [...keep.anchors, ...place.tops].some(
        (a) => within(a, at) || within(a, entry),
      )
    ) {
      // It holds the pull root, the registries, the quarantine or the home, or the rig transfers from it: what it holds
      // moves, never it. (A listing that fails throws: the caller's check has failed.)
      if (!lstatSync(at).isDirectory()) return;
      for (const name of readdirSync(at)) visit(path.join(at, name));
      return;
    }
    if (place.transferable(at) || place.transferable(entry)) targets.add(at);
    // Else: outside the pull root and outside what the rig transfers, nothing travels from it, and it never moves.
  };
  for (const m of matches) {
    try {
      lstatSync(m);
    } catch {
      continue; // nothing there: nothing is transferred
    }
    visit(m);
  }
  return { targets: [...targets], stuck };
}

/** A fresh quarantine: beside the registries, else the system temporary directory, else /tmp, the first whose fresh
 * directory, once resolved, the rig could never collect from (place.collectable); a candidate that fails is removed (it is
 * empty). Null when none takes one. */
function quarantineFor(place, registries) {
  for (const base of [path.dirname(registries), os.tmpdir(), "/tmp"]) {
    let made;
    try {
      made = mkdtempSync(path.join(path.resolve(base), "value-free-withheld-"));
    } catch {
      continue;
    }
    const at = canonical(made);
    if (at !== null && !place.collectable(at)) return at;
    try {
      rmdirSync(made);
    } catch {
      /* an empty directory left: it holds nothing */
    }
  }
  return null;
}

/** Moves `from` into `quarantine` by its path below the root (outside the root: below `outside/` by its absolute path);
 * across file systems, a copy then the original removed. Never deleted. */
function moveInto(quarantine, place, from) {
  const root = place.roots.find((r) => within(from, r));
  const rel =
    root !== undefined ? path.relative(root, from) : path.join("outside", from);
  let to = path.join(quarantine, rel);
  try {
    lstatSync(to);
    to = path.join(mkdtempSync(path.join(quarantine, "again-")), rel);
  } catch {
    /* free */
  }
  mkdirSync(path.dirname(to), { recursive: true });
  try {
    renameSync(from, to);
  } catch (error) {
    if (error?.code !== "EXDEV") throw error;
    cpSync(from, to, {
      recursive: true,
      verbatimSymlinks: true,
      errorOnExist: true,
      force: false,
    });
    rmSync(from, { recursive: true, force: true });
  }
}

/** Withholds everything `patterns` reach that the walk rig would transfer, into one quarantine it never collects. True when
 * nothing is reached any more and nothing failed: bash read them and every move succeeded. */
export function withholdReach({ root, patterns, registries }) {
  try {
    return withhold(root, patterns, registries);
  } catch {
    return false;
  }
}

function withhold(root, patterns, registries) {
  const place = placeOf(root);
  const registriesC = canonical(registries) ?? path.resolve(registries);
  const home = homeOf(place);
  let quarantine = null;
  let ok = true;
  const keep = () => {
    const sealed = [
      registriesC,
      ...(quarantine ? [quarantine] : []),
      ...(home.sealed ? [home.at] : []),
    ];
    return {
      registries: registriesC,
      sealed,
      anchors: [
        ...place.roots,
        process.cwd(),
        ...(home.at ? [home.at] : []),
        ...sealed,
      ],
    };
  };
  const move = (targets) => {
    if (!targets.length) return;
    quarantine ??= quarantineFor(place, registriesC);
    if (!quarantine) {
      ok = false;
      return;
    }
    const moved = [];
    // Shallowest first: an entry inside one already moved went with it.
    const depth = (p) => p.split(path.sep).length;
    for (const t of [...targets].sort(
      (a, b) => depth(a) - depth(b) || (a < b ? -1 : 1),
    )) {
      if (moved.some((m) => inside(m, t))) continue;
      try {
        lstatSync(t);
      } catch {
        continue;
      }
      try {
        moveInto(quarantine, place, t);
        moved.push(t);
      } catch {
        ok = false;
      }
    }
  };
  for (let pass = 0; ; pass++) {
    let matches = rigReading(place.rootL, patterns);
    if (matches === null) {
      matches = patterns.flatMap((p) => bounds(place.rootL, p));
      ok = false;
    }
    const { targets, stuck } = reached(place, matches, keep());
    if (stuck) ok = false;
    if (!targets.length) return ok;
    if (pass === PASSES || !ok) {
      // Without bash (or with nothing more to learn) one move of what is bounded, and the check has failed.
      move(targets);
      return false;
    }
    move(targets);
  }
}
