// A deterministic ustar writer and a member walker, in the browser.
//
// It writes exactly the bytes `omega deploy` writes (OSS @omega/agent-server
// src/deploy/tar.ts): POSIX ustar headers with uid/gid 0, no user or group
// names, mtime 0, mode 0644 (0755 when executable), entries in the order
// given, then two zero blocks. The same files therefore give the same
// uncompressed tar, and the same contentSha256, whether the CLI or this page
// bundled them.

const BLOCK = 512;
const encoder = new TextEncoder();
const decoder = new TextDecoder();

export class TarError extends Error {
  constructor(message) {
    super(message);
    this.name = "TarError";
  }
}

const byteLength = (text) => encoder.encode(text).length;

/** ustar keeps a path in `name` (100 bytes) and `prefix` (155 bytes), split at a slash. */
export function splitTarPath(path) {
  if (
    typeof path !== "string" ||
    path === "" ||
    path.startsWith("/") ||
    path.split("/").some((part) => part === "" || part === "." || part === "..")
  ) {
    throw new TarError(
      `A bundle path must be relative and plain: ${JSON.stringify(path)}`,
    );
  }
  if (byteLength(path) <= 100) return { name: path, prefix: "" };
  for (
    let index = path.indexOf("/");
    index !== -1;
    index = path.indexOf("/", index + 1)
  ) {
    const prefix = path.slice(0, index);
    const name = path.slice(index + 1);
    if (byteLength(prefix) <= 155 && byteLength(name) <= 100)
      return { name, prefix };
  }
  throw new TarError(
    `A bundle path is too long for a tar header (255 bytes, a file name 100): ${path}`,
  );
}

function writeText(block, offset, length, text) {
  const bytes = encoder.encode(text);
  block.set(bytes.subarray(0, length), offset);
}

function writeOctal(block, offset, length, value) {
  const digits = value.toString(8);
  if (digits.length > length - 1)
    throw new TarError(`A tar field cannot hold ${value}`);
  writeText(block, offset, length, `${digits.padStart(length - 1, "0")}\0`);
}

function header(entry) {
  const block = new Uint8Array(BLOCK);
  const { name, prefix } = splitTarPath(entry.path);
  writeText(block, 0, 100, name);
  writeOctal(block, 100, 8, entry.executable ? 0o755 : 0o644);
  writeOctal(block, 108, 8, 0);
  writeOctal(block, 116, 8, 0);
  writeOctal(block, 124, 12, entry.data.length);
  writeOctal(block, 136, 12, 0);
  block.fill(0x20, 148, 156);
  block[156] = 0x30; // "0": a regular file
  writeText(block, 257, 6, "ustar\0");
  writeText(block, 263, 2, "00");
  writeText(block, 345, 155, prefix);
  let sum = 0;
  for (const byte of block) sum += byte;
  writeText(block, 148, 8, `${sum.toString(8).padStart(6, "0")}\0 `);
  return block;
}

/**
 * The tar archive of `entries` ({ path, data: Uint8Array, executable }),
 * ending with the two zero blocks.
 */
export function writeTar(entries) {
  let total = BLOCK * 2;
  for (const entry of entries) {
    total += BLOCK + Math.ceil(entry.data.length / BLOCK) * BLOCK;
  }
  const out = new Uint8Array(total);
  let offset = 0;
  for (const entry of entries) {
    out.set(header(entry), offset);
    offset += BLOCK;
    out.set(entry.data, offset);
    offset += Math.ceil(entry.data.length / BLOCK) * BLOCK;
  }
  return out;
}

function readText(block, offset, length) {
  const field = block.subarray(offset, offset + length);
  const end = field.indexOf(0);
  return decoder.decode(field.subarray(0, end === -1 ? field.length : end));
}

function readOctal(block, offset, length) {
  const raw = String.fromCharCode(...block.subarray(offset, offset + length))
    .replace(/\0/g, "")
    .trim();
  if (!/^[0-7]+$/.test(raw)) return undefined;
  const value = Number.parseInt(raw, 8);
  return Number.isSafeInteger(value) ? value : undefined;
}

function checksumOk(block) {
  const stored = readOctal(block, 148, 8);
  if (stored === undefined) return false;
  let sum = 0;
  for (let index = 0; index < BLOCK; index += 1) {
    sum += index >= 148 && index < 156 ? 32 : block[index];
  }
  return sum === stored;
}

/**
 * Walk a ustar archive the way the control plane checks it: every member is a
 * regular file or a directory at a plain relative path. Returns the regular
 * files as { path, data }. Throws TarError naming the first problem.
 */
export function readTarMembers(tar) {
  const files = [];
  let offset = 0;
  while (offset + BLOCK <= tar.length) {
    const block = tar.subarray(offset, offset + BLOCK);
    if (block.every((byte) => byte === 0)) break;
    if (!checksumOk(block))
      throw new TarError(
        `The archive has a corrupt tar header at byte ${offset}.`,
      );
    const name = readText(block, 0, 100);
    const ustar = String.fromCharCode(...block.subarray(257, 262)) === "ustar";
    const prefix = ustar ? readText(block, 345, 155) : "";
    const path = prefix === "" ? name : `${prefix}/${name}`;
    const size = readOctal(block, 124, 12);
    const type = String.fromCharCode(block[156]);
    const start = offset + BLOCK;
    if (size === undefined || start + size > tar.length)
      throw new TarError(
        `The archive member ${JSON.stringify(path)} is truncated.`,
      );
    const trimmed = path.replace(/\/+$/, "").replace(/^\.\//, "");
    if (
      trimmed === "" ||
      path.startsWith("/") ||
      path.includes("\\") ||
      trimmed
        .split("/")
        .some((part) => part === "" || part === "." || part === "..")
    ) {
      throw new TarError(
        `The archive member ${JSON.stringify(path.slice(0, 200))} is not a plain relative path.`,
      );
    }
    if (type === "0" || type === "\0") {
      files.push({ path: trimmed, data: tar.subarray(start, start + size) });
    } else if (type !== "5") {
      throw new TarError(
        `The archive member ${JSON.stringify(path)} is a link or special file; a bundle holds only files and folders.`,
      );
    }
    offset = start + Math.ceil(size / BLOCK) * BLOCK;
  }
  return files;
}
