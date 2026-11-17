// RED at 7e05e449: hubBundle/hubArchive do not exist. Each named case below
// demands validation/conversion absent from the text-preview-only Hub client.
import { describe, expect, it, vi } from "vitest";
import {
  adaptHubBundle,
  parseHubSource,
  validateHubVersion,
} from "../../deployments/lib/hubBundle";
import {
  HUB_LIMITS,
  inflateHubArchive,
  readHubTar,
} from "../../deployments/lib/hubArchive";
import { gunzip, gzip, sha256Hex } from "../../deployments/lib/bundle";
import { readTarMembers, writeTar } from "../../deployments/lib/tar";
import {
  PIN,
  fixture,
  project,
  repairChecksum,
  utf8,
} from "./hubBundleFixtures";

const parse = (value) => parseHubSource(new URLSearchParams(value));

describe("exact Hub source admission", () => {
  it("accepts server grammar, including six-digit core versions, and distinguishes no Hub", () => {
    expect(parse(`hub=${encodeURIComponent(PIN)}`).ref).toBe(PIN);
    expect(parse("hub=aa/abc@999999.0.1").version).toBe("999999.0.1");
    expect(parse("deployment=d1")).toBeNull();
  });
  it.each([
    "hub=",
    "hub=aa/abc",
    "hub=https://evil.invalid/a",
    "hub=aa/abc@01.0.0",
    "hub=aa/abc@1.0.0-rc",
    "hub=aa/abc@1000000.0.0",
    "hub=aa/abc@1.0.0%0A",
    `hub=${PIN}&hub=${PIN}`,
    `hub=${PIN}&deployment=`,
    `hub=${"a".repeat(300)}`,
  ])("rejects ambiguous or invalid query %s", (query) => {
    expect(() => parse(query)).toThrow();
  });
  it.each([
    ["identity", { ref: "someone/another@1.0.0" }],
    ["version", { version: "2.0.0" }],
    ["removed", { status: "removed" }],
    ["yanked", { status: "yanked" }],
    ["missing status", { status: undefined }],
    ["unsupported capability", { capabilities: { deploy: false } }],
    ["truthy capability", { capabilities: { deploy: "true" } }],
    ["bad digest", { digest: "sha256:123" }],
    ["bad archive digest", { bundleSha256: "123" }],
  ])("fails closed for %s", async (_, change) => {
    const { metadata } = await fixture();
    expect(() =>
      validateHubVersion(parse(`hub=${PIN}`), { ...metadata, ...change }),
    ).toThrow();
  });
});

describe("canonical archive to deployment bundle", () => {
  it.each([false, true])(
    "preserves binary and >64KiB source (official=%s), deterministic digests and provenance",
    async (official) => {
      const input = await fixture({ official });
      const bundle = await adaptHubBundle({
        pin: parse(`hub=${PIN}`),
        ...input,
      });
      const again = await adaptHubBundle({
        pin: parse(`hub=${PIN}`),
        ...input,
      });
      const tar = await gunzip(bundle.archive);
      const members = readTarMembers(tar);
      for (const [path, data] of Object.entries(project))
        expect(members.find((m) => m.path === path).data).toEqual(data);
      expect(bundle.source).toBe("hub");
      expect(bundle.manifest.entries[0].id).toBe("main");
      expect(bundle.sha256).toBe(await sha256Hex(bundle.archive));
      expect(bundle.contentSha256).toBe(await sha256Hex(tar));
      expect(bundle.archive).toEqual(again.archive);
      expect(bundle.provenance).toMatchObject({
        ref: PIN,
        version: "1.0.0",
        digest: input.metadata.digest,
        bundleSha256: input.metadata.bundleSha256,
      });
      expect(bundle.sha256).not.toBe(input.metadata.bundleSha256);
      expect(
        members.some(
          (m) => m.path.startsWith("entry/") || m.path.startsWith("source/"),
        ),
      ).toBe(false);
    },
  );
  it("uses entry fallback when manifest.source exists but source root is empty", async () => {
    const input = await fixture({ manifest: { source: "first-agent" } });
    expect(
      (await adaptHubBundle({ pin: parse(`hub=${PIN}`), ...input })).manifest
        .name,
    ).toBe("first-agent");
  });
  it("retains unfamiliar metadata and excludes only the recording from catalog, not deployment", async () => {
    const input = await fixture({
      files: { ...project, "metadata.custom": utf8("keep") },
    });
    const bundle = await adaptHubBundle({ pin: parse(`hub=${PIN}`), ...input });
    expect(bundle.files.map((f) => f.path)).toEqual(
      expect.arrayContaining([
        "omega.hub.json",
        "demo.cast",
        "metadata.custom",
      ]),
    );
  });
  it.each(["archive", "catalog", "manifest", "size", "header"])(
    "refuses %s mismatch",
    async (kind) => {
      const input = await fixture(
        kind === "manifest" ? { manifest: { version: "9.0.0" } } : {},
      );
      if (kind === "archive") input.archive[10] ^= 1;
      if (kind === "catalog")
        input.metadata.digest = `sha256:${"0".repeat(64)}`;
      if (kind === "size") input.metadata.bundleBytes += 1;
      if (kind === "header") input.bundleSha256 = "0".repeat(64);
      await expect(
        adaptHubBundle({ pin: parse(`hub=${PIN}`), ...input }),
      ).rejects.toThrow();
    },
  );
  it("requires a supported root project and allows LangGraph summaries without promising runtime support", async () => {
    const bad = await fixture({
      files: { "nested/omega.studio.json": project["omega.studio.json"] },
    });
    await expect(
      adaptHubBundle({ pin: parse(`hub=${PIN}`), ...bad }),
    ).rejects.toThrow(/project|manifest/i);
    const good = await fixture({
      files: {
        "langgraph.json": utf8('{"graphs":{"main":"./main.js:graph"}}'),
        "main.js": utf8("export const graph = {};"),
      },
    });
    expect(
      (await adaptHubBundle({ pin: parse(`hub=${PIN}`), ...good })).manifest
        .file,
    ).toBe("langgraph.json");
  });
});

describe("bounded canonical tar and inflation", () => {
  const one = () =>
    writeTar([{ path: "entry/ok.bin", data: new Uint8Array([0, 255]) }]);
  it("accepts a regular-file control and canonical zero padding", () => {
    expect(readHubTar(one())[0].data).toEqual(new Uint8Array([0, 255]));
  });
  it.each([
    "../oops",
    "/entry/oops",
    "entry/../oops",
    "entry//oops",
    "entry/./oops",
    "entry\\oops",
    "other/oops",
    "./entry/oops",
    "entry/C:/oops",
  ])("rejects unsafe path %s before accumulation", (path) => {
    const tar = one();
    tar.fill(0, 0, 100);
    tar.set(utf8(path), 0);
    repairChecksum(tar);
    expect(() => readHubTar(tar)).toThrow();
  });
  it.each(["1", "2", "3", "4", "5", "6", "x", "L"])(
    "rejects member type %s",
    (type) => {
      const tar = one();
      tar[156] = type.charCodeAt(0);
      repairChecksum(tar);
      expect(() => readHubTar(tar)).toThrow();
    },
  );
  it("rejects duplicate paths, corrupt checksums, truncated bodies and nonzero trailers", () => {
    const member = { path: "entry/same", data: utf8("x") };
    expect(() => readHubTar(writeTar([member, member]))).toThrow(/duplicate/i);
    const corrupt = one();
    corrupt[0] ^= 1;
    expect(() => readHubTar(corrupt)).toThrow(/checksum/i);
    expect(() => readHubTar(one().slice(0, 513))).toThrow();
    const trailing = one();
    trailing[trailing.length - 1] = 1;
    expect(() => readHubTar(trailing)).toThrow();
  });
  it("bounds member count, total source bytes and path length before retaining members", () => {
    expect(() =>
      readHubTar(
        writeTar(
          Array.from({ length: HUB_LIMITS.members + 1 }, (_, i) => ({
            path: `entry/f${i}`,
            data: new Uint8Array(),
          })),
        ),
      ),
    ).toThrow(/count|many|members/i);
    expect(() =>
      readHubTar(
        writeTar([
          {
            path: "entry/big",
            data: new Uint8Array(HUB_LIMITS.sourceBytes + 1),
          },
        ]),
      ),
    ).toThrow(/size|large|limit/i);
    const long = one();
    long.fill(97, 0, 100);
    long.fill(98, 345, 500);
    repairChecksum(long);
    expect(() => readHubTar(long)).toThrow();
  });
  it("stops decompression incrementally and refuses oversized compressed input", async () => {
    const compressed = await gzip(new Uint8Array(HUB_LIMITS.inflatedBytes + 1));
    await expect(inflateHubArchive(compressed)).rejects.toThrow(/limit|large/i);
    await expect(
      inflateHubArchive(new Uint8Array(HUB_LIMITS.compressedBytes + 1)),
    ).rejects.toThrow(/limit|large/i);
    await expect(inflateHubArchive(utf8("not gzip"))).rejects.toThrow();
  });
  it("cancels the decoded stream at the first over-limit chunk, before another read", async () => {
    let pulls = 0;
    const cancel = vi.fn();
    const stream = vi
      .spyOn(globalThis, "DecompressionStream")
      .mockImplementation(() => ({
        readable: new ReadableStream(
          {
            pull(controller) {
              pulls += 1;
              if (pulls > 1) throw new Error("Unexpected second decoded read");
              controller.enqueue(new Uint8Array(HUB_LIMITS.inflatedBytes + 1));
            },
            cancel,
          },
          { highWaterMark: 0 },
        ),
        writable: new WritableStream(),
      }));
    try {
      await expect(
        inflateHubArchive(utf8("bounded test input")),
      ).rejects.toThrow("The inflated Hub archive exceeds the size limit.");
      expect(stream).toHaveBeenCalledOnce();
      expect(stream).toHaveBeenCalledWith("gzip");
      expect(pulls).toBe(1);
      expect(cancel).toHaveBeenCalledOnce();
    } finally {
      stream.mockRestore();
    }
  });
  it("cancels before inflation and conversion", async () => {
    const input = await fixture();
    const controller = new AbortController();
    controller.abort();
    await expect(
      adaptHubBundle({
        pin: parse(`hub=${PIN}`),
        ...input,
        signal: controller.signal,
      }),
    ).rejects.toMatchObject({ name: "AbortError" });
  });
});
