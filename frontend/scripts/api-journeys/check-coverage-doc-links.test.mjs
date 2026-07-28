import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import {
  checkCoverageDocLinks,
  extractProductFeatureIdsFromText,
} from "./check-coverage-doc-links.mjs";

describe("coverage docs link checker", () => {
  const cleanup = [];

  afterEach(async () => {
    for (const item of cleanup.splice(0).reverse()) {
      await fs.rm(item, { force: true, recursive: true });
    }
  });

  it("extracts product-feature ids from coverage text", () => {
    expect(
      extractProductFeatureIdsFromText(
        "PF-001 PF-072 AUD-001 DFE-001 PF-NOT-A-NUMBER",
      ),
    ).toEqual(["PF-001", "PF-072"]);
  });

  it("passes when feature ids are unique and flow files exist", async () => {
    const docsRoot = await makeDocsRoot({
      "06-product-feature-map.csv": [
        "feature_id,feature,sub_feature,current_flow_file",
        "PF-001,dataset,list,01-datasets.csv",
        "PF-002,observe,traces,03-observe.csv;TBD-observe.csv",
      ].join("\n"),
      "01-datasets.csv": "flow,notes\nDPE-001,PF-001\n",
      "03-observe.csv": "flow,notes\nOBS-001,PF-002\n",
      "TBD-observe.csv": "flow,notes\nOBS-002,PF-002\n",
      "README.md": "Feature map covers PF-001 and PF-002.",
    });

    const result = await checkCoverageDocLinks({ docsRoot });

    expect(result).toMatchObject({
      status: "passed",
      product_feature_row_count: 2,
      duplicate_product_feature_id_count: 0,
      invalid_product_feature_id_count: 0,
      missing_current_flow_file_count: 0,
      unknown_product_feature_ref_count: 0,
    });
    expect(result.docs_by_product_feature_id["PF-001"]).toEqual([
      "01-datasets.csv",
      "06-product-feature-map.csv",
      "README.md",
    ]);
  });

  it("fails on duplicate ids, invalid ids, missing flow files, and unknown refs", async () => {
    const docsRoot = await makeDocsRoot({
      "06-product-feature-map.csv": [
        "feature_id,feature,sub_feature,current_flow_file",
        "PF-001,dataset,list,missing.csv",
        "PF-001,dataset,detail,01-datasets.csv",
        "BAD-002,observe,traces,../escape.csv",
      ].join("\n"),
      "01-datasets.csv": "flow,notes\nDPE-001,PF-999\n",
    });

    const result = await checkCoverageDocLinks({ docsRoot });

    expect(result).toMatchObject({
      status: "failed",
      duplicate_product_feature_id_count: 1,
      invalid_product_feature_id_count: 1,
      missing_current_flow_file_count: 2,
      unknown_product_feature_ref_count: 1,
    });
    expect(result.duplicate_product_feature_ids[0]).toMatchObject({
      feature_id: "PF-001",
    });
    expect(result.invalid_product_feature_ids[0]).toMatchObject({
      feature_id: "BAD-002",
      problem: "invalid_product_feature_id",
    });
    expect(result.unknown_product_feature_refs[0]).toMatchObject({
      feature_id: "PF-999",
      files: ["01-datasets.csv"],
    });
  });

  async function makeDocsRoot(files) {
    const docsRoot = await fs.mkdtemp(
      path.join(os.tmpdir(), "coverage-doc-links-"),
    );
    cleanup.push(docsRoot);
    for (const [name, content] of Object.entries(files)) {
      await fs.writeFile(path.join(docsRoot, name), `${content}\n`);
    }
    return docsRoot;
  }
});
