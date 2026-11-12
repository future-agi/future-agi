import { BundleError } from "./bundle";

// Match the canonical reader's stricter caps, not Deployments' 100 MiB / 20k.
// Inflation includes headers, padding per member, and Python tar's zero trailer.
export const HUB_LIMITS = Object.freeze({
  compressedBytes: 8 * 1024 * 1024,
  sourceBytes: 6 * 1024 * 1024,
  inflatedBytes: 7 * 1024 * 1024,
  members: 600,
  pathBytes: 255,
});
const BLOCK = 512;
const decoder = new TextDecoder("utf-8", { fatal: true });
const encoder = new TextEncoder();
const fail = (message) => {
  throw new BundleError(message);
};
export function checkHubAbort(signal) {
  if (signal?.aborted)
    throw new DOMException("Hub source cancelled.", "AbortError");
}

/** Limit each decoded chunk before retaining it; never use Response.arrayBuffer on inflation. */
export async function inflateHubArchive(bytes, signal) {
  checkHubAbort(signal);
  if (
    !(bytes instanceof Uint8Array) ||
    bytes.length > HUB_LIMITS.compressedBytes
  )
    fail("The Hub compressed archive exceeds the size limit.");
  const reader = new Response(bytes).body
    .pipeThrough(new DecompressionStream("gzip"))
    .getReader();
  const abort = () => {
    reader.cancel().catch(() => {});
  };
  signal?.addEventListener("abort", abort, { once: true });
  let total = 0;
  const chunks = [];
  try {
    for (;;) {
      checkHubAbort(signal);
      const { done, value } = await reader.read();
      checkHubAbort(signal);
      if (done) break;
      total += value.byteLength;
      if (total > HUB_LIMITS.inflatedBytes)
        fail("The inflated Hub archive exceeds the size limit.");
      chunks.push(value);
    }
    const out = new Uint8Array(total);
    let offset = 0;
    for (const chunk of chunks) {
      out.set(chunk, offset);
      offset += chunk.length;
    }
    return out;
  } finally {
    signal?.removeEventListener("abort", abort);
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

function text(block, start, length) {
  const bytes = block.subarray(start, start + length);
  const end = bytes.indexOf(0);
  if (end !== -1 && bytes.subarray(end).some((b) => b !== 0))
    fail("Ambiguous tar text field.");
  try {
    return decoder.decode(end === -1 ? bytes : bytes.subarray(0, end));
  } catch {
    return fail("Invalid UTF-8 tar path.");
  }
}
function octal(block, start, length) {
  const raw = String.fromCharCode(...block.subarray(start, start + length));
  if (!/^[0-7]+[\0 ]*$/.test(raw)) fail("Invalid tar numeric field.");
  const number = Number.parseInt(raw, 8);
  if (!Number.isSafeInteger(number)) fail("Invalid tar size.");
  return number;
}

/** Strict canonical ustar only; all validation precedes adding a member view. */
export function readHubTar(tar, signal) {
  if (
    !(tar instanceof Uint8Array) ||
    tar.length > HUB_LIMITS.inflatedBytes ||
    tar.length % BLOCK !== 0
  )
    fail("Invalid tar size or archive size limit.");
  const members = [];
  const seen = new Set();
  let offset = 0;
  let total = 0;
  while (offset + BLOCK <= tar.length) {
    checkHubAbort(signal);
    const block = tar.subarray(offset, offset + BLOCK);
    if (block.every((b) => b === 0)) {
      if (
        offset + 2 * BLOCK > tar.length ||
        tar.subarray(offset).some((b) => b !== 0)
      )
        fail("Invalid tar end marker or trailing data.");
      return members;
    }
    const checksum = octal(block, 148, 8);
    let sum = 0;
    for (let i = 0; i < BLOCK; i += 1)
      sum += i >= 148 && i < 156 ? 32 : block[i];
    if (sum !== checksum) fail("Invalid tar checksum.");
    if (text(block, 257, 6) !== "ustar" || text(block, 263, 2) !== "00")
      fail("Only canonical ustar archives are supported.");
    if (block[156] !== 48 && block[156] !== 0)
      fail("The Hub archive contains a link or non-regular member.");
    if (text(block, 157, 100))
      fail("A regular Hub member cannot name a link target.");
    const prefix = text(block, 345, 155);
    const name = text(block, 0, 100);
    const path = prefix ? `${prefix}/${name}` : name;
    const parts = path.split("/");
    if (
      encoder.encode(path).length > HUB_LIMITS.pathBytes ||
      parts.length < 2 ||
      !["entry", "source"].includes(parts[0]) ||
      path.includes("\\") ||
      path.includes(":") ||
      Array.from(path).some(
        (c) => c.charCodeAt(0) < 32 || c.charCodeAt(0) === 127,
      ) ||
      parts.some((part) => !part || part === "." || part === "..")
    )
      fail("Unsafe Hub archive path.");
    if (seen.has(path)) fail("Duplicate Hub archive path.");
    if (members.length >= HUB_LIMITS.members)
      fail("Too many Hub archive members.");
    const size = octal(block, 124, 12);
    total += size;
    if (total > HUB_LIMITS.sourceBytes)
      fail("The Hub source exceeds the size limit.");
    const start = offset + BLOCK;
    const end = start + Math.ceil(size / BLOCK) * BLOCK;
    if (end > tar.length) fail("Truncated Hub archive member.");
    if (tar.subarray(start + size, end).some((b) => b !== 0))
      fail("Nonzero tar member padding.");
    // A file cannot also be a directory prefix, including when encountered later.
    for (let i = 1; i < parts.length; i += 1)
      if (seen.has(parts.slice(0, i).join("/")))
        fail("Conflicting Hub archive paths.");
    if (members.some((member) => member.path.startsWith(`${path}/`)))
      fail("Conflicting Hub archive paths.");
    seen.add(path);
    members.push({ path, data: tar.subarray(start, start + size) });
    offset = end;
  }
  return fail("The Hub archive has no complete end marker.");
}
