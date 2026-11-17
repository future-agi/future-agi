import { Buffer } from "node:buffer";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";

import {
  LEAK_PATTERNS,
  PROTOTYPE_ONLY_PATHS,
  leakHits,
  scanDist,
} from "../../../scripts/prototype-leak-scan.mjs";

describe("leakHits", () => {
  it("names each pattern with the files that contain it", () => {
    const hits = leakHits(
      [
        { file: "a.js", content: "x proto_stage y" },
        { file: "b.js", content: Buffer.from("startPrototypeMode()") },
        { file: "c.js", content: "clean" },
      ],
      ["proto_stage", "startPrototypeMode", "absent"],
    );
    expect(hits).toEqual([
      { pattern: "proto_stage", files: ["a.js"] },
      { pattern: "startPrototypeMode", files: ["b.js"] },
    ]);
  });

  it("covers fixture ids, the prototype runtime and its statics", () => {
    expect(LEAK_PATTERNS).toEqual(
      expect.arrayContaining([
        "d5a1e7c2-0b3f-4a8d-9e61",
        "proto_stage",
        "startPrototypeMode",
        "mock/multimodal",
        "prototype-mock-sw",
      ]),
    );
  });
});

describe("scanDist", () => {
  it("reports prototype-only paths and pattern hits, and nothing for a clean folder", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "leak-scan-"));
    try {
      fs.mkdirSync(path.join(dir, "assets"));
      fs.writeFileSync(path.join(dir, "assets", "index.js"), "app()");
      expect(scanDist(dir)).toEqual({ files: 1, present: [], hits: [] });

      fs.mkdirSync(path.join(dir, "mock", "multimodal"), { recursive: true });
      fs.writeFileSync(
        path.join(dir, "mock", "multimodal", "call.wav"),
        "RIFF",
      );
      fs.writeFileSync(
        path.join(dir, "assets", "page.js"),
        'u.searchParams.set("proto_stage",s)',
      );
      const result = scanDist(dir);
      expect(result.present).toEqual(["mock"]);
      expect(PROTOTYPE_ONLY_PATHS).toContain("prototype-mock-sw.js");
      expect(result.hits).toEqual([
        { pattern: "proto_stage", files: [path.join("assets", "page.js")] },
      ]);
    } finally {
      fs.rmSync(dir, { recursive: true, force: true });
    }
  });
});
