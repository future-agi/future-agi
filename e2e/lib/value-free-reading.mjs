// HR-JRN-01: the ONE reading of what value-free mode publishes, in plain Node with
// no dependencies, so every reader reads exactly the same layers: the reporter's scan
// (lib/value-free-publish.ts), the codec's sanitizer (lib/value-free-codec.ts), and the
// publication boundary's final check (lib/value-free-boundary.mjs), which the walk job runs
// right before its pull, where no TypeScript loader exists. Moved here unchanged from
// lib/value-free-zip.ts (the zip codec), lib/value-free.ts (the shortest value a run can
// register, colour stripping, the value shapes and kindOf) and lib/value-free-codec.ts (the
// layer helpers and the Scan); those modules re-export what their importers use.
import { deflateRawSync, inflateRawSync } from "node:zlib";
import {
  checkLog,
  checkRecord,
  entryLayer,
  UNKNOWN_KIND,
} from "./value-free-record.mjs";

/** The shortest value that can be substituted safely; a shorter one refuses the run (lib/value-free.ts). So no registered
 * value is shorter, and a name shorter than this cannot hold one: the walk export directory's name. */
export const VALUE_FREE_MIN_LENGTH = 6;

// The zip codec (lib/value-free-zip.ts): the blob report, the HTML report's embedded data and any
// zip inside them. Entries are read by the central directory (yazl writes data descriptors);
// zip64, encryption and any method but stored or deflate are refused (null), never guessed.
// Writing is deterministic (fixed time, fixed level), so a second pass over its own output
// changes nothing.
const LOCAL = 0x04034b50;
const CENTRAL = 0x02014b50;
const END = 0x06054b50;
const CRC = Array.from({ length: 256 }, (_, n) => {
  let c = n;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});
function crc32(data) {
  let c = 0xffffffff;
  for (const byte of data) c = CRC[(c ^ byte) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}
export const isZip = (buf) =>
  buf.length >= 22 && [LOCAL, END].includes(buf.readUInt32LE(0));

/** Entries by the central directory (yazl writes data descriptors), or null for what this cannot read. */
export function readZip(buf) {
  let end = -1;
  for (
    let i = buf.length - 22;
    i >= Math.max(0, buf.length - 22 - 0xffff);
    i--
  ) {
    if (buf.readUInt32LE(i) === END) {
      end = i;
      break;
    }
  }
  if (end < 0) return null;
  const count = buf.readUInt16LE(end + 10);
  let at = buf.readUInt32LE(end + 16);
  if (count === 0xffff || at === 0xffffffff) return null; // zip64
  const entries = [];
  for (let n = 0; n < count; n++) {
    if (at + 46 > buf.length || buf.readUInt32LE(at) !== CENTRAL) return null;
    const [flags, method, crc] = [
      buf.readUInt16LE(at + 8),
      buf.readUInt16LE(at + 10),
      buf.readUInt32LE(at + 16),
    ];
    const [packed, size, local] = [
      buf.readUInt32LE(at + 20),
      buf.readUInt32LE(at + 24),
      buf.readUInt32LE(at + 42),
    ];
    const nameLength = buf.readUInt16LE(at + 28);
    const name = buf.subarray(at + 46, at + 46 + nameLength).toString("utf8");
    at +=
      46 + nameLength + buf.readUInt16LE(at + 30) + buf.readUInt16LE(at + 32);
    if (
      flags & 1 ||
      local + 30 > buf.length ||
      buf.readUInt32LE(local) !== LOCAL
    )
      return null;
    const start =
      local + 30 + buf.readUInt16LE(local + 26) + buf.readUInt16LE(local + 28);
    const body = buf.subarray(start, start + packed);
    if (body.length !== packed || (method !== 0 && method !== 8)) return null;
    let data;
    try {
      data = method === 8 ? inflateRawSync(body) : Buffer.from(body);
    } catch {
      return null;
    }
    if (data.length !== size || crc32(data) !== crc) return null;
    entries.push({ name, data });
  }
  return entries;
}

/** Deterministic (fixed time, fixed level), so a second pass over its own output changes nothing. */
export function writeZip(entries) {
  const parts = [];
  const central = [];
  let offset = 0;
  for (const { name, data } of entries) {
    const nameBytes = Buffer.from(name, "utf8");
    const deflated = deflateRawSync(data, { level: 6 });
    const method = deflated.length < data.length ? 8 : 0;
    const body = method === 8 ? deflated : data;
    const crc = crc32(data);
    const local = Buffer.alloc(30);
    local.writeUInt32LE(LOCAL, 0);
    local.writeUInt16LE(20, 4);
    local.writeUInt16LE(0x0800, 6);
    local.writeUInt16LE(method, 8);
    local.writeUInt16LE(0x21, 12);
    local.writeUInt32LE(crc, 14);
    local.writeUInt32LE(body.length, 18);
    local.writeUInt32LE(data.length, 22);
    local.writeUInt16LE(nameBytes.length, 26);
    const entry = Buffer.alloc(46);
    entry.writeUInt32LE(CENTRAL, 0);
    entry.writeUInt16LE(20, 4);
    entry.writeUInt16LE(20, 6);
    entry.writeUInt16LE(0x0800, 8);
    entry.writeUInt16LE(method, 10);
    entry.writeUInt16LE(0x21, 14);
    entry.writeUInt32LE(crc, 16);
    entry.writeUInt32LE(body.length, 20);
    entry.writeUInt32LE(data.length, 24);
    entry.writeUInt16LE(nameBytes.length, 28);
    entry.writeUInt32LE(offset, 42);
    parts.push(local, nameBytes, body);
    central.push(entry, nameBytes);
    offset += 30 + nameBytes.length + body.length;
  }
  const directory = Buffer.concat(central);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(END, 0);
  end.writeUInt16LE(entries.length, 8);
  end.writeUInt16LE(entries.length, 10);
  end.writeUInt32LE(directory.length, 12);
  end.writeUInt32LE(offset, 16);
  return Buffer.concat([...parts, directory, end]);
}

const UTF8 = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true });
export const asText = (buf) => {
  if (buf.includes(0)) return null;
  try {
    return UTF8.decode(buf);
  } catch {
    return null;
  }
};

// Whole shapes go first, so a value gets the same token whatever part of it is registered
// (the prefix inside an email); registered values next, longest first; the fixture prefix
// last, after the email local parts that contain it. A token matches none of them, so
// scrubbing is idempotent (lib/value-free.ts).
export const SHAPES = [
  ["jwt", /\beyJ[\w-]+\.[\w-]+\.[\w-]+/g],
  ["email", /[\w.%+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}/g],
  [
    "id",
    /\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b/g,
  ],
  // OrgApiKey api_key and secret_key (accounts/models/user.py: uuid4().hex).
  ["key", /\b[0-9a-fA-F]{32}\b/g],
];
// The ScopeActors fixture prefix, which names its emails, workspaces and orgs.
export const RUN = ["run", /\be2e-h1-\d+-[0-9a-z]+/g];
const WHOLE = [...SHAPES, RUN].map(([kind, re]) => [
  kind,
  new RegExp(`^(?:${re.source})$`),
]);
/** What a token says its value was. */
export const kindOf = (raw) =>
  WHOLE.find(([, re]) => re.test(raw))?.[0] ?? "value";

// ECMA-48: a CSI with any parameter bytes (colon-form colours such as ESC[38:2:255:0:0m too),
// a string control (OSC hyperlinks, DCS, SOS, PM, APC) up to its terminator, then any other
// escape. A diff or a highlighted log line splits an email or a UUID into pieces no pattern
// recognises, so every match runs on the text without them.
const ANSI =
  /(?:\u001B\[|\u009B)[0-?]*[ -/]*[@-~]|(?:\u001B[\]PX^_]|[\u0090\u0098\u009D-\u009F])[^\u0007\u001B\u009C]*(?:\u0007|\u001B\\|\u009C)|\u001B[ -/]*[0-~]/g;
// The same sequences as JSON writes them (\u001b[...m), only where the backslash is not itself
// escaped (a literal "\\u001b[" stays valid JSON); never a quote or a backslash inside one.
const ANSI_JSON = new RegExp(
  String.raw`(?<=(?:^|[^\\])(?:\\\\)*)(?:` +
    [
      String.raw`(?:\\u001[bB]\[|\\u009[bB])[0-?]*[ !#-/]*[@-[\]-~]`,
      String.raw`(?:\\u001[bB][\]PX^_]|\\u009[08dDeEfF])(?:[^"\\]|\\(?!u0007|u001[bB]|u009[cC])(?:u[0-9a-fA-F]{4}|[^u]))*?` +
        String.raw`(?:\\u0007|\\u001[bB]\\\\|\\u009[cC])`,
      String.raw`\\u001[bB][ !#-/]*[0-[\]-~]`,
    ].join("|") +
    ")",
  "g",
);
export const stripAnsi = (text) =>
  text.replace(ANSI, "").replace(ANSI_JSON, "");
/** S2: the runner log's closed schema (value-free-record.mjs checkLog), read as written and with colours removed. */
export const logProblem = (text) => checkLog(text) ?? checkLog(stripAnsi(text));

// The layers (lib/value-free-codec.ts): a zip (the blob report, a zip inside a field): every
// entry, its name too; an embedded zip data: URL (the HTML report's index.html); in JSON or
// JSONL, the base64 fields (blob `base64` and stdio `data`, json `buffer` anywhere, the json
// report's attachment `body`) and every string or object key that is itself a JSON document.
// Each decode is one layer; deeper than NESTING_BOUND is a problem.

/** Layers of decoding read through (a zip's entries, an embedded zip URL, a base64 field, a JSON document in a string). The
 * deepest Playwright report layer the harness publishes is 5 (html: index.html, its zip, an entry, a JSON attachment body,
 * a base64 field inside it); one more layer than this refuses the publication. */
export const NESTING_BOUND = 8;
export const EMBEDDED_ZIP = /data:application\/zip;base64,([A-Za-z0-9+/=]*)/g;
export const looksJson = (s) => /^\s*[[{]/.test(s);
export const parseJson = (s) => {
  try {
    return JSON.parse(s);
  } catch {
    return undefined;
  }
};
export const isDoc = (v) => v !== null && typeof v === "object";
/** The json reporter's report (config, suites): the one whose attachment `body` is base64. */
export const isJsonReport = (doc) =>
  isDoc(doc) && "suites" in doc && "config" in doc;
export const isEncodedField = (key, o, reportBodies) =>
  key === "base64" ||
  key === "buffer" ||
  (key === "data" && o.isBase64 === true) ||
  (key === "body" && reportBodies);
/** Base64 by its alphabet (standard or url-safe, whitespace allowed), decoded; null for a string that is not, which is plain
 * text and read as it is. */
export function strictBase64(s) {
  const t = s.replace(/\s+/g, "");
  return t.length % 4 !== 1 && /^[A-Za-z0-9+/_-]*={0,2}$/.test(t)
    ? Buffer.from(t, "base64")
    : null;
}

/** The scan: every registered value (and its JSON-escaped form), colours removed or not, through the same layers. */
export class Scan {
  constructor(values) {
    this.forms = values.filter(Boolean).flatMap((v) => {
      const escaped = JSON.stringify(v).slice(1, -1);
      return escaped === v
        ? [[v, v]]
        : [
            [v, v],
            [v, escaped],
          ];
    });
    this.findings = new Map();
  }

  add(f) {
    this.findings.set(JSON.stringify(f), f);
  }

  /** A text read as it is (a path component, a name), no layer decoded. */
  check(text, place) {
    for (const view of new Set([text, stripAnsi(text)])) {
      for (const [value, form] of this.forms) {
        if (view.includes(form))
          this.add({
            reason: "scan-hit",
            kind: kindOf(value),
            length: value.length,
            place,
          });
      }
    }
  }

  /** A file's or a text's bytes, every layer decoded. A binary that is not a zip is a problem: a publication holds none.
   * S2: `layer` is what the file is as a published record (lib/value-free-record.mjs
   * recordLayer; null for a payload): a record of no known kind, a record zip or document that is not one, or a record that
   * holds a field or a kind outside its closed schema is a problem, and the record is read no further. */
  bytes(buf, depth = 0, place = "content", layer = null) {
    if (depth > NESTING_BOUND) {
      this.add({ reason: "nesting-bound", place, depth });
      return;
    }
    if (layer?.unknown) {
      this.add({ reason: UNKNOWN_KIND, place, depth });
      return;
    }
    this.check(buf.toString("utf8"), place);
    if (isZip(buf)) {
      if (layer && !layer.zip) {
        this.add({ reason: UNKNOWN_KIND, place, depth });
        return;
      }
      const entries = readZip(buf);
      if (!entries) {
        this.add({ reason: "unreadable-zip", place, depth });
        return;
      }
      for (const e of entries) {
        this.check(e.name, "zip-entry-name");
        const inner = layer?.zip ? entryLayer(layer.zip, e.name) : null;
        this.bytes(e.data, depth + 1, "zip-entry", inner);
      }
      return;
    }
    if (layer?.zip) {
      this.add({ reason: UNKNOWN_KIND, place, depth });
      return;
    }
    const text = asText(buf);
    if (text === null) {
      this.add({ reason: "unsupported-encoding", place, depth });
      return;
    }
    // S2: the runner log holds lines of text only; a document in it (or an embedded zip) refuses, and is read no further.
    if (layer?.log) {
      const problem = logProblem(text);
      if (problem !== null) this.add({ reason: problem, place, depth });
      return;
    }
    for (const m of text.matchAll(EMBEDDED_ZIP)) {
      if (!m[1]) continue;
      const zip = Buffer.from(m[1], "base64");
      if (!isZip(zip))
        this.add({
          reason: "unreadable-zip",
          place: "embedded-zip",
          depth: depth + 1,
        });
      else
        this.bytes(
          zip,
          depth + 1,
          "embedded-zip",
          layer?.embedded ? { zip: layer.embedded } : null,
        );
    }
    if (layer?.doc || layer?.lines) {
      this.records(text, depth, place, layer);
      return;
    }
    if (!looksJson(text)) return;
    const whole = parseJson(text);
    const docs =
      whole !== undefined
        ? [whole]
        : text
            .split("\n")
            .filter(looksJson)
            .map(parseJson)
            .filter((d) => d !== undefined);
    for (const doc of docs) this.json(doc, depth, isJsonReport(doc), place);
  }

  /** S2: a record file's text: one document of its type (`layer.doc`), or one per line (`layer.lines`). Anything
   * that is not one is a record of no known kind. */
  records(text, depth, place, layer) {
    const docs = layer.doc
      ? [parseJson(text)]
      : text
          .split("\n")
          .filter((line) => line !== "")
          .map(parseJson);
    for (const doc of docs) {
      if (doc === undefined) this.add({ reason: UNKNOWN_KIND, place, depth });
      else
        this.json(
          doc,
          depth,
          isJsonReport(doc),
          place,
          layer.doc ?? layer.lines,
        );
    }
  }

  json(v, depth, reportBodies, place, record = null) {
    // S2: a record holding a field or a kind outside its closed schema refuses, and is never traversed.
    if (record !== null) {
      const problem = checkRecord(v, record);
      if (problem !== null) {
        this.add({ reason: problem, place, depth });
        return;
      }
    }
    if (typeof v === "string") {
      this.string(v, depth, place);
      return;
    }
    if (Array.isArray(v)) {
      for (const x of v) this.json(x, depth, reportBodies, place);
      return;
    }
    if (!isDoc(v)) return;
    for (const [key, x] of Object.entries(v)) {
      this.string(key, depth, place); // a key is read like a string value: a JSON document in it a layer down
      const bytes =
        typeof x === "string" && isEncodedField(key, v, reportBodies)
          ? strictBase64(x)
          : null;
      if (bytes) {
        this.check(x, place);
        this.bytes(bytes, depth + 1, "base64-field");
      } else this.json(x, depth, reportBodies, place);
    }
  }

  string(x, depth, place) {
    this.check(x, place);
    const doc = looksJson(x) ? parseJson(x) : undefined;
    if (!isDoc(doc)) return;
    if (depth + 1 > NESTING_BOUND) {
      this.add({
        reason: "nesting-bound",
        place: "nested-json",
        depth: depth + 1,
      });
      return;
    }
    this.json(doc, depth + 1, false, "nested-json");
  }
}
