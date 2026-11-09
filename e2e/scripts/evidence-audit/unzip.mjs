// The evidence auditor's OWN archive reader (708 add.78). It deliberately shares
// no code with the trace scrubber (e2e/lib/trace-hygiene/): a reader bug that
// hid an entry from the scrubber must not hide it from the auditor as well.
// It reads every entry the central directory lists AND any local header the
// directory does not list, and reports what it cannot read as a problem, never
// as "clean" (the caller turns problems into refusals: fail closed).
// Everything else an archive carries is handed back as METADATA for the
// caller to inspect: entry names (central and local, when they differ),
// entry comments, central and local extra fields, the archive comment, and
// every byte no structure claims (before, between and after the records).
// Names are returned with an id; problems and metadata refer to that id, so
// the caller decides how (or whether) a name may appear in a report.
import { gunzipSync, inflateRawSync } from "node:zlib";

const LOCAL = 0x04034b50;
const CENTRAL = 0x02014b50;
const END = 0x06054b50;
const DESCRIPTOR = 0x08074b50;
const LOCAL_SIG = Buffer.from([0x50, 0x4b, 0x03, 0x04]);

/** True when the buffer starts like a ZIP archive (or is an empty one). */
export function looksLikeZip(buf) {
  if (buf.length < 22) return false;
  const sig = buf.readUInt32LE(0);
  return sig === LOCAL || (sig === END && buf.length === 22);
}

/** True when the buffer is a gzip stream. */
export function looksLikeGzip(buf) {
  return buf.length > 18 && buf[0] === 0x1f && buf[1] === 0x8b;
}

export function gunzip(buf) {
  return gunzipSync(buf);
}

/**
 * The optional header fields of a gzip member (RFC 1952: FEXTRA, FNAME,
 * FCOMMENT) as metadata; throws on a header it cannot walk.
 */
export function gzipHeader(buf) {
  const flags = buf[3];
  const meta = [];
  let p = 10;
  const need = (n) => {
    if (p + n > buf.length) throw new Error("truncated gzip header");
  };
  if (flags & 0x04) {
    need(2);
    const length = buf.readUInt16LE(p);
    need(2 + length);
    meta.push({
      kind: "gzip-extra",
      data: buf.subarray(p + 2, p + 2 + length),
    });
    p += 2 + length;
  }
  for (const [bit, kind] of [
    [0x08, "gzip-name"],
    [0x10, "gzip-comment"],
  ]) {
    if (!(flags & bit)) continue;
    const zero = buf.indexOf(0, p);
    if (zero < 0) throw new Error("unterminated gzip header field");
    meta.push({ kind, data: buf.subarray(p, zero) });
    p = zero + 1;
  }
  return meta;
}

function findEnd(buf) {
  const floor = Math.max(0, buf.length - 22 - 0xffff);
  for (let i = buf.length - 22; i >= floor; i--) {
    if (buf.readUInt32LE(i) === END) return i;
  }
  return -1;
}

/**
 * One entry's local record: its data (inflated), the byte range the record
 * claims (header, name, extra, data, data descriptor) and its local metadata.
 */
function readLocal(buf, offset, entry, out) {
  const { id, method, compressedSize, centralName, centralFlags } = entry;
  if (offset + 30 > buf.length || buf.readUInt32LE(offset) !== LOCAL) {
    out.problems.push({ problem: "bad-local-header", id });
    return null;
  }
  const flags = buf.readUInt16LE(offset + 6);
  const nameLength = buf.readUInt16LE(offset + 26);
  const extraLength = buf.readUInt16LE(offset + 28);
  const nameEnd = offset + 30 + nameLength;
  const start = nameEnd + extraLength;
  if (start > buf.length) {
    out.problems.push({ problem: "truncated-entry", id });
    return null;
  }
  const localName = buf.subarray(offset + 30, nameEnd);
  if (centralName && !localName.equals(centralName))
    out.meta.push({ id, kind: "local-name", data: localName });
  if (extraLength)
    out.meta.push({
      id,
      kind: "local-extra",
      data: buf.subarray(nameEnd, start),
    });
  const end = start + compressedSize;
  if (end > buf.length) {
    out.problems.push({ problem: "truncated-entry", id });
    return null;
  }
  let claimEnd = end;
  if ((flags | (centralFlags ?? 0)) & 0x8) {
    const signed =
      end + 4 <= buf.length && buf.readUInt32LE(end) === DESCRIPTOR;
    claimEnd = Math.min(buf.length, end + (signed ? 16 : 12));
  }
  out.claimed.push([offset, claimEnd]);
  out.spans.push([start, end]);
  if (flags & 0x1) {
    out.problems.push({ problem: "encrypted-entry", id });
    return null;
  }
  const raw = buf.subarray(start, end);
  if (method === 0) return Buffer.from(raw);
  if (method === 8) {
    try {
      return inflateRawSync(raw);
    } catch {
      out.problems.push({ problem: "corrupt-entry", id });
      return null;
    }
  }
  out.problems.push({ problem: "unsupported-method", id });
  return null;
}

/**
 * Every entry of a ZIP buffer, plus the problems met and the metadata found:
 *   names     [{ id, n, name }]   every entry name (n: its position)
 *   entries   [{ id, data }]
 *   problems  [{ problem, id? }]  what could not be read (never dropped)
 *   meta      [{ id?, kind, data }]  comments, extra fields, a local name that
 *             differs from the central one, and unclaimed byte ranges
 */
export function zipEntries(buf) {
  const out = {
    names: [],
    entries: [],
    problems: [],
    meta: [],
    claimed: [],
    spans: [],
  };
  const seen = new Set();
  const end = findEnd(buf);
  if (end < 0) {
    out.problems.push({ problem: "no-end-of-central-directory" });
  } else {
    const commentLength = buf.readUInt16LE(end + 20);
    const eocdEnd = Math.min(buf.length, end + 22 + commentLength);
    out.claimed.push([end, eocdEnd]);
    if (end + 22 + commentLength > buf.length)
      out.problems.push({ problem: "truncated-archive-comment" });
    if (commentLength)
      out.meta.push({
        kind: "archive-comment",
        data: buf.subarray(end + 22, eocdEnd),
      });
    const count = buf.readUInt16LE(end + 10);
    const dirOffset = buf.readUInt32LE(end + 16);
    if (count === 0xffff || dirOffset === 0xffffffff) {
      out.problems.push({ problem: "zip64-not-read" });
    } else {
      let p = dirOffset;
      for (let n = 0; n < count; n++) {
        if (p + 46 > buf.length || buf.readUInt32LE(p) !== CENTRAL) {
          out.problems.push({ problem: "bad-central-directory" });
          break;
        }
        const nameLength = buf.readUInt16LE(p + 28);
        const extraLength = buf.readUInt16LE(p + 30);
        const commentLength = buf.readUInt16LE(p + 32);
        const nameEnd = p + 46 + nameLength;
        const recordEnd = nameEnd + extraLength + commentLength;
        if (recordEnd > buf.length) {
          out.problems.push({ problem: "bad-central-directory" });
          break;
        }
        const id = `c${n}`;
        const centralName = buf.subarray(p + 46, nameEnd);
        out.names.push({ id, n, name: centralName.toString("utf8") });
        if (extraLength)
          out.meta.push({
            id,
            kind: "extra",
            data: buf.subarray(nameEnd, nameEnd + extraLength),
          });
        if (commentLength)
          out.meta.push({
            id,
            kind: "comment",
            data: buf.subarray(nameEnd + extraLength, recordEnd),
          });
        const local = buf.readUInt32LE(p + 42);
        seen.add(local);
        const data = readLocal(
          buf,
          local,
          {
            id,
            method: buf.readUInt16LE(p + 10),
            compressedSize: buf.readUInt32LE(p + 20),
            centralName,
            centralFlags: buf.readUInt16LE(p + 8),
          },
          out,
        );
        if (data) out.entries.push({ id, data });
        p = recordEnd;
      }
      out.claimed.push([dirOffset, p]);
    }
  }
  // Local headers the central directory does not list: read them when their
  // sizes are known, otherwise report them (an unlisted entry is not "clean").
  let unlisted = 0;
  for (
    let i = buf.indexOf(LOCAL_SIG);
    i >= 0;
    i = buf.indexOf(LOCAL_SIG, i + 4)
  ) {
    if (seen.has(i) || i + 30 > buf.length) continue;
    // Inside a listed entry's bytes: a stored nested archive, read when the
    // caller recurses into that entry, not an entry of this archive.
    if (out.spans.some(([a, b]) => i >= a && i < b)) continue;
    const flags = buf.readUInt16LE(i + 6);
    const method = buf.readUInt16LE(i + 8);
    const compressedSize = buf.readUInt32LE(i + 18);
    const nameLength = buf.readUInt16LE(i + 26);
    if (nameLength === 0 || i + 30 + nameLength > buf.length) continue;
    const name = buf.toString("utf8", i + 30, i + 30 + nameLength);
    // Random bytes inside compressed data can spell the signature; a real
    // header has a printable name and a known method. (Such bytes, when they
    // are not inside a listed entry, are still inspected as unclaimed bytes.)
    if (!/^[\x20-\x7e]+$/.test(name) || (method !== 0 && method !== 8))
      continue;
    const id = `u${unlisted}`;
    out.names.push({ id, n: `unlisted ${unlisted}`, name });
    unlisted++;
    if (flags & 0x8 && compressedSize === 0) {
      out.problems.push({ problem: "unlisted-entry", id });
      continue;
    }
    const data = readLocal(buf, i, { id, method, compressedSize }, out);
    if (data) out.entries.push({ id, data });
  }
  // Bytes no record claims: prepended, between records, or appended.
  if (end >= 0) {
    const ranges = out.claimed
      .filter(([a, b]) => b > a)
      .sort((x, y) => x[0] - y[0]);
    let at = 0;
    for (const [a, b] of ranges) {
      if (a > at)
        out.meta.push({ kind: "unclaimed", at, data: buf.subarray(at, a) });
      at = Math.max(at, b);
    }
    if (at < buf.length)
      out.meta.push({ kind: "unclaimed", at, data: buf.subarray(at) });
  }
  return {
    names: out.names,
    entries: out.entries,
    problems: out.problems,
    meta: out.meta,
  };
}
