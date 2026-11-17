// A split saved without a description reads back with description "".
// The generated response contract must accept that body on every split
// endpoint that returns a split.
import { describe, expect, it } from "vitest";
import { validateContractedResponse } from "src/api/contracts/openapi-contract";

const DATASET = "0b7c5a52-6f0e-4a8e-9a43-2a1f0e1d3c11";
const SPLIT_ID = "4f9d2c1a-7b3e-4c5d-8e6f-0a1b2c3d4e5f";
const ROW_ID = "6d1b1f7e-3c55-4a2b-8e4b-1f6a9c2d7e01";
const split = (description) => ({
  id: SPLIT_ID,
  name: "holdout",
  color: "info",
  description,
  row_count: 1,
});
const response = (method, url, result) => ({
  config: { url, method },
  status: 200,
  data: { status: true, result },
});
const reads = (description) => [
  response("get", `/model-hub/datasets/${DATASET}/splits/`, {
    splits: [split(description)],
    no_split_count: 1,
  }),
  response(
    "post",
    `/model-hub/datasets/${DATASET}/splits/`,
    split(description),
  ),
  response(
    "patch",
    `/model-hub/datasets/${DATASET}/splits/${SPLIT_ID}/`,
    split(description),
  ),
  response(
    "get",
    `/model-hub/datasets/${DATASET}/splits/export/?split_id=${SPLIT_ID}`,
    {
      dataset_id: DATASET,
      split: split(description),
      rows: [{ row_id: ROW_ID, fields: { question: "q", answer: null } }],
    },
  ),
];

describe("split response contract", () => {
  it("accepts a split with a blank description on every split read", () => {
    for (const r of reads("")) {
      const checked = validateContractedResponse(r);
      expect(checked.skipped, `${r.config.method} ${r.config.url}`).not.toBe(
        true,
      );
      expect(
        checked.ok,
        `${r.config.method} ${r.config.url}: ${checked.error?.message ?? ""}`,
      ).toBe(true);
    }
  });

  it("still refuses a split body that breaks the contract", () => {
    for (const r of reads(null)) {
      const checked = validateContractedResponse(r);
      expect(checked.skipped, `${r.config.method} ${r.config.url}`).not.toBe(
        true,
      );
      expect(checked.ok, `${r.config.method} ${r.config.url}`).toBe(false);
    }
  });
});
