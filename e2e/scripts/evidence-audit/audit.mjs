// The independent evidence auditor (708 add.78): every byte of an evidence
// tree, through the broad detectors in detect.mjs:
//   - inside ZIP and gzip archives, including what an archive carries besides
//     its entries (names, comments, extra fields, unclaimed bytes, gzip header
//     fields);
//   - through JSON-escaped and percent-encoded layers (repeated until nothing
//     changes) and base64 / base64url layers (decoded text, archives and
//     binary payloads);
//   - in binary payloads, over their printable text spans (ASCII, UTF-16LE,
//     UTF-16BE) as well as the exact-shape detectors over the raw bytes.
// Any finding, anything it cannot read, and any decoding BOUND it reaches
// (LIMITS) refuses the evidence: fail closed, never a silent stop.
// The report is value-free: kind, file and archive-entry LABELS (a path or
// entry name that itself carries a finding is withheld and replaced by its
// position), offset, layer, the nearest field NAME and a keyed digest that is
// only comparable within one report. Real paths stay in memory, on a symbol
// key JSON never serializes, for quarantineAll() and the CLI's --stage.
import { createHash, createHmac, randomBytes } from "node:crypto";
import {
  copyFileSync,
  lstatSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  renameSync,
  unlinkSync,
} from "node:fs";
import path from "node:path";
import { detect } from "./detect.mjs";
import {
  gunzip,
  gzipHeader,
  looksLikeGzip,
  looksLikeZip,
  zipEntries,
} from "./unzip.mjs";

export const AUDITOR = "e2e/scripts/evidence-audit";
const MAX_FILE = 1024 * 1024 * 1024;

/**
 * Decoding bounds. Reaching one is a problem that refuses the evidence.
 *   archiveDepth   ZIP/gzip archives nested inside each other
 *   base64Depth    base64 layers nested inside each other
 *   normalize      successive JSON-unescape / percent-decode steps of one text
 *   base64Decodes  distinct base64 candidates decoded per evidence file
 * Callers (tests) may only TIGHTEN them.
 */
export const LIMITS = Object.freeze({
  archiveDepth: 4,
  base64Depth: 3,
  normalize: 8,
  base64Decodes: 200_000,
});
// Binary that a base64 token decodes to and that is shorter than this is an
// identifier or raw key material: it is not span-scanned, and the raw token
// stays under the entropy check (detect.mjs) because it was not decoded to text.
const MIN_BINARY_PAYLOAD = 64;
const MIN_SPAN = 6;
const REAL = Symbol("evidence-audit real paths");

const CODE =
  /\.(?:m?js|cjs|jsx|tsx?|css|map|wasm)$|(?:^|\/)src@[0-9a-f]+\.txt$/i;
const MAGIC = [
  [0x89, 0x50, 0x4e, 0x47],
  [0xff, 0xd8, 0xff],
  [0x47, 0x49, 0x46, 0x38],
  [0x52, 0x49, 0x46, 0x46],
  [0x77, 0x4f, 0x46],
  [0x25, 0x50, 0x44, 0x46],
  [0x7f, 0x45, 0x4c, 0x46],
  [0x00, 0x61, 0x73, 0x6d],
];

function isBinary(buf) {
  if (MAGIC.some((sig) => sig.every((b, i) => buf[i] === b))) return true;
  const head = buf.subarray(0, 8192);
  if (head.includes(0)) return true;
  // Mostly non-UTF-8 bytes: treat as binary.
  const text = head.toString("utf8");
  const bad = (text.match(/�/g) ?? []).length;
  return bad > Math.max(8, text.length / 50);
}

const printableByte = (b) =>
  (b >= 0x20 && b <= 0x7e) || b === 0x09 || b === 0x0a || b === 0x0d;

/**
 * The printable text runs of a binary payload: ASCII runs, and UTF-16LE and
 * UTF-16BE runs (text a NUL test would call binary), each at least MIN_SPAN
 * characters, joined by newlines. `offsetOf(i)` maps an offset in the joined
 * text back to the byte offset of its run.
 */
export function textSpans(buf, min = MIN_SPAN) {
  const runs = [];
  let s = -1;
  for (let i = 0; i <= buf.length; i++) {
    if (i < buf.length && printableByte(buf[i])) {
      if (s < 0) s = i;
      continue;
    }
    if (s >= 0 && i - s >= min)
      runs.push({ at: s, text: buf.toString("latin1", s, i) });
    s = -1;
  }
  for (const bigEndian of [false, true])
    for (const align of [0, 1]) {
      let start = -1;
      let chars = [];
      for (let i = align; ; i += 2) {
        const inside = i + 1 < buf.length;
        const lo = inside ? (bigEndian ? buf[i + 1] : buf[i]) : 0;
        const hi = inside ? (bigEndian ? buf[i] : buf[i + 1]) : 1;
        if (inside && hi === 0 && printableByte(lo)) {
          if (start < 0) {
            start = i;
            chars = [];
          }
          chars.push(lo);
          continue;
        }
        if (start >= 0 && chars.length >= min)
          runs.push({ at: start, text: String.fromCharCode(...chars) });
        start = -1;
        if (!inside) break;
      }
    }
  const starts = [];
  let pos = 0;
  for (const r of runs) {
    starts.push(pos);
    pos += r.text.length + 1;
  }
  return {
    text: runs.map((r) => r.text).join("\n"),
    offsetOf(i) {
      let lo = 0;
      let hi = starts.length - 1;
      while (lo < hi) {
        const mid = (lo + hi + 1) >> 1;
        if (starts[mid] <= i) lo = mid;
        else hi = mid - 1;
      }
      return runs.length ? runs[lo].at + Math.max(0, i - starts[lo]) : i;
    },
  };
}

function unescapeLayer(text) {
  if (!/\\["\\/u]/.test(text)) return null;
  return text
    .replace(/\\u([0-9a-fA-F]{4})/g, (_, h) =>
      String.fromCharCode(parseInt(h, 16)),
    )
    .replace(/\\(["\\/])/g, "$1");
}

function percentLayer(text) {
  if (!/%[0-9A-Fa-f]{2}/.test(text)) return null;
  return text.replace(/(?:%[0-9A-Fa-f]{2})+/g, (run) => {
    try {
      return decodeURIComponent(run);
    } catch {
      return run;
    }
  });
}

/** One normalization step: JSON-unescape, then percent-decode. Null when nothing changes. */
function normalizeStep(text) {
  const un = unescapeLayer(text) ?? text;
  const out = percentLayer(un) ?? un;
  if (out === text) return null;
  const how =
    un !== text && out !== un
      ? "unescaped+percent"
      : un !== text
        ? "unescaped"
        : "percent";
  return { text: out, how };
}

// Standard and URL-safe alphabets (Node decodes both under "base64").
// Sixteen encoded characters cover decodeLayer's 12-byte minimum. A higher
// candidate floor silently skipped short JSON pairs inside string values.
const BASE64 =
  /(?<![A-Za-z0-9+/=_-])([A-Za-z0-9+/_-]{16,}={0,2})(?![A-Za-z0-9+/=_-])/g;

function* base64Candidates(text) {
  BASE64.lastIndex = 0;
  for (let m = BASE64.exec(text); m; m = BASE64.exec(text)) {
    const before = text.slice(Math.max(0, m.index - 64), m.index);
    // Inline media is not evidence text (and is never exempted as decoded).
    if (/data:(?:image|font|audio|video)\/[^;,]*;base64,$/i.test(before))
      continue;
    yield { at: m.index, token: m[1] };
  }
}

function textual(bytes) {
  const decoded = bytes.toString("utf8");
  const printable = decoded.replace(/[^\x20-\x7e\t\r\n]/g, "").length;
  return decoded.length > 0 && printable / decoded.length >= 0.95;
}

function tighten(limits) {
  const out = { ...LIMITS };
  for (const [k, v] of Object.entries(limits ?? {}))
    if (k in LIMITS && Number.isInteger(v) && v >= 0 && v < LIMITS[k])
      out[k] = v;
  return out;
}

const join = (a, b) => (a ? `${a}!${b}` : b);
const rel = (root, p) => path.relative(root, p) || path.basename(p);
const sha256 = (buf) => createHash("sha256").update(buf).digest("hex");

class Audit {
  constructor({ registry, limits } = {}) {
    this.registry = (registry ?? []).filter(
      (v) => typeof v === "string" && v.length >= 6,
    );
    this.limits = tighten(limits);
    this.key = randomBytes(32);
    this.rows = new Map();
    this.problems = [];
    this.files = 0;
    this.bytes = 0;
    this.entries = 0;
    this.decodes = 0;
    this.capture = null;
    this.withheld = 0;
    this.real = new Map(); // file label -> { abs, rel, sha256 }
  }

  digest(value) {
    return createHmac("sha256", this.key)
      .update(value)
      .digest("hex")
      .slice(0, 12);
  }

  emit(file, entry, layer, hit) {
    if (this.capture) {
      this.capture.push({ layer, hit });
      return;
    }
    const digest = this.digest(hit.value);
    // Different field identities must survive deduplication even when they
    // carry the same value (including overlapping serialized-text readings).
    const id = `${file}\0${entry}\0${hit.kind}\0${hit.name}\0${digest}`;
    const row = this.rows.get(id);
    if (row) row.count++;
    else
      this.rows.set(id, {
        kind: hit.kind,
        file,
        entry,
        offset: hit.offset,
        layer,
        name: hit.name,
        digest,
        count: 1,
      });
  }

  problem(problem, file, entry) {
    if (this.capture) {
      this.capture.push({ problem });
      return;
    }
    this.problems.push({ problem, file, entry: entry ?? "" });
  }

  /** The findings a NAME (file path, archive entry name) carries, not yet recorded. */
  findingsIn(name, layer) {
    const saved = this.capture;
    const found = (this.capture = []);
    try {
      this.scanText("", "", name, { code: false }, layer, 0, 0);
    } finally {
      this.capture = saved;
    }
    return found;
  }

  record(file, entry, found) {
    for (const f of found)
      if (f.hit) this.emit(file, entry, f.layer, f.hit);
      else this.problem(f.problem, file, entry);
  }

  /** Layers of one text: raw and normalized variants, base64 decodes, detectors. */
  scanText(
    file,
    entry,
    text,
    opts,
    layer = "raw",
    depth = 0,
    archiveDepth = 0,
    map = null,
  ) {
    const variants = [{ text, layer }];
    // The raw bytes of a binary payload get the exact-shape pass only; its
    // text spans are scanned as text (with these layers) by scanBuffer.
    for (let t = text, n = 1; !opts.binary; n++) {
      const step = normalizeStep(t);
      if (!step) break;
      if (n > this.limits.normalize) {
        this.problem("normalize-limit", file, entry);
        break;
      }
      variants.push({
        text: step.text,
        layer: `${layer}>${step.how}${n > 1 ? n : ""}`,
      });
      t = step.text;
    }
    const inspected = new Set();
    if (!opts.binary && !opts.code) {
      const tokens = new Map();
      for (const v of variants)
        for (const c of base64Candidates(v.text))
          if (!tokens.has(c.token))
            tokens.set(c.token, `${v.layer}>base64@${c.at}`);
      for (const [token, at] of tokens) {
        if (++this.decodes > this.limits.base64Decodes) {
          this.problem("base64-decode-limit", file, entry);
          break;
        }
        if (this.decodeLayer(file, entry, token, at, depth, archiveDepth))
          inspected.add(token.replace(/=+$/, ""));
      }
    }
    for (const v of variants)
      for (const hit of detect(v.text, {
        ...opts,
        registry: this.registry,
        inspected,
      }))
        this.emit(
          file,
          entry,
          v.layer,
          map && v.text === text ? { ...hit, offset: map(hit.offset) } : hit,
        );
  }

  /** One base64 token. True when its decoded TEXT was scanned (the token is then exempt from entropy). */
  decodeLayer(file, entry, token, layer, depth, archiveDepth) {
    const bytes = Buffer.from(token, "base64");
    if (bytes.length < 12) return false;
    const archive = looksLikeZip(bytes) || looksLikeGzip(bytes);
    const text = !archive && textual(bytes);
    if (!archive && !text && bytes.length < MIN_BINARY_PAYLOAD) return false;
    if (depth + 1 > this.limits.base64Depth) {
      this.problem("base64-depth-limit", file, entry);
      return false;
    }
    if (text) {
      this.scanText(
        file,
        entry,
        bytes.toString("utf8"),
        { code: false },
        layer,
        depth + 1,
        archiveDepth,
      );
      return true;
    }
    this.scanBuffer(
      file,
      archive ? join(entry, `(${layer})`) : entry,
      bytes,
      archiveDepth,
      depth + 1,
      layer,
    );
    return false;
  }

  scanBuffer(file, entry, buf, archiveDepth = 0, depth = 0, layer = "raw") {
    this.bytes += buf.length;
    const zip = looksLikeZip(buf);
    if (zip || looksLikeGzip(buf)) {
      if (archiveDepth >= this.limits.archiveDepth) {
        this.problem("archive-depth-limit", file, entry);
        return;
      }
      if (zip) this.scanZip(file, entry, buf, archiveDepth, depth);
      else this.scanGzip(file, entry, buf, archiveDepth, depth);
      return;
    }
    const code = CODE.test(entry || file);
    if (isBinary(buf)) {
      // Exact shapes and minted values over every byte, then every detector
      // over the payload's printable text spans.
      this.scanText(
        file,
        entry,
        buf.toString("latin1"),
        { binary: true },
        layer,
        depth,
        archiveDepth,
      );
      const spans = textSpans(buf);
      if (spans.text)
        this.scanText(
          file,
          entry,
          spans.text,
          { code },
          `${layer}>spans`,
          depth,
          archiveDepth,
          spans.offsetOf,
        );
      return;
    }
    this.scanText(
      file,
      entry,
      buf.toString("utf8"),
      { code },
      layer,
      depth,
      archiveDepth,
    );
  }

  scanZip(file, entry, buf, archiveDepth, depth) {
    const z = zipEntries(buf);
    const labels = new Map();
    for (const { id, n, name } of z.names) {
      const found = this.findingsIn(name, "zip-name");
      const label = join(
        entry,
        found.length ? `[entry ${n}: name withheld]` : name,
      );
      labels.set(id, label);
      this.record(file, label, found);
    }
    const at = (id) => (id ? labels.get(id) ?? join(entry, `[${id}]`) : entry);
    for (const p of z.problems) this.problem(p.problem, file, at(p.id));
    for (const e of z.entries) {
      this.entries++;
      this.scanBuffer(file, at(e.id), e.data, archiveDepth + 1, depth);
    }
    for (const m of z.meta)
      this.scanBuffer(
        file,
        join(at(m.id), `(${m.kind}${m.at === undefined ? "" : `@${m.at}`})`),
        m.data,
        archiveDepth + 1,
        depth,
        `zip-${m.kind}`,
      );
  }

  scanGzip(file, entry, buf, archiveDepth, depth) {
    let meta;
    try {
      meta = gzipHeader(buf);
    } catch {
      this.problem("bad-gzip-header", file, entry);
      return;
    }
    for (const m of meta)
      this.scanBuffer(
        file,
        join(entry, `(${m.kind})`),
        m.data,
        archiveDepth + 1,
        depth,
        m.kind,
      );
    let inner;
    try {
      inner = gunzip(buf);
    } catch {
      this.problem("corrupt-gzip", file, entry);
      return;
    }
    this.scanBuffer(
      file,
      join(entry, "(gunzip)"),
      inner,
      archiveDepth + 1,
      depth,
    );
  }

  /** The label a path may carry in the report; withheld when the path itself has a finding. */
  fileLabel(root, p) {
    const relative = rel(root, p);
    const found = this.findingsIn(relative, "path");
    const label = found.length
      ? `[path ${++this.withheld}: withheld]`
      : relative;
    this.record(label, "", found);
    return { label, relative };
  }

  scanPath(p, root) {
    let st;
    try {
      st = lstatSync(p);
    } catch {
      const { label, relative } = this.fileLabel(root, p);
      this.real.set(label, { abs: p, rel: relative });
      this.problem("unreadable", label, "");
      return;
    }
    if (st.isDirectory()) {
      let children;
      try {
        children = readdirSync(p).sort();
      } catch {
        const { label, relative } = this.fileLabel(root, p);
        this.real.set(label, { abs: p, rel: relative });
        this.problem("unreadable", label, "");
        return;
      }
      for (const child of children) this.scanPath(path.join(p, child), root);
      return;
    }
    const { label, relative } = this.fileLabel(root, p);
    this.real.set(label, { abs: p, rel: relative });
    if (st.isSymbolicLink()) {
      this.problem("symlink-not-followed", label, "");
      return;
    }
    if (!st.isFile()) return;
    this.files++;
    if (st.size > MAX_FILE) {
      this.problem("too-large", label, "");
      return;
    }
    let buf;
    try {
      buf = readFileSync(p);
    } catch {
      this.problem("unreadable", label, "");
      return;
    }
    this.real.get(label).sha256 = sha256(buf);
    this.decodes = 0;
    this.scanBuffer(label, "", buf);
  }

  report(extra = {}) {
    const hits = [...this.rows.values()];
    const out = {
      auditor: AUDITOR,
      digestScope: "per-report keyed digest; not comparable across reports",
      scanned: {
        files: this.files,
        bytes: this.bytes,
        archiveEntries: this.entries,
      },
      hits,
      problems: this.problems,
      refused: hits.length > 0 || this.problems.length > 0,
      ...extra,
    };
    Object.defineProperty(out, REAL, { value: this.real, enumerable: false });
    return out;
  }
}

/**
 * Audit files and directories. `registry` (optional) is the run's own minted
 * values, matched exactly in every layer. Paths are reported relative to `root`.
 * `limits` may tighten LIMITS (tests).
 */
export function auditPaths(
  paths,
  { registry, root = process.cwd(), limits } = {},
) {
  const audit = new Audit({ registry, limits });
  for (const p of paths) audit.scanPath(path.resolve(p), path.resolve(root));
  return audit.report();
}

/** Audit one in-memory buffer (tests and the in-worker trace check). */
export function auditBuffer(buf, name, { registry, limits } = {}) {
  const audit = new Audit({ registry, limits });
  audit.files = 1;
  audit.scanBuffer(name, "", buf);
  return audit.report();
}

/** The audited files of a report (auditPaths): label -> { abs, rel, sha256 }. */
export function auditedFiles(report) {
  return report[REAL] ?? new Map();
}

/**
 * Move every file with a finding or problem under `dir` (outside the evidence
 * tree), keeping its path relative to `root`. Returns the LABELS moved and the
 * labels that could NOT be moved (a failed quarantine leaves the file where it
 * was: the caller must then keep it from landing by other means).
 */
export function quarantineAll(report, root, dir) {
  const real = auditedFiles(report);
  const moved = [];
  const failed = [];
  const labels = new Set(
    [...report.hits, ...report.problems].map((r) => r.file).filter(Boolean),
  );
  let outside = 0;
  for (const label of labels) {
    const known = real.get(label);
    const from = known?.abs ?? path.resolve(root, label);
    let relative = known?.rel ?? label;
    if (relative.startsWith("..") || path.isAbsolute(relative))
      relative = path.join(
        "outside-root",
        `${outside++}-${path.basename(from)}`,
      );
    const to = path.join(dir, relative);
    try {
      mkdirSync(path.dirname(to), { recursive: true, mode: 0o700 });
      try {
        renameSync(from, to);
      } catch (error) {
        if (error.code !== "EXDEV") throw error;
        copyFileSync(from, to);
        unlinkSync(from);
      }
      moved.push(label);
    } catch {
      failed.push(label);
    }
  }
  return { moved, failed };
}

/** r1 signature: the labels moved (see quarantineAll for the failures). */
export function quarantine(report, root, dir) {
  return quarantineAll(report, root, dir).moved;
}
