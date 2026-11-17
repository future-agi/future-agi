// SRC-S1b-HTTP-FE-05: the HTTP mutation request fields (row_ids on the
// append PUT, acknowledge_http_side_effects on rerun-cells) agree across
// Swagger, the runtime request contract and Zod. Authored RED against
// e0d9585e, whose strict request contract refused both fields.
// r3 (FE-16): the Add new rows preview (preview=true on the same PUT) and its
// answer {preview, new_row_ids, request_count, held_count, limit} are
// declared everywhere too, so the strict request and response checks admit
// them. RED against e0d9585e and r2: no preview field, and the PUT answer
// required the experiment's name.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import process from "node:process";
import { describe, expect, it } from "vitest";
import { OPENAPI_CONTRACT } from "src/api/contracts/openapi-contract.generated";
import {
  validateContractedRequestConfig,
  validateContractedResponse,
} from "src/api/contracts/openapi-contract";
import { strictViolations } from "src/api/contracts/strictBody";
import {
  ModelHubExperimentsV2RerunCellsCreateBody,
  ModelHubExperimentsV2UpdateBody,
  ModelHubExperimentsV2UpdateResponse,
} from "src/generated/api-contracts/api.zod";

const snapshot = JSON.parse(
  readFileSync(
    resolve(process.cwd(), "../api_contracts/openapi/swagger.json"),
    "utf8",
  ),
);
const EXP = "4f1e0c52-1a2b-4c3d-8e9f-0a1b2c3d4e50";
const ROW = "4f1e0c52-1a2b-4c3d-8e9f-0a1b2c3d4e51";
const COLUMN = "4f1e0c52-1a2b-4c3d-8e9f-0a1b2c3d4e52";
const SOURCE = "4f1e0c52-1a2b-4c3d-8e9f-0a1b2c3d4e53";
const PREVIEW = {
  status: true,
  result: { preview: true, new_row_ids: [ROW], request_count: 2, held_count: 1, limit: 10000 },
};

describe("HTTP mutation contracts stay aligned", () => {
  it.each([
    "ExperimentUpdateV2",
    "ExperimentRerunCells",
    "ExperimentUpdateV2Response",
    "ExperimentUpdateV2Result",
    "HttpAppendRowsPreview",
  ])("%s is the same in Swagger and the runtime contract", (name) => {
    expect(snapshot.definitions[name]).toBeTruthy();
    expect(OPENAPI_CONTRACT.definitions[name]).toEqual(snapshot.definitions[name]);
  });

  it("retains the standalone preview schema from the backend serializer", () => {
    const preview = snapshot.definitions.HttpAppendRowsPreview;
    expect(preview["x-client-retained"]).toBe(true);
    expect(preview.required).toEqual([
      "preview", "new_row_ids", "request_count", "held_count", "limit",
    ]);
    for (const [name, field] of Object.entries(preview.properties)) {
      expect(snapshot.definitions.ExperimentUpdateV2Result.properties[name])
        .toEqual(field);
    }
  });

  it("declares row_ids for HTTP append and the side-effect acknowledgement for retries", () => {
    expect(snapshot.definitions.ExperimentUpdateV2.properties.row_ids).toMatchObject({
      type: "array", items: { type: "string", format: "uuid" }, maxItems: 10000,
    });
    expect(snapshot.definitions.ExperimentRerunCells.properties.acknowledge_http_side_effects)
      .toMatchObject({ type: "boolean" });
  });

  it("SRC-S1b-HTTP-FE-16 declares the Add new rows preview on the PUT request and its answer", () => {
    const update = snapshot.definitions.ExperimentUpdateV2.properties;
    expect(update.preview).toMatchObject({ type: "boolean", default: false });
    // An empty candidate list is allowed for a preview.
    expect(update.row_ids).not.toHaveProperty("minItems");
    const put = snapshot.paths["/model-hub/experiments/v2/{experiment_id}/"];
    expect(put.put.responses["200"].schema).toEqual({
      $ref: "#/definitions/ExperimentUpdateV2Response",
    });
    // Reading an experiment keeps its detail answer.
    expect(put.get.responses["200"].schema).toEqual({
      $ref: "#/definitions/ExperimentV2DetailResponse",
    });
    const result = snapshot.definitions.ExperimentUpdateV2Result;
    expect(result).not.toHaveProperty("required");
    expect(result.properties).toMatchObject({
      preview: { type: "boolean" },
      new_row_ids: { type: "array", items: { type: "string", format: "uuid" }, maxItems: 10000 },
      request_count: { type: "integer", minimum: 0 },
      held_count: { type: "integer", minimum: 0, maximum: 10000 },
      limit: { type: "integer", minimum: 10000, maximum: 10000 },
    });
    expect(Object.keys(result.properties)).toEqual([
      ...Object.keys(snapshot.definitions.ExperimentDetailV2.properties),
      "preview", "new_row_ids", "request_count", "held_count", "limit",
    ]);
    expect(OPENAPI_CONTRACT.endpoints["/model-hub/experiments/v2/{experiment_id}/"].put.responses[200])
      .toEqual({ $ref: "#/definitions/ExperimentUpdateV2Response" });
  });

  it("Zod keeps both fields and bounds the rows", () => {
    expect(ModelHubExperimentsV2UpdateBody.parse({ append_only: true, row_ids: [ROW] }))
      .toEqual({ append_only: true, preview: false, row_ids: [ROW] });
    expect(ModelHubExperimentsV2UpdateBody.safeParse({ append_only: true, row_ids: ["row-1"] }).success)
      .toBe(false);
    expect(ModelHubExperimentsV2UpdateBody.safeParse({
      append_only: true, row_ids: Array.from({ length: 10001 }, () => ROW),
    }).success).toBe(false);
    const retry = ModelHubExperimentsV2RerunCellsCreateBody.parse({
      cells: [{ column_id: COLUMN, row_id: ROW }], acknowledge_http_side_effects: true,
    });
    expect(retry.acknowledge_http_side_effects).toBe(true);
    expect(retry.cells).toEqual([{ column_id: COLUMN, row_id: ROW }]);
  });

  it("SRC-S1b-HTTP-FE-16 Zod keeps the preview request and its answer", () => {
    expect(ModelHubExperimentsV2UpdateBody.parse({ append_only: true, preview: true, row_ids: [] }))
      .toEqual({ append_only: true, preview: true, row_ids: [] });
    expect(ModelHubExperimentsV2UpdateResponse.parse(PREVIEW)).toEqual(PREVIEW);
    expect(ModelHubExperimentsV2UpdateResponse.safeParse({
      status: true, result: { ...PREVIEW.result, held_count: 10001 },
    }).success).toBe(false);
  });

  it.each([
    ["put", `/model-hub/experiments/v2/${EXP}/`, { append_only: true, row_ids: [ROW] }],
    ["put", `/model-hub/experiments/v2/${EXP}/`, { append_only: true, preview: true, row_ids: [ROW] }],
    ["put", `/model-hub/experiments/v2/${EXP}/`, { append_only: true, preview: true, row_ids: [] }],
    ["post", `/model-hub/experiments/v2/${EXP}/rerun-cells/`, {
      cells: [{ column_id: COLUMN, row_id: ROW }], acknowledge_http_side_effects: true,
    }],
    ["post", `/model-hub/experiments/v2/${EXP}/rerun-cells/`, {
      source_ids: [SOURCE], failed_only: true, acknowledge_http_side_effects: true,
    }],
  ])("the strict request contract admits the %s HTTP mutation body", (method, url, data) => {
    const result = validateContractedRequestConfig({ method, url, data });
    expect(result.skipped).toBeFalsy();
    expect(result.ok).toBe(true);
    // The strict route refuses an undeclared top-level key with a 400.
    expect(strictViolations(method, url, data)).toEqual([]);
  });

  it.each([
    ["the preview answer", PREVIEW],
    ["the experiment detail", { status: true, result: { name: "Orders", http_configs: [] } }],
  ])("SRC-S1b-HTTP-FE-16 the strict response contract admits %s", (_label, data) => {
    const result = validateContractedResponse({
      config: { method: "put", url: `/model-hub/experiments/v2/${EXP}/` },
      status: 200,
      data,
    });
    expect(result.skipped).toBeFalsy();
    expect(result.ok).toBe(true);
  });
});
