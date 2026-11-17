// HO S2: the leak scan also catches the prototype label's text, the legacy
// mock worker and the chunks only prototype builds emit.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";

import {
  LEAK_PATTERNS,
  PROTOTYPE_ONLY_CHUNKS,
  PROTOTYPE_ONLY_PATHS,
  prototypeChunksOf,
  scanDist,
} from "../../../scripts/prototype-leak-scan.mjs";

describe("leak scan additions", () => {
  it("lists the label text, the mock worker and the prototype chunk prefixes", () => {
    expect(LEAK_PATTERNS).toContain("Prototype: mock data, resets per browser");
    expect(PROTOTYPE_ONLY_PATHS).toContain("mockServiceWorker.js");
    expect(PROTOTYPE_ONLY_CHUNKS).toEqual(
      expect.arrayContaining([
        "R4Lab-",
        "Leaderboards-",
        "OneViewGallery-",
        "PrototypeLabel-",
      ]),
    );
  });

  it("prototypeChunksOf names only files directly under assets/ with a prefix", () => {
    expect(
      prototypeChunksOf([
        "assets/R4Lab-abc123.js",
        "assets/PrototypeLabel-x.js",
        "assets/index-1.js",
        "assets/LeaderboardsTable-1.js",
        "R4Lab-root.js",
        "assets/nested/R4Lab-1.js",
        path.join("assets", "OneViewGallery-9.js"),
      ]),
    ).toEqual([
      "assets/R4Lab-abc123.js",
      "assets/PrototypeLabel-x.js",
      path.join("assets", "OneViewGallery-9.js"),
    ]);
  });

  it("scanDist reports a prototype chunk, the worker and the label text", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "leak-scan-ho-"));
    try {
      fs.mkdirSync(path.join(dir, "assets"));
      fs.writeFileSync(path.join(dir, "assets", "index-1.js"), "app()");
      expect(scanDist(dir)).toEqual({ files: 1, present: [], hits: [] });

      fs.writeFileSync(path.join(dir, "mockServiceWorker.js"), "// msw");
      fs.writeFileSync(
        path.join(dir, "assets", "Leaderboards-abc.js"),
        "export default 1",
      );
      fs.writeFileSync(
        path.join(dir, "assets", "PrototypeLabel-def.js"),
        'x("Prototype: mock data, resets per browser")',
      );
      const result = scanDist(dir);
      expect(result.present).toEqual([
        "mockServiceWorker.js",
        path.join("assets", "Leaderboards-abc.js"),
        path.join("assets", "PrototypeLabel-def.js"),
      ]);
      expect(result.hits).toEqual([
        {
          pattern: "Prototype: mock data, resets per browser",
          files: [path.join("assets", "PrototypeLabel-def.js")],
        },
      ]);
    } finally {
      fs.rmSync(dir, { recursive: true, force: true });
    }
  });
});
