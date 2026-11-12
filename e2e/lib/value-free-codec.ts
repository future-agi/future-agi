// HR-JRN-01: ONE recursive reading of everything value-free mode publishes, used
// both to sanitize (lib/value-free-publish.ts builds the publication with it, and
// lib/value-free-scrub.ts rewrites the files that stay on the machine) and to scan it, so
// the scan reads exactly the layers the sanitizer wrote and no fewer. From a payload's
// original bytes, never through a lossy text round trip, it decodes:
//   - a zip (the blob report, a zip inside a field): every entry, its name too;
//   - an embedded zip data: URL (the HTML report's index.html);
//   - in JSON or JSONL, the base64 fields (blob `base64` and stdio `data`, json `buffer`
//     anywhere, the json report's attachment `body`) and every string that is itself a
//     JSON document (an attachment body the HTML report keeps as text);
// and every JSON string and key is sanitized and checked on its own, so a value escaped
// twice inside a nested document is still whole. An object key is read exactly
// like a string value, so a key that is itself a JSON document is decoded a layer down
// too (one that is not is cleaned as before). Each decode is one layer. Deeper than
// NESTING_BOUND layers, a payload in an encoding this does not decode (a binary that is
// not a zip inside a field, unless its contentType declares it binary, when it is
// withheld), or a zip it cannot read is a Problem: the publication is refused, and the
// on-disk pass deletes the file. A binary file or zip entry is left out (never published);
// the scan treats one in a publication as a problem too.
// The Scan, the layer helpers both halves use and the zip codec live in
// lib/value-free-reading.mjs (plain Node), so the publication boundary's final check
// (lib/value-free-boundary.mjs) reads the same layers too; this module re-exports them.
// S2: what the builder publishes as a report RECORD (its
// `layer`, lib/value-free-record.mjs recordLayer) is first projected to that record type's
// closed schema: an unknown field, a kind its field does not admit (an arbitrary nested
// object included), a record file of no known kind or a record that is not one refuses it,
// and nothing of it is preserved or traversed. A payload inside a record (an attachment's
// body) and every file that is not a record stay what they were: sanitized through every layer.
// S2: runner.log is a record too (its layer `log`): lines of text only. A document in it (a
// JSON object or array from the start of a line to the end of one, or an embedded zip: lib/value-free-record.mjs checkLog)
// refuses it before anything of it is traversed; lines of text are sanitized as before.
import { isTextContent, type ValueFree } from './value-free';
import { asText, EMBEDDED_ZIP, isDoc, isEncodedField, isJsonReport, isZip, logProblem, looksJson, NESTING_BOUND, parseJson,
  readZip, strictBase64, writeZip, type Place, type Problem, type ZipEntry } from './value-free-reading.mjs';
import { entryLayer, projectRecord, UNKNOWN_KIND, type RecordLayer } from './value-free-record.mjs';

export { NESTING_BOUND, Scan, strictBase64 } from './value-free-reading.mjs';
export type { Finding, Place, Problem, ProblemCode } from './value-free-reading.mjs';
export type { RecordLayer } from './value-free-record.mjs';
export type Sanitized = { bytes: Buffer } | { binary: true } | Problem;

const isProblem = (r: unknown): r is Problem => Boolean(r && typeof r === 'object' && 'problem' in r);
const withheld = (contentType: string): string => Buffer.from(`withheld in value-free mode: ${contentType}`).toString('base64');

type Clean = (text: string) => string;

/** The sanitized bytes of a file, a zip entry or a decoded field; `binary` for one that is neither text nor a zip. `layer`:
 * what the file is published as when it is a report record, null for anything else. */
export function sanitizeBytes(buf: Buffer, vf: ValueFree, layer: RecordLayer | null = null): Sanitized {
  return bytesOf(buf, vf.frozen(), 0, 'content', layer);
}
const unknownKind = (place: Place, depth: number): Problem => ({ problem: UNKNOWN_KIND, place, depth });

/** A text (a report, the runner's output) with every layer inside it sanitized, or the problem that refuses it. `layer`: what
 * it is published as when it is a record (R2: the runner's output is the runner log, RUNNER_LOG), null for anything else. */
export function sanitizeText(text: string, vf: ValueFree, layer: RecordLayer | null = null): string | Problem {
  return textOf(text, vf.frozen(), 0, 'content', layer);
}

function bytesOf(buf: Buffer, clean: Clean, depth: number, place: Place, layer: RecordLayer | null = null): Sanitized {
  if (depth > NESTING_BOUND) return { problem: 'nesting-bound', place, depth };
  if (layer?.unknown) return unknownKind(place, depth);
  if (isZip(buf)) {
    if (layer && !layer.zip) return unknownKind(place, depth);
    const entries = readZip(buf);
    if (!entries) return { problem: 'unreadable-zip', place, depth };
    const out: ZipEntry[] = [];
    for (const e of entries) {
      const r = bytesOf(e.data, clean, depth + 1, 'zip-entry', layer?.zip ? entryLayer(layer.zip, e.name) : null);
      if (isProblem(r)) return r;
      if ('bytes' in r) out.push({ name: clean(e.name), data: r.bytes }); // a binary entry is left out
    }
    return { bytes: writeZip(out) };
  }
  if (layer?.zip) return unknownKind(place, depth);
  const text = asText(buf);
  if (text === null) return layer ? unknownKind(place, depth) : { binary: true };
  const r = textOf(text, clean, depth, place, layer);
  return isProblem(r) ? r : { bytes: Buffer.from(r, 'utf8') };
}

function textOf(text: string, clean: Clean, depth: number, place: Place = 'content', layer: RecordLayer | null = null):
  string | Problem {
  if (layer?.log) {
    // S2: the runner log, before anything in it is read as a layer: lines of text, then sanitized as any text is.
    if (logProblem(text) !== null) return unknownKind(place, depth);
    layer = null;
  }
  const json = layer?.doc || layer?.lines ? recordTextOf(text, clean, depth, place, layer) : jsonTextOf(text, clean, depth);
  if (isProblem(json)) return json;
  let out = '';
  let last = 0;
  for (const m of json.matchAll(EMBEDDED_ZIP)) {
    if (!m[1]) continue; // the prefix alone (a report's own code): nothing encoded
    const zip = Buffer.from(m[1], 'base64');
    if (!isZip(zip)) return { problem: 'unreadable-zip', place: 'embedded-zip', depth: depth + 1 };
    const r = bytesOf(zip, clean, depth + 1, 'embedded-zip', layer?.embedded ? { zip: layer.embedded } : null);
    if (isProblem(r)) return r;
    if (!('bytes' in r)) return { problem: 'unreadable-zip', place: 'embedded-zip', depth: depth + 1 };
    out += `${clean(json.slice(last, m.index))}data:application/zip;base64,${r.bytes.toString('base64')}`;
    last = m.index! + m[0].length;
  }
  return out + clean(json.slice(last));
}

/** A JSON file, or each line of a JSONL file (the blob's report.jsonl), re-serialized only where something changed. */
function jsonTextOf(text: string, clean: Clean, depth: number): string | Problem {
  if (!looksJson(text)) return text;
  const one = (source: string, indent?: number): string | Problem => {
    const doc = parseJson(source);
    if (doc === undefined) return source;
    const r = jsonOf(doc, clean, depth, isJsonReport(doc));
    if (isProblem(r)) return r;
    return JSON.stringify(r.value) === JSON.stringify(doc) ? source : JSON.stringify(r.value, null, indent);
  };
  if (parseJson(text) !== undefined) return one(text, text.includes('\n  "') ? 2 : undefined);
  const lines = text.split('\n');
  for (let i = 0; i < lines.length; i++) {
    if (!looksJson(lines[i])) continue;
    const r = one(lines[i]);
    if (isProblem(r)) return r;
    lines[i] = r;
  }
  return lines.join('\n');
}

/** S2: a report record's text: one document of its type (`layer.doc`), or one per line (`layer.lines`), each
 * projected to its closed schema and then sanitized; anything that is not one record is a record of no known kind. */
function recordTextOf(text: string, clean: Clean, depth: number, place: Place, layer: RecordLayer): string | Problem {
  const type = (layer.doc ?? layer.lines)!;
  const one = (source: string, indent?: number): string | Problem => {
    const doc = parseJson(source);
    if (doc === undefined) return unknownKind(place, depth);
    const r = jsonOf(doc, clean, depth, isJsonReport(doc), { type, place });
    return isProblem(r) ? r : JSON.stringify(r.value, null, indent);
  };
  if (layer.doc) {
    const r = one(text, text.includes('\n  "') ? 2 : undefined);
    return isProblem(r) ? r : `${r}${text.endsWith('\n') ? '\n' : ''}`;
  }
  const lines = text.split('\n');
  for (let i = 0; i < lines.length; i++) {
    if (lines[i] === '') continue;
    const r = one(lines[i]);
    if (isProblem(r)) return r;
    lines[i] = r;
  }
  return lines.join('\n');
}

/** `record` (S2): `v` is a report record of that type. It is projected to the type's closed schema FIRST: an
 * unknown field or kind refuses it here, before any of it is preserved or traversed; what remains is sanitized below. */
function jsonOf(v: unknown, clean: Clean, depth: number, reportBodies: boolean, record?: { type: string; place: Place }):
  { value: unknown } | Problem {
  if (record) {
    const projected = projectRecord(v, record.type);
    if (projected.problem !== undefined) return { problem: projected.problem, place: record.place, depth };
    v = projected.value;
  }
  if (typeof v === 'string') return stringOf(v, clean, depth);
  if (Array.isArray(v)) {
    const out: unknown[] = [];
    for (const x of v) {
      const r = jsonOf(x, clean, depth, reportBodies);
      if (isProblem(r)) return r;
      out.push(r.value);
    }
    return { value: out };
  }
  if (!isDoc(v)) return { value: v };
  const o = v as Record<string, unknown>;
  const contentType = typeof o.contentType === 'string' ? o.contentType : undefined;
  const out: Record<string, unknown> = {};
  let withholdType = false;
  for (const [key, x] of Object.entries(o)) {
    let r: { value: unknown } | Problem;
    if (typeof x === 'string' && isEncodedField(key, o, reportBodies)) {
      // An attachment body its contentType declares binary: withheld, never decoded (as before).
      if (contentType !== undefined && (key === 'base64' || key === 'body') && !isTextContent(contentType)) {
        r = { value: withheld(contentType) };
        withholdType = true;
      } else r = encodedOf(x, clean, depth);
    } else r = jsonOf(x, clean, depth, reportBodies);
    if (isProblem(r)) return r;
    // The key as a string value: one that is a JSON document is sanitized as one, a layer down.
    const k = stringOf(key, clean, depth);
    if (isProblem(k)) return k;
    out[k.value as string] = r.value;
  }
  if (withholdType) out.contentType = 'text/plain';
  return { value: out };
}

/** A base64 field: decoded, sanitized as bytes one layer down, re-encoded only when it changed. A binary that is not a zip
 * is an encoding this does not decode. */
function encodedOf(x: string, clean: Clean, depth: number): { value: unknown } | Problem {
  const bytes = strictBase64(x);
  if (!bytes) return stringOf(x, clean, depth);
  const r = bytesOf(bytes, clean, depth + 1, 'base64-field');
  if (isProblem(r)) return r;
  if (!('bytes' in r)) return { problem: 'unsupported-encoding', place: 'base64-field', depth: depth + 1 };
  return { value: r.bytes.equals(bytes) ? x : r.bytes.toString('base64') };
}

/** A string: one that is a JSON document is sanitized as one, a layer down; any other through the registry. */
function stringOf(x: string, clean: Clean, depth: number): { value: unknown } | Problem {
  const doc = looksJson(x) ? parseJson(x) : undefined;
  if (!isDoc(doc)) return { value: clean(x) };
  if (depth + 1 > NESTING_BOUND) return { problem: 'nesting-bound', place: 'nested-json', depth: depth + 1 };
  const r = jsonOf(doc, clean, depth + 1, false);
  if (isProblem(r)) return r;
  return { value: clean(JSON.stringify(r.value) === JSON.stringify(doc) ? x : JSON.stringify(r.value)) };
}
