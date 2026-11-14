#!/usr/bin/env node
// Scans Playwright artefacts for a secret before they are shared: the HTML
// report (its embedded base64 zip is unpacked), JSON/blob reports, traces and
// any other file, including the zip archives among them. Looks for the raw
// value, its URL-encoded form and its base64 forms at all three byte offsets.
//
//   SCAN_SECRET=<value> node scripts/scan-artefacts-for-secret.mjs <dir|file>...
//
// SCAN_CONTEXT=1 also prints the bytes around each hit, every encoding masked.
// The secret is read from the environment so it never lands in shell history
// or process listings. Prints one line per hit (path and form, never the
// value) and exits 1 on any hit, 2 when a target is missing or nothing was
// scanned, 0 when every artefact is clean.
import * as fs from 'node:fs';
import * as path from 'node:path';
import * as zlib from 'node:zlib';

const secret = process.env.SCAN_SECRET ?? '';
if (secret.length < 8) {
  console.error('SCAN_SECRET must be set to the value to look for (at least 8 characters).');
  process.exit(2);
}
const targets = process.argv.slice(2);
if (!targets.length) {
  console.error('usage: SCAN_SECRET=<value> node scripts/scan-artefacts-for-secret.mjs <dir|file>...');
  process.exit(2);
}

// Base64 of the secret depends on its byte offset in the encoded stream. For
// each of the three offsets, keep exactly the characters whose 6 bits all come
// from the secret: they are the same whatever bytes precede or follow it.
function base64Needles(value) {
  const bytes = Buffer.from(value, 'utf8');
  const out = [];
  for (let shift = 0; shift < 3; shift++) {
    const enc = Buffer.concat([Buffer.alloc(shift), bytes]).toString('base64');
    const start = Math.ceil((8 * shift) / 6);
    const end = Math.floor((8 * (shift + bytes.length)) / 6);
    out.push(enc.slice(start, end));
  }
  return out;
}
const needles = [
  ['raw', Buffer.from(secret, 'utf8')],
  ['url-encoded', Buffer.from(encodeURIComponent(secret), 'utf8')],
  ['json-escaped', Buffer.from(JSON.stringify(secret).slice(1, -1), 'utf8')],
  ...base64Needles(secret).map((n, i) => [`base64@${i}`, Buffer.from(n, 'utf8')]),
].filter(([, b], i, all) => all.findIndex(([, o]) => o.equals(b)) === i);

// Minimal zip reader (stored and deflated entries), enough for Playwright's
// traces, blob reports and the HTML report's embedded archive.
// Anything it cannot read is reported (unreadable), so the scan is not
// clean: bytes it never examined cannot pass as "0 hit(s)".
function unzip(buf, label) {
  const entries = [];
  let eocd = -1;
  for (let i = buf.length - 22; i >= Math.max(0, buf.length - 65_557); i--) {
    if (buf.readUInt32LE(i) === 0x06054b50) { eocd = i; break; }
  }
  if (eocd < 0) { unreadableAt(label, 'zip without an end-of-central-directory record'); return entries; }
  const count = buf.readUInt16LE(eocd + 10);
  let p = buf.readUInt32LE(eocd + 16);
  if (count === 0xffff || p === 0xffffffff) { unreadableAt(label, 'zip64 archive'); return entries; }
  let read = 0;
  for (let n = 0; n < count && p + 46 <= buf.length; n++) {
    if (buf.readUInt32LE(p) !== 0x02014b50) break;
    read += 1;
    const method = buf.readUInt16LE(p + 10);
    const compSize = buf.readUInt32LE(p + 20);
    const nameLen = buf.readUInt16LE(p + 28);
    const extraLen = buf.readUInt16LE(p + 30);
    const commentLen = buf.readUInt16LE(p + 32);
    const local = buf.readUInt32LE(p + 42);
    const name = buf.subarray(p + 46, p + 46 + nameLen).toString('utf8');
    p += 46 + nameLen + extraLen + commentLen;
    const lNameLen = buf.readUInt16LE(local + 26);
    const lExtraLen = buf.readUInt16LE(local + 28);
    const data = buf.subarray(local + 30 + lNameLen + lExtraLen, local + 30 + lNameLen + lExtraLen + compSize);
    if (method !== 0 && method !== 8) { unreadableAt(`${label}!${name}`, `compression method ${method}`); continue; }
    try {
      entries.push([name, method === 0 ? data : zlib.inflateRawSync(data)]);
    } catch (e) {
      unreadableAt(`${label}!${name}`, `inflate failed: ${e.message}`);
    }
  }
  if (read < count) unreadableAt(label, `read ${read} of ${count} central directory entries`);
  return entries;
}

// The bytes around the first hit of one form, with every form masked across
// the whole buffer first, so no window edge can cut a copy in half.
function context(buf, form) {
  let text = buf.toString('latin1');
  for (const [name, needle] of needles) text = text.split(needle.toString('latin1')).join(`<SECRET:${name}>`);
  const marker = `<SECRET:${form}>`;
  const at = text.indexOf(marker);
  // Masking came first, so a window edge can only cut a marker, never a copy.
  return text.slice(Math.max(0, at - 160), at + marker.length + 50)
    .replace(/<SECRET:[^>]*>/g, '<SECRET>')
    .replace(/\s+/g, ' ');
}

let hits = 0;
let scanned = 0;
let missing = 0;
let unreadable = 0;
function unreadableAt(label, why) {
  unreadable += 1;
  console.log(`UNREADABLE ${label} (${why})`);
}
function scanBuffer(label, buf, depth = 0) {
  scanned += 1;
  for (const [form, needle] of needles) {
    if (buf.indexOf(needle) >= 0) {
      hits += 1;
      console.log(`HIT ${label} (${form})`);
      if (process.env.SCAN_CONTEXT === '1') console.log(`    …${context(buf, form)}…`);
    }
  }
  if (depth > 3) return;
  if (buf.length > 4 && buf.readUInt32LE(0) === 0x04034b50) {
    for (const [name, data] of unzip(buf, label)) scanBuffer(`${label}!${name}`, data, depth + 1);
  }
  // The HTML report embeds its data as a base64 zip in a data: URL.
  const marker = Buffer.from('data:application/zip;base64,');
  for (let i = buf.indexOf(marker); i >= 0; i = buf.indexOf(marker, i + 1)) {
    let j = i + marker.length;
    while (j < buf.length && /[A-Za-z0-9+/=]/.test(String.fromCharCode(buf[j]))) j++;
    const zip = Buffer.from(buf.subarray(i + marker.length, j).toString('latin1'), 'base64');
    scanBuffer(`${label}#embedded-zip`, zip, depth + 1);
  }
}
function walk(p) {
  if (!fs.existsSync(p)) { console.log(`MISSING ${p}`); missing += 1; return; }
  const st = fs.statSync(p);
  if (st.isDirectory()) {
    for (const e of fs.readdirSync(p)) walk(path.join(p, e));
  } else if (st.isFile()) {
    scanBuffer(p, fs.readFileSync(p));
  }
}
for (const t of targets) walk(t);
console.log(`scanned ${scanned} files/entries for ${needles.length} encodings of the secret: ${hits} hit(s)`);
if (hits) process.exit(1);
if (missing || unreadable || !scanned) {
  console.log(missing ? `${missing} target(s) missing: not a clean scan`
    : unreadable ? `${unreadable} archive(s)/entries could not be read: not a clean scan`
      : 'nothing scanned: not a clean scan');
  process.exit(2);
}
process.exit(0);
