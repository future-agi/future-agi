import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { normalizeRowReadContract } from "./row-read-contract.mjs";

const ZOD = [
  "const jsonValueSchema = zod.any();",
  "export const ModelHubDevelopsGetRowDataCreateResponse = zod.object({});",
  "",
].join("\n");
const SCHEMAS = [
  "export interface RowDataApi {",
  "  row_id: string;",
  "}",
  "",
].join("\n");
const ROW_PATH = "/model-hub/datasets/{dataset_id}/rows/{row_id}/";
const ROW_DEFINITIONS = {
  DatasetRowReadResult: {
    type: "object",
    properties: {
      cells: {
        type: "object",
        additionalProperties: { $ref: "#/definitions/DatasetRowReadCell" },
      },
    },
  },
  DatasetRowReadCell: {
    type: "object",
    properties: {
      cell_value: { type: "string", "x-nullable": true },
      value_infos: { type: "object", "x-json-value": true },
      feedback_info: { type: "object", "x-json-value": true },
    },
  },
  DatasetRowReadCellMetadata: {
    type: "object",
    properties: {
      response_time_ms: { type: "number", "x-nullable": true },
      token_count: { type: "integer", "x-nullable": true },
      annotation: { type: "string", "x-nullable": true },
    },
  },
};
const ROW_ZOD = [
  ZOD,
  "export const ModelHubDatasetsRowsReadResponse = zod.object({",
  "  result: zod.object({",
  "    cells: zod.record(zod.object({",
  "      cell_value: zod.string(),",
  "      metadata: zod.object({",
  "        response_time_ms: zod.number(),",
  "        token_count: zod.number(),",
  "        annotation: zod.string(),",
  "      }),",
  "      value_infos: zod.object({}).passthrough(),",
  "      feedback_info: zod.object({}).passthrough(),",
  "    })),",
  "  }),",
  "});",
  "",
].join("\n");
const ROW_SCHEMAS = [
  SCHEMAS,
  "export interface DatasetRowReadCellApi {",
  "  cell_value: string;",
  "}",
  "export interface DatasetRowReadCellMetadataApi {",
  "  response_time_ms: number;",
  "  token_count: number;",
  "  annotation: string;",
  "}",
  "export type DatasetRowReadCellApiValueInfos = { [key: string]: unknown };",
  "export type DatasetRowReadCellApiFeedbackInfo = { [key: string]: unknown };",
  "",
].join("\n");

function generated(
  t,
  definitions,
  { paths = {}, zod = ZOD, schemas = SCHEMAS } = {},
) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "row-read-contract-"));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const schemaPath = path.join(dir, "swagger.json");
  fs.writeFileSync(
    schemaPath,
    JSON.stringify({ swagger: "2.0", paths, definitions }),
  );
  const outputDir = path.join(dir, "generated");
  fs.mkdirSync(outputDir);
  fs.writeFileSync(path.join(outputDir, "api.zod.ts"), zod);
  fs.writeFileSync(path.join(outputDir, "api.schemas.ts"), schemas);
  return { schemaPath, outputDir };
}

test("an explicitly disabled row read leaves generated files unchanged", (t) => {
  const { schemaPath, outputDir } = generated(t, {
    RowData: { type: "object", properties: { row_id: { type: "string" } } },
  });
  normalizeRowReadContract(schemaPath, outputDir);
  const read = (name) => fs.readFileSync(path.join(outputDir, name), "utf8");
  assert.equal(read("api.zod.ts"), ZOD);
  assert.equal(read("api.schemas.ts"), SCHEMAS);
});

test("the default row read preserves nullable and JSON response fields", (t) => {
  const { schemaPath, outputDir } = generated(t, ROW_DEFINITIONS, {
    paths: { [ROW_PATH]: { get: { responses: {} } } },
    zod: ROW_ZOD,
    schemas: ROW_SCHEMAS,
  });
  normalizeRowReadContract(schemaPath, outputDir);
  const read = (name) => fs.readFileSync(path.join(outputDir, name), "utf8");
  const zod = read("api.zod.ts");
  assert.ok(zod.startsWith(ZOD));
  for (const [field, type] of [
    ["cell_value", "string"],
    ["response_time_ms", "number"],
    ["token_count", "number"],
    ["annotation", "string"],
  ]) {
    assert.ok(zod.includes(`${field}: zod.${type}().nullable()`), field);
    assert.ok(
      read("api.schemas.ts").includes(`${field}: ${type} | null;`),
      field,
    );
  }
  for (const field of ["value_infos", "feedback_info"]) {
    assert.ok(
      zod.includes(
        `${field}: jsonValueSchema.describe("Any valid JSON value.")`,
      ),
      field,
    );
  }
  assert.ok(read("api.schemas.ts").startsWith(SCHEMAS));
  assert.equal(read("api.schemas.ts").match(/= JsonValueApi;/g)?.length, 2);
});

test("a partial row read schema still fails generation", (t) => {
  const { schemaPath, outputDir } = generated(t, {
    DatasetRowReadResult: { type: "object", properties: {} },
  });
  assert.throws(
    () => normalizeRowReadContract(schemaPath, outputDir),
    /DatasetRowReadResult\.cells must map column ids to DatasetRowReadCell/,
  );
});
