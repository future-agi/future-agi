import { webcrypto } from "node:crypto";
import { afterAll, beforeAll, vi } from "vitest";
import { gzip, sha256Hex } from "../../deployments/lib/bundle";

// jsdom's Crypto omits subtle; use real SHA-256 rather than mocking integrity.
beforeAll(() => vi.stubGlobal("crypto", webcrypto));
afterAll(() => vi.unstubAllGlobals());
import { writeTar } from "../../deployments/lib/tar";

export const PIN = "future-agi/first-agent@1.0.0";
// Node's TextEncoder returns host-realm bytes in jsdom; browser callers use
// the same realm as the archive reader. Preserve every byte in that realm.
export const utf8 = (text) => new Uint8Array(new TextEncoder().encode(text));
export const project = {
  "omega.studio.json": utf8(
    JSON.stringify({ name: "first-agent", entries: [{ id: "main" }] }),
  ),
  "asset.bin": new Uint8Array([0, 255, 128, 13, 10, 0, 254]),
  "large.ts": utf8("x".repeat(70_000)),
};
export async function catalog(files, cast) {
  const lines = await Promise.all(
    Object.entries(files)
      .filter(([p]) => p !== cast)
      .map(async ([p, data]) => `${p}\0${await sha256Hex(data)}`),
  );
  return `sha256:${await sha256Hex(utf8(lines.sort().join("\n")))}`;
}
export async function fixture({
  official = false,
  files = project,
  extra = [],
  manifest: overrides = {},
} = {}) {
  const manifest = {
    id: "first-agent",
    version: "1.0.0",
    ...(official ? { source: "first-agent" } : {}),
    ...overrides,
  };
  const entry = {
    "omega.hub.json": utf8(JSON.stringify(manifest)),
    "demo.cast": utf8("recording"),
    ...(official ? {} : files),
  };
  const root = official ? "source" : "entry";
  const members = [
    ...Object.entries(entry).map(([path, data]) => ({
      path: `entry/${path}`,
      data,
    })),
    ...(official
      ? Object.entries(files).map(([path, data]) => ({
          path: `source/${path}`,
          data,
        }))
      : []),
    ...extra,
  ];
  const archive = await gzip(writeTar(members));
  const metadata = {
    ref: PIN,
    version: "1.0.0",
    manifest,
    status: "approved",
    capabilities: { deploy: true },
    bundleSha256: await sha256Hex(archive),
    bundleBytes: archive.length,
    digest: await catalog(
      official ? files : entry,
      official ? undefined : "demo.cast",
    ),
  };
  return { archive, metadata, root };
}
export function repairChecksum(tar, offset = 0) {
  tar.fill(32, offset + 148, offset + 156);
  const sum = tar.subarray(offset, offset + 512).reduce((a, b) => a + b, 0);
  tar.set(utf8(`${sum.toString(8).padStart(6, "0")}\0 `), offset + 148);
  return tar;
}
