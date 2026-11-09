/**
 * Step 1, unpack (SPEC §4.2 row 1): read the upload strictly, find the entry, and refuse anything
 * opaque. Files stay in memory as buffers; nothing is written until the canonical layout exists.
 */
import { readUpload } from "./archive.mjs";
import { block } from "./findings.mjs";

export const RAW_LIMIT = 2 * 1024 * 1024;
export const ENTRY_LIMITS = { files: 60, bytes: 512 * 1024 };
export const SOURCE_LIMITS = { files: 400, bytes: 4 * 1024 * 1024 };
export const ID = /^[a-z][a-z0-9-]{2,39}$/;
export const SOURCE = /^[a-z][a-z0-9-]{1,39}$/;
const BANNED_EXT = [".zip", ".tar", ".gz", ".tgz", ".7z", ".rar", ".wasm", ".node", ".so", ".dylib", ".dll", ".exe", ".pyc", ".jar", ".bin"];
const IMAGES = { ".png": [0x89, 0x50, 0x4e, 0x47], ".jpg": [0xff, 0xd8, 0xff], ".jpeg": [0xff, 0xd8, 0xff], ".gif": [0x47, 0x49, 0x46, 0x38], ".webp": [0x52, 0x49, 0x46, 0x46] };
const OPAQUE_MAGIC = [
  ["a zip archive", [0x50, 0x4b, 0x03, 0x04]], ["a gzip archive", [0x1f, 0x8b]], ["an ELF binary", [0x7f, 0x45, 0x4c, 0x46]],
  ["a Mach-O binary", [0xcf, 0xfa, 0xed, 0xfe]], ["a Mach-O binary", [0xfe, 0xed, 0xfa, 0xcf]], ["a Windows executable", [0x4d, 0x5a]],
  ["a WebAssembly module", [0x00, 0x61, 0x73, 0x6d]], ["a 7z archive", [0x37, 0x7a, 0xbc, 0xaf]], ["a RAR archive", [0x52, 0x61, 0x72, 0x21]],
];
const IGNORED = (name) => name.startsWith("__MACOSX/") || /(^|\/)\._/.test(name) || /(^|\/)\.DS_Store$/.test(name);
const decoder = new TextDecoder("utf-8", { fatal: true });

const startsWith = (data, magic) => magic.every((byte, i) => data[i] === byte);
const extOf = (name) => {
  const base = name.slice(name.lastIndexOf("/") + 1).toLowerCase();
  const dot = base.lastIndexOf(".");
  return dot > 0 ? base.slice(dot) : "";
};

/** Why a file is opaque, or null when it is UTF-8 text or a real image (Hermes rule 17, C4). */
export function opaqueReason(name, data) {
  const ext = extOf(name);
  if (BANNED_EXT.includes(ext)) return `${ext} files are not allowed`;
  if (IMAGES[ext]) return startsWith(data, IMAGES[ext]) ? null : `the bytes are not a ${ext.slice(1).toUpperCase()} image`;
  const magic = OPAQUE_MAGIC.find(([, bytes]) => startsWith(data, bytes));
  if (magic && data.length >= 4) return `the file is ${magic[0]}`;
  if (data.includes(0)) return "the file holds NUL bytes: it is not text";
  try {
    decoder.decode(data);
  } catch {
    return "the file is not UTF-8 text";
  }
  return null;
}

function cleanPath(name) {
  if (name.includes("\\") || name.includes("\0")) return null;
  const parts = name.replace(/^\.\//, "").split("/").filter((part, i, all) => part !== "" || i === all.length - 1);
  if (name.startsWith("/") || parts.some((part) => part === ".." || part === ".")) return null;
  return parts.join("/");
}

function layout(files) {
  const names = [...files.keys()];
  if (names.some((name) => name.startsWith("entry/"))) {
    const stray = names.find((name) => !name.startsWith("entry/") && !name.startsWith("source/"));
    return stray ? { error: `${stray} is outside entry/ and source/` } : { entryPrefix: "entry/", sourcePrefix: "source/" };
  }
  if (files.has("omega.hub.json")) return { entryPrefix: "", sourcePrefix: null };
  const tops = new Set(names.map((name) => name.split("/")[0]));
  if (tops.size === 1) {
    const [top] = tops;
    if (files.has(`${top}/omega.hub.json`)) return { entryPrefix: `${top}/`, sourcePrefix: null };
  }
  return { error: "no omega.hub.json at the top of the upload, in one folder, or under entry/" };
}

/** Unpack an upload. Returns `{ findings, notes, id, source, manifestRaw, entry: Map, sourceFiles: Map }`. */
export function unpack(raw, { tier }) {
  const findings = [];
  const notes = [];
  const fail = (rule, file, message, fix) => findings.push(block("unpack", rule, file, message, { fix }));
  const out = { findings, notes, id: null, source: null, entry: new Map(), sourceFiles: new Map() };
  if (raw.length > RAW_LIMIT) {
    fail("size", "", `the upload is ${raw.length} bytes; the limit is 2 MiB`, "remove large files");
    return out;
  }
  let members;
  try {
    members = readUpload(raw).members;
  } catch (error) {
    fail("archive", "", `the upload does not open: ${error.message}`, "pack it with omega hub pack");
    return out;
  }
  const files = new Map();
  for (const member of members) {
    if (IGNORED(member.name)) {
      notes.push(`ignored ${member.name} (macOS metadata)`);
      continue;
    }
    const name = cleanPath(member.name);
    if (name === null) {
      fail("path", member.name, "an absolute path, '..' or '.' segment, backslash or NUL in a path", "use plain relative paths");
      continue;
    }
    if (member.type === "dir") continue;
    if (member.type !== "file") {
      fail("link", name, `${member.type === "link" ? "a link" : "a device or special file"} in the upload`, "replace it with a regular file");
      continue;
    }
    if (files.has(name)) fail("duplicate", name, "the same path appears twice", "pack the folder again");
    files.set(name, member.data);
  }
  if (findings.length) return out;
  const where = layout(files);
  if (where.error) {
    fail("layout", "", where.error, "upload the entry folder (with omega.hub.json) as packed by omega hub pack");
    return out;
  }
  for (const [name, data] of files) {
    if (name.startsWith(where.entryPrefix)) out.entry.set(name.slice(where.entryPrefix.length), data);
    else if (where.sourcePrefix && name.startsWith(where.sourcePrefix)) out.sourceFiles.set(name.slice(where.sourcePrefix.length), data);
  }
  let manifest;
  try {
    manifest = JSON.parse(out.entry.get("omega.hub.json").toString("utf8"));
  } catch {
    fail("manifest", "omega.hub.json", "omega.hub.json does not parse", "fix the JSON");
    return out;
  }
  if (typeof manifest?.id !== "string" || !ID.test(manifest.id)) {
    fail("manifest", "omega.hub.json", "`id` must be 3-40 lowercase letters, digits and hyphens", "set id to the folder name");
    return out;
  }
  out.id = manifest.id;
  out.manifestRaw = out.entry.get("omega.hub.json");
  if (out.sourceFiles.size > 0) {
    if (tier !== "official") fail("source", "source/", "only Official entries may carry a source folder", "put every file in the entry");
    else if (typeof manifest.source !== "string" || !SOURCE.test(manifest.source)) fail("source", "omega.hub.json", "`source` must be one folder name", "set source to a single folder name");
    else out.source = manifest.source;
  }
  checkLimits(out.entry, ENTRY_LIMITS, "", fail);
  checkLimits(out.sourceFiles, SOURCE_LIMITS, "source/", fail);
  for (const [prefix, map] of [["", out.entry], ["source/", out.sourceFiles]]) {
    for (const [rel, data] of map) {
      const why = opaqueReason(rel, data);
      if (why) fail("opaque-file", `${prefix}${rel}`, why, "ship only text and PNG, JPEG, GIF or WebP images (C4)");
    }
  }
  return out;
}

function checkLimits(map, limits, prefix, fail) {
  const bytes = [...map.values()].reduce((sum, data) => sum + data.length, 0);
  if (map.size > limits.files) fail("files", prefix || ".", `${map.size} files; the limit is ${limits.files}`, "remove files");
  if (bytes > limits.bytes) fail("files", prefix || ".", `${bytes} bytes unpacked; the limit is ${limits.bytes}`, "remove large files");
}
