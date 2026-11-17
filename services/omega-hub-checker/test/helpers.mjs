/** Fixture builders for the checker tests. Unsafe bytes (secrets, binaries, links) are made here. */
import { createHash } from "node:crypto";
import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";
import { writeTarGz } from "../src/archive.mjs";

export const FIXTURES = path.join(path.dirname(fileURLToPath(import.meta.url)), "fixtures");
export const sha = (data) => createHash("sha256").update(data).digest("hex");

export function readDir(dir, prefix = "", out = new Map()) {
  for (const name of readdirSync(dir).sort()) {
    const file = path.join(dir, name);
    if (statSync(file).isDirectory()) readDir(file, `${prefix}${name}/`, out);
    else out.set(`${prefix}${name}`, readFileSync(file));
  }
  return out;
}

const manifestOf = (files) => JSON.parse(files.get("omega.hub.json").toString("utf8"));
const setManifest = (files, manifest) => files.set("omega.hub.json", Buffer.from(`${JSON.stringify(manifest, null, 2)}\n`));

/** `good`, plus the named overlay folder (its files, and overlay.json manifest changes). */
export function fixture(name) {
  if (name === "good" || name === "curl-probe") return readDir(path.join(FIXTURES, name));
  const files = readDir(path.join(FIXTURES, "good"));
  const overlay = readDir(path.join(FIXTURES, name));
  for (const [rel, data] of overlay) if (rel !== "overlay.json" && rel !== "README.md") files.set(rel, data);
  if (overlay.has("overlay.json")) {
    const spec = JSON.parse(overlay.get("overlay.json").toString("utf8"));
    setManifest(files, { ...manifestOf(files), ...spec.manifest });
    if (spec.readmeId) files.set("README.md", Buffer.from(files.get("README.md").toString("utf8").replaceAll("good-agent", spec.readmeId)));
  }
  const builders = {
    secret: () => files.set("config.mjs", Buffer.from(`export const OPENAI_API_KEY = "${"sk-" + "proj-"}${"Q7".repeat(14)}";\nexport const KEY = \`${"-----BEGIN " + "RSA PRIVATE KEY-----"}\`;\n`)),
    binary: () => {
      files.set("helper.ts", Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 13]));
      files.set("tool", Buffer.concat([Buffer.from([0x7f, 0x45, 0x4c, 0x46, 2, 1, 1, 0]), Buffer.alloc(56)]));
    },
    archive: () => files.set("vendor.zip", Buffer.concat([Buffer.from("PK\u0003\u0004"), Buffer.alloc(26)])),
  };
  builders[name]?.();
  return files;
}

/** A .tar.gz upload with every file under `entry/` (plus extra raw members). */
export function upload(files, { layout = "entry", extra = [] } = {}) {
  const prefix = layout === "entry" ? "entry/" : layout === "folder" ? "my-entry/" : "";
  const members = [...files].map(([rel, data]) => ({ name: `${prefix}${rel}`, data }));
  if (!extra.length) return writeTarGz(members);
  return gzipSync(Buffer.concat([...members.map(rawMember), ...extra.map(rawMember), Buffer.alloc(1024)]));
}

/** A raw tar member, including the kinds the reader must refuse (type "2" symlink, odd names). */
export function rawMember({ name, data = Buffer.alloc(0), type = "0", link = "" }) {
  const header = Buffer.alloc(512);
  header.write(name, 0, "utf8");
  header.write("0000644\0", 100);
  header.write("0000000\0", 108);
  header.write("0000000\0", 116);
  header.write(`${data.length.toString(8).padStart(11, "0")}\0`, 124);
  header.write("00000000000\0", 136);
  header.write("        ", 148);
  header.write(type, 156);
  header.write(link, 157);
  header.write("ustar\0", 257);
  header.write("00", 263);
  let sum = 0;
  for (const byte of header) sum += byte;
  header.write(`${sum.toString(8).padStart(6, "0")}\0 `, 148);
  return Buffer.concat([header, data, Buffer.alloc((512 - (data.length % 512)) % 512)]);
}

/** A stored (uncompressed) zip with the given files. */
export function zip(files) {
  const locals = [];
  const centrals = [];
  let offset = 0;
  for (const [name, data] of files) {
    const nameBuf = Buffer.from(name);
    const crc = crc32(data);
    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0); local.writeUInt16LE(20, 4); local.writeUInt32LE(crc, 14);
    local.writeUInt32LE(data.length, 18); local.writeUInt32LE(data.length, 22); local.writeUInt16LE(nameBuf.length, 26);
    const central = Buffer.alloc(46);
    central.writeUInt32LE(0x02014b50, 0); central.writeUInt16LE((3 << 8) | 20, 4); central.writeUInt16LE(20, 6);
    central.writeUInt32LE(crc, 16); central.writeUInt32LE(data.length, 20); central.writeUInt32LE(data.length, 24);
    central.writeUInt16LE(nameBuf.length, 28); central.writeUInt32LE((0o100644 << 16) >>> 0, 38); central.writeUInt32LE(offset, 42);
    locals.push(local, nameBuf, data);
    centrals.push(central, nameBuf);
    offset += 30 + nameBuf.length + data.length;
  }
  const cd = Buffer.concat(centrals);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0); end.writeUInt16LE(files.size, 8); end.writeUInt16LE(files.size, 10);
  end.writeUInt32LE(cd.length, 12); end.writeUInt32LE(offset, 16);
  return Buffer.concat([...locals, cd, end]);
}

function crc32(data) {
  let crc = ~0;
  for (const byte of data) {
    crc ^= byte;
    for (let k = 0; k < 8; k += 1) crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1));
  }
  return ~crc >>> 0;
}

export const job = (overrides = {}) => ({ job: "00000000-0000-4000-8000-000000000001", submission: "00000000-0000-4000-8000-000000000002",
  publisher: "acme", publisherTier: "community", hints: {}, ...overrides });
export const registry = (context = {}, previous = null) => ({
  context: async () => ({ previousVersion: previous ? { version: "0.9.0" } : null, versionTaken: false, remixTargetOk: null,
    remixTargetBlocked: false, movedTo: null, entryRemoved: false, codeHashes: {}, ...context }),
  previousBundle: async () => previous,
});
export const passingSandbox = async () => ({ step: { id: "sandbox-run", status: "pass", ms: 1, findings: [], network: "none" }, log: "ok\n" });
export const CHECKER = { version: "0.1.0", omegaCommit: "881b05ba5", runnerImage: "sha256:test", vendoredHub: "881b05ba5" };
