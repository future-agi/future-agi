import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import * as apiZod from "src/generated/api-contracts/api.zod";
import {
  findOpenApiEndpoint,
  validateContractedResponse,
} from "src/api/contracts/openapi-contract";

const DATASET = "00000000-0000-4000-8000-0000000000d1";
const ROW = "00000000-0000-4000-8000-0000000000a1";
const COLUMN = "00000000-0000-4000-8000-0000000000c1";
const CELL = "00000000-0000-4000-8000-0000000000e1";
const ROW_TEMPLATE = "/model-hub/datasets/{dataset_id}/rows/{row_id}/";
const ROW_URL = `/model-hub/datasets/${DATASET}/rows/${ROW}/`;

function rowResponse(valueInfos = null) {
  return {
    status: true,
    result: {
      row_id: ROW,
      order: 0,
      row_meta: {},
      cells: {
        [COLUMN]: {
          cell_id: CELL,
          cell_value: null,
          metadata: {
            response_time_ms: null,
            token_count: null,
            annotation: null,
          },
          status: "",
          value_infos: valueInfos,
          feedback_info: valueInfos,
        },
      },
    },
  };
}

function validate(data) {
  return validateContractedResponse({
    config: { url: ROW_URL, method: "get" },
    status: 200,
    data,
  });
}

describe("the default-on row read response contract", () => {
  it("publishes the generated row response validator and runtime GET", () => {
    expect(apiZod).toHaveProperty("ModelHubDatasetsRowsReadResponse");
    expect(findOpenApiEndpoint(ROW_URL, "get")).toMatchObject({
      template: ROW_TEMPLATE,
      method: "get",
    });
  });

  it.each([
    { value: null },
    { value: [] },
    { value: [1, { nested: [null, "text", false] }] },
    { value: "text" },
    { value: false },
    { value: 7 },
    { value: { nested: [true, null] } },
  ])("accepts nullable cells and JSON details: $value", ({ value }) => {
    const data = rowResponse(value);
    expect(
      apiZod.ModelHubDatasetsRowsReadResponse.safeParse(data).success,
    ).toBe(true);
    const result = validate(data);
    expect(result.ok).toBe(true);
    expect(result.skipped).not.toBe(true);
    expect(result.endpoint.template).toBe(ROW_TEMPLATE);
  });

  it("validates recorded source metadata when present", () => {
    const data = rowResponse();
    data.result.row_meta.source = {
      type: "span",
      label: "Imported span",
      project_id: DATASET,
      trace_id: "trace-one",
      span_id: "span-one",
      expired: false,
    };
    expect(
      apiZod.ModelHubDatasetsRowsReadResponse.safeParse(data).success,
    ).toBe(true);
    expect(validate(data).ok).toBe(true);
  });

  it("rejects an absent response instead of skipping validation", () => {
    expect(
      apiZod.ModelHubDatasetsRowsReadResponse.safeParse(null).success,
    ).toBe(false);
    const result = validate(null);
    expect(result.ok).toBe(false);
    expect(result.skipped).not.toBe(true);
  });

  it("rejects malformed cell values in both validators", () => {
    const data = rowResponse();
    data.result.cells[COLUMN].cell_value = { unexpected: "object" };
    expect(
      apiZod.ModelHubDatasetsRowsReadResponse.safeParse(data).success,
    ).toBe(false);
    expect(validate(data).ok).toBe(false);
  });

  it("declares row read interfaces with nullable TypeScript fields", () => {
    const source = fs.readFileSync(
      path.resolve("src/generated/api-contracts/api.schemas.ts"),
      "utf8",
    );
    expect(source).toMatch(/export\s+interface\s+DatasetRowReadResultApi\b/);
    expect(source).toMatch(/export\s+interface\s+DatasetRowReadCellApi\b/);
    expect(source).toMatch(/cell_value\??:\s*string\s*\|\s*null;/);
    expect(source).toMatch(/response_time_ms\??:\s*number\s*\|\s*null;/);
  });
});
