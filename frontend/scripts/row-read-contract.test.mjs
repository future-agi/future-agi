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

function generated(t, definitions) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "row-read-contract-"));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const schemaPath = path.join(dir, "swagger.json");
  fs.writeFileSync(
    schemaPath,
    JSON.stringify({ swagger: "2.0", paths: {}, definitions }),
  );
  const outputDir = path.join(dir, "generated");
  fs.mkdirSync(outputDir);
  fs.writeFileSync(path.join(outputDir, "api.zod.ts"), ZOD);
  fs.writeFileSync(path.join(outputDir, "api.schemas.ts"), SCHEMAS);
  return { schemaPath, outputDir };
}

test("the OFF contract, without the row read, generates unchanged files", (t) => {
  // DSX_DATASET_ROW_SOURCE_ENABLED unset: the schema omits the row read.
  const { schemaPath, outputDir } = generated(t, {
    RowData: { type: "object", properties: { row_id: { type: "string" } } },
  });
  normalizeRowReadContract(schemaPath, outputDir);
  const read = (name) => fs.readFileSync(path.join(outputDir, name), "utf8");
  assert.equal(read("api.zod.ts"), ZOD);
  assert.equal(read("api.schemas.ts"), SCHEMAS);
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
