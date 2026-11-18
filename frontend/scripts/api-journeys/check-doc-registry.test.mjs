import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import {
  checkJourneyDocsAgainstRegistry,
  extractJourneyIdsFromText,
} from "./check-doc-registry.mjs";

describe("api journey docs registry checker", () => {
  const cleanup = [];

  afterEach(async () => {
    for (const item of cleanup.splice(0).reverse()) {
      await fs.rm(item, { force: true, recursive: true });
    }
  });

  it("extracts only journey-style ids from coverage text", () => {
    expect(
      extractJourneyIdsFromText(
        "AQ-API-001 PUBLIC-AUTH-046 PUBLIC-SYSTEM-047 MCP-OAUTH-001 DFE-123 PF-001 NOT_A_JOURNEY_999",
      ),
    ).toEqual([
      "AQ-API-001",
      "MCP-OAUTH-001",
      "PUBLIC-AUTH-046",
      "PUBLIC-SYSTEM-047",
    ]);
  });

  it("passes when docs and registered journeys match", async () => {
    const docsRoot = await makeDocsRoot({
      "coverage.csv":
        "flow,evidence\nAQ-API-001,covered\nPUBLIC-AUTH-046,guarded\n",
      "guide.md": "Run AQ-API-001 and PUBLIC-AUTH-046.",
    });

    const result = await checkJourneyDocsAgainstRegistry({
      docsRoot,
      registeredJourneys: [
        { id: "AQ-API-001", title: "Annotation" },
        { id: "PUBLIC-AUTH-046", title: "Guard" },
      ],
    });

    expect(result).toMatchObject({
      status: "passed",
      docs_files_scanned: 2,
      docs_id_count: 2,
      registered_id_count: 2,
      missing_in_runner: [],
      missing_in_docs: [],
      duplicate_registered_ids: [],
    });
    expect(result.docs_by_id["AQ-API-001"]).toEqual([
      "coverage.csv",
      "guide.md",
    ]);
  });

  it("fails on docs ids missing from runner, undocumented runner ids, and duplicate registrations", async () => {
    const docsRoot = await makeDocsRoot({
      "coverage.csv": "AQ-API-001,OBS-API-999\n",
    });

    const result = await checkJourneyDocsAgainstRegistry({
      docsRoot,
      registeredJourneys: [
        { id: "AQ-API-001", title: "Annotation" },
        { id: "AQ-API-001", title: "Duplicate" },
        { id: "OBS-API-001", title: "Observe" },
      ],
    });

    expect(result).toMatchObject({
      status: "failed",
      missing_in_runner: ["OBS-API-999"],
      missing_in_docs: ["OBS-API-001"],
      duplicate_registered_ids: ["AQ-API-001"],
    });
  });

  async function makeDocsRoot(files) {
    const docsRoot = await fs.mkdtemp(
      path.join(os.tmpdir(), "api-journey-docs-"),
    );
    cleanup.push(docsRoot);
    for (const [name, content] of Object.entries(files)) {
      await fs.writeFile(path.join(docsRoot, name), content);
    }
    return docsRoot;
  }
});
