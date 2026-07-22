import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import {
  checkBrowserSmokeDocs,
  extractBrowserSmokeRefsFromText,
} from "./check-browser-smoke-docs.mjs";

describe("browser smoke docs checker", () => {
  const cleanup = [];

  afterEach(async () => {
    for (const item of cleanup.splice(0).reverse()) {
      await fs.rm(item, { force: true, recursive: true });
    }
  });

  it("extracts browser smoke filenames from coverage text", () => {
    expect(
      extractBrowserSmokeRefsFromText(
        "Run alerts-smoke.mjs, gateway-deep-smoke.spec.js, and ignore api-journey.mjs.",
      ),
    ).toEqual(["alerts-smoke.mjs", "gateway-deep-smoke.spec.js"]);
  });

  it("passes when referenced browser smokes exist and reports undocumented files", async () => {
    const { browserRoot, docsRoot } = await makeFixture({
      browserFiles: [
        "alerts-smoke.mjs",
        "gateway-deep-smoke.spec.js",
        "undocumented-smoke.mjs",
      ],
      docsFiles: {
        "coverage.csv": "alerts-smoke.mjs,gateway-deep-smoke.spec.js\n",
        "guide.md": "Run alerts-smoke.mjs.",
      },
    });

    const result = await checkBrowserSmokeDocs({ browserRoot, docsRoot });

    expect(result).toMatchObject({
      status: "passed",
      browser_smoke_count: 3,
      docs_ref_count: 2,
      missing_files: [],
      undocumented_smokes: ["undocumented-smoke.mjs"],
    });
    expect(result.docs_by_smoke["alerts-smoke.mjs"]).toEqual([
      "coverage.csv",
      "guide.md",
    ]);
  });

  it("fails when docs reference a missing smoke or strict undocumented mode is enabled", async () => {
    const { browserRoot, docsRoot } = await makeFixture({
      browserFiles: ["alerts-smoke.mjs", "undocumented-smoke.mjs"],
      docsFiles: {
        "coverage.csv": "alerts-smoke.mjs,missing-smoke.mjs\n",
      },
    });

    const missingResult = await checkBrowserSmokeDocs({
      browserRoot,
      docsRoot,
    });
    expect(missingResult).toMatchObject({
      status: "failed",
      missing_files: ["missing-smoke.mjs"],
      undocumented_smokes: ["undocumented-smoke.mjs"],
    });

    const strictResult = await checkBrowserSmokeDocs({
      browserRoot,
      docsRoot,
      strictUndocumented: true,
    });
    expect(strictResult.status).toBe("failed");
  });

  async function makeFixture({ browserFiles, docsFiles }) {
    const root = await fs.mkdtemp(
      path.join(os.tmpdir(), "browser-smoke-docs-"),
    );
    cleanup.push(root);
    const browserRoot = path.join(root, "browser");
    const docsRoot = path.join(root, "docs");
    await fs.mkdir(browserRoot, { recursive: true });
    await fs.mkdir(docsRoot, { recursive: true });

    for (const file of browserFiles) {
      await fs.writeFile(path.join(browserRoot, file), "export {};\n");
    }
    for (const [file, content] of Object.entries(docsFiles)) {
      await fs.writeFile(path.join(docsRoot, file), content);
    }

    return { browserRoot, docsRoot };
  }
});
