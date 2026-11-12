import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import * as apiZod from "src/generated/api-contracts/api.zod";
import {
  findOpenApiEndpoint,
  validateContractedResponse,
} from "src/api/contracts/openapi-contract";

// The checked-in contract is generated with DSX_DATASET_ROW_SOURCE_ENABLED OFF.
// The normalizer's OFF/partial-schema fixtures live in
// frontend/scripts/row-read-contract.test.mjs; no ON response shape is claimed here.
const DATASET = "00000000-0000-4000-8000-0000000000d1";
const ROW = "00000000-0000-4000-8000-0000000000a1";
const ROW_URL = `/model-hub/datasets/${DATASET}/rows/${ROW}/`;

describe("row read response contract while row source is OFF", () => {
  it("omits the generated row read response validator", () => {
    expect(apiZod).not.toHaveProperty("ModelHubDatasetsRowsReadResponse");
  });

  it("omits the dataset row GET from the runtime contract", () => {
    expect(findOpenApiEndpoint(ROW_URL, "get")).toBeNull();
  });

  it("skips runtime response validation for the uncontracted GET", () => {
    expect(
      validateContractedResponse({
        config: { url: ROW_URL, method: "get" },
        status: 200,
        data: null,
      }),
    ).toEqual({ ok: true, skipped: true });
  });

  it("declares no dataset row read interfaces for TypeScript consumers", () => {
    const source = fs.readFileSync(
      path.resolve("src/generated/api-contracts/api.schemas.ts"),
      "utf8",
    );
    expect(source).not.toMatch(/export\s+interface\s+DatasetRowRead\w*Api\b/);
  });
});
