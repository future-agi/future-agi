import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import {
  checkLiveEvidenceDocs,
  extractEvidenceArtifactRefs,
} from "./check-live-evidence-docs.mjs";

describe("live evidence docs checker", () => {
  const cleanup = [];

  afterEach(async () => {
    for (const item of cleanup.splice(0).reverse()) {
      await fs.rm(item, { force: true, recursive: true });
    }
  });

  it("extracts concrete /tmp artifacts from evidence text", () => {
    expect(
      extractEvidenceArtifactRefs(
        "Required preflight and journey artifacts passed: /tmp/preflight.json and --json /tmp/journey-results.json. Screenshot: /tmp/browser.png",
      ),
    ).toEqual([
      "/tmp/browser.png",
      "/tmp/journey-results.json",
      "/tmp/preflight.json",
    ]);
  });

  it("passes artifact-backed and blocked live mentions", async () => {
    const docsRoot = await makeDocsRoot({
      "coverage.csv": [
        "flow_id,status,last_tested,evidence,notes",
        'AQ-API-001,passed,2026-06-01,"passed live against localhost:8003 with /tmp/aq-api-001.json and /tmp/preflight.json",',
        'AQ-API-002,blocked,2026-06-01,"JS journey wired; live local blocked by auth/DB",',
      ].join("\n"),
    });

    const result = await checkLiveEvidenceDocs({ docsRoot });

    expect(result).toMatchObject({
      status: "passed",
      live_claim_row_count: 2,
      artifact_backed_live_claim_row_count: 1,
      blocked_or_static_live_mention_count: 1,
      missing_live_artifact_row_count: 0,
    });
  });

  it("reports missing live artifacts by default and fails in strict mode", async () => {
    const docsRoot = await makeDocsRoot({
      "coverage.csv": [
        "flow_id,status,last_tested,evidence,notes",
        "OBS-API-001,passed,2026-06-01,passed live against localhost:8003,",
      ].join("\n"),
    });

    await expect(checkLiveEvidenceDocs({ docsRoot })).resolves.toMatchObject({
      status: "passed",
      missing_live_artifact_row_count: 1,
      missing_live_artifact_rows: [
        expect.objectContaining({
          file: "coverage.csv",
          line: 2,
          row_id: "OBS-API-001",
        }),
      ],
    });

    await expect(
      checkLiveEvidenceDocs({ docsRoot, strictMissingArtifacts: true }),
    ).resolves.toMatchObject({
      status: "failed",
      missing_live_artifact_row_count: 1,
    });
  });

  it("ignores ordinary verified rows without live-local claims", async () => {
    const docsRoot = await makeDocsRoot({
      "coverage.csv": [
        "flow_id,status,last_tested,evidence,notes",
        "DPE-API-001,API verified,2026-06-01,Backend regression added,",
      ].join("\n"),
    });

    await expect(checkLiveEvidenceDocs({ docsRoot })).resolves.toMatchObject({
      status: "passed",
      live_claim_row_count: 0,
      missing_live_artifact_row_count: 0,
    });
  });

  async function makeDocsRoot(files) {
    const docsRoot = await fs.mkdtemp(
      path.join(os.tmpdir(), "api-live-evidence-docs-"),
    );
    cleanup.push(docsRoot);
    for (const [name, content] of Object.entries(files)) {
      await fs.writeFile(path.join(docsRoot, name), `${content}\n`);
    }
    return docsRoot;
  }
});
