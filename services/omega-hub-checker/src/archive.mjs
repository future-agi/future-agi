/**
 * Minimal, strict readers for .tar(.gz) and .zip uploads, and the deterministic tar writer for the
 * canonical bundle. No dependency, no extraction to disk: members are returned as buffers, and
 * links, devices, encryption and zip64 are reported rather than followed.
 */
import { gunzipSync, gzipSync, inflateRawSync } from "node:zlib";

export const MAX_UNPACKED = 8 * 1024 * 1024;
const BLOCK = 512;

const text = (buf, start, end) => {
  const slice = buf.subarray(start, end);
  const nul = slice.indexOf(0);
  return slice.subarray(0, nul < 0 ? slice.length : nul).toString("utf8");
};

function octal(buf, start, end, field) {
  if (buf[start] & 0x80) throw new Error(`tar: base-256 ${field} is not supported`);
  const value = text(buf, start, end).trim();
  if (value === "") return 0;
  if (!/^[0-7]+$/.test(value)) throw new Error(`tar: bad ${field}`);
  return parseInt(value, 8);
}

function checksumOk(header) {
  let sum = 0;
  for (let i = 0; i < BLOCK; i += 1) sum += i >= 148 && i < 156 ? 32 : header[i];
  return sum === octal(header, 148, 156, "checksum");
}

function paxPath(data) {
  let at = 0;
  let found;
  while (at < data.length) {
    const space = data.indexOf(32, at);
    if (space < 0) break;
    const length = parseInt(data.subarray(at, space).toString("utf8"), 10);
    if (!Number.isInteger(length) || length <= 0) break;
    const record = data.subarray(space + 1, at + length - 1).toString("utf8");
    const eq = record.indexOf("=");
    if (record.slice(0, eq) === "path") found = record.slice(eq + 1);
    at += length;
  }
  return found;
}

/** `[{ name, type: "file"|"dir"|"link"|"other", data }]` from an uncompressed tar. */
export function readTar(buf) {
  const out = [];
  let at = 0;
  let longName;
  let total = 0;
  while (at + BLOCK <= buf.length) {
    const header = buf.subarray(at, at + BLOCK);
    if (header.every((byte) => byte === 0)) break;
    if (!checksumOk(header)) throw new Error("tar: header checksum mismatch");
    const size = octal(header, 124, 136, "size");
    const flag = String.fromCharCode(header[156] || 48);
    const prefix = text(header, 345, 500);
    let name = text(header, 0, 100);
    if (prefix && text(header, 257, 262) === "ustar") name = `${prefix}/${name}`;
    const data = buf.subarray(at + BLOCK, at + BLOCK + size);
    if (data.length < size) throw new Error("tar: truncated member");
    at += BLOCK + Math.ceil(size / BLOCK) * BLOCK;
    total += size;
    if (total > MAX_UNPACKED) throw new Error("tar: more than 8 MiB unpacked");
    if (flag === "x") {
      longName = paxPath(data) ?? longName;
      continue;
    }
    if (flag === "g") continue;
    if (flag === "L") {
      longName = text(data, 0, data.length);
      continue;
    }
    if (longName) {
      name = longName;
      longName = undefined;
    }
    const type = flag === "0" || flag === "7" ? "file" : flag === "5" ? "dir" : flag === "1" || flag === "2" ? "link" : "other";
    out.push({ name, type, data: type === "file" ? Buffer.from(data) : Buffer.alloc(0) });
  }
  return out;
}

/** `[{ name, type, data }]` from a zip. Encrypted members and zip64 archives throw. */
export function readZip(buf) {
  let eocd = -1;
  for (let i = buf.length - 22; i >= Math.max(0, buf.length - 65557); i -= 1) {
    if (buf.readUInt32LE(i) === 0x06054b50) {
      eocd = i;
      break;
    }
  }
  if (eocd < 0) throw new Error("zip: no end of central directory");
  const count = buf.readUInt16LE(eocd + 10);
  let at = buf.readUInt32LE(eocd + 16);
  if (count === 0xffff || at === 0xffffffff) throw new Error("zip: zip64 is not supported");
  const out = [];
  let total = 0;
  for (let n = 0; n < count; n += 1) {
    if (buf.readUInt32LE(at) !== 0x02014b50) throw new Error("zip: bad central directory");
    const madeBy = buf.readUInt16LE(at + 4) >> 8;
    const flags = buf.readUInt16LE(at + 8);
    const method = buf.readUInt16LE(at + 10);
    const compressed = buf.readUInt32LE(at + 20);
    const size = buf.readUInt32LE(at + 24);
    const nameLength = buf.readUInt16LE(at + 28);
    const extraLength = buf.readUInt16LE(at + 30);
    const commentLength = buf.readUInt16LE(at + 32);
    const external = buf.readUInt32LE(at + 38);
    const local = buf.readUInt32LE(at + 42);
    const name = buf.subarray(at + 46, at + 46 + nameLength).toString("utf8");
    at += 46 + nameLength + extraLength + commentLength;
    if (flags & 1) throw new Error(`zip: ${name} is encrypted`);
    const mode = madeBy === 3 ? external >>> 16 : 0;
    const kind = mode & 0o170000;
    if (name.endsWith("/")) {
      out.push({ name, type: "dir", data: Buffer.alloc(0) });
      continue;
    }
    if (kind === 0o120000) {
      out.push({ name, type: "link", data: Buffer.alloc(0) });
      continue;
    }
    if (kind !== 0 && kind !== 0o100000) {
      out.push({ name, type: "other", data: Buffer.alloc(0) });
      continue;
    }
    if (buf.readUInt32LE(local) !== 0x04034b50) throw new Error("zip: bad local header");
    const start = local + 30 + buf.readUInt16LE(local + 26) + buf.readUInt16LE(local + 28);
    const raw = buf.subarray(start, start + compressed);
    total += size;
    if (total > MAX_UNPACKED) throw new Error("zip: more than 8 MiB unpacked");
    let data;
    if (method === 0) data = Buffer.from(raw);
    else if (method === 8) data = inflateRawSync(raw, { maxOutputLength: MAX_UNPACKED });
    else throw new Error(`zip: ${name} uses compression method ${method}`);
    if (data.length !== size) throw new Error(`zip: ${name} has the wrong size`);
    out.push({ name, type: "file", data });
  }
  return out;
}

/** Members of an upload: gzip (a tar inside) or zip, by magic bytes. */
export function readUpload(buf) {
  if (buf[0] === 0x1f && buf[1] === 0x8b) return { format: "tar.gz", members: readTar(gunzipSync(buf, { maxOutputLength: MAX_UNPACKED + 1024 * 1024 })) };
  if (buf.readUInt32LE(0) === 0x04034b50) return { format: "zip", members: readZip(buf) };
  throw new Error("the upload is neither a .tar.gz nor a .zip");
}

function header(name, size) {
  const block = Buffer.alloc(BLOCK);
  let base = name;
  let prefix = "";
  if (Buffer.byteLength(name) > 100) {
    const cut = name.lastIndexOf("/", 155);
    prefix = name.slice(0, cut);
    base = name.slice(cut + 1);
    if (cut <= 0 || Buffer.byteLength(base) > 100 || Buffer.byteLength(prefix) > 155) throw new Error(`tar: path too long: ${name}`);
  }
  block.write(base, 0, "utf8");
  block.write("0000644\0", 100);
  block.write("0000000\0", 108);
  block.write("0000000\0", 116);
  block.write(`${size.toString(8).padStart(11, "0")}\0`, 124);
  block.write("00000000000\0", 136);
  block.write("        ", 148);
  block.write("0", 156);
  block.write("ustar\0", 257);
  block.write("00", 263);
  block.write(prefix, 345, "utf8");
  let sum = 0;
  for (const byte of block) sum += byte;
  block.write(`${sum.toString(8).padStart(6, "0")}\0 `, 148);
  return block;
}

/** A deterministic gzip tar: given names in the given order, mode 0644, uid/gid 0, mtime 0. */
export function writeTarGz(files) {
  const parts = [];
  for (const { name, data } of files) {
    parts.push(header(name, data.length), data, Buffer.alloc((BLOCK - (data.length % BLOCK)) % BLOCK));
  }
  parts.push(Buffer.alloc(BLOCK * 2));
  const gz = gzipSync(Buffer.concat(parts), { level: 9 });
  gz[9] = 0xff; // OS byte: "unknown" on every platform, so the bytes do not depend on the packer's host
  return gz;
}
