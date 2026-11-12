import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import process from "node:process";
import { describe, expect, it } from "vitest";

import { buildManagementApiSwagger } from "../../../../scripts/openapi-client-schema.mjs";

const TRIGGER_ID = "agentcc_managed-credits_auto-reload_trigger_create";
const METHODS = new Set([
  "get", "put", "post", "delete", "options", "head", "patch", "trace",
]);
const swagger = JSON.parse(
  readFileSync(resolve(process.cwd(), "../api_contracts/openapi/swagger.json"), "utf8"),
);
const operations = (schema) =>
  Object.entries(schema.paths).flatMap(([path, item]) =>
    Object.entries(item)
      .filter(([method]) => METHODS.has(method))
      .map(([method, operation]) => ({ path, method, operation })),
  );

// Read the regenerated contract, not a synthetic trigger added by this test.
describe("generated customer client selection", () => {
  it("requires the excluded money-trigger operation to exist in Swagger", () => {
    expect(operations(swagger).filter(({ operation }) => operation.operationId === TRIGGER_ID))
      .toHaveLength(1);
  });

  it("excludes the Owner/Admin money trigger from client-generator input", () => {
    const selected = buildManagementApiSwagger(swagger);
    expect(operations(selected).map(({ operation }) => operation.operationId))
      .not.toContain(TRIGGER_ID);
    expect(selected.paths["/agentcc/managed-credits/auto-reload/trigger/"])
      .toBeUndefined();
  });

  it("preserves every other regenerated operation without mutating the inventory", () => {
    const before = structuredClone(swagger);
    const selected = buildManagementApiSwagger(swagger);
    expect(operations(selected)).toEqual(
      operations(swagger).filter(({ operation }) => operation.operationId !== TRIGGER_ID),
    );
    expect(swagger).toEqual(before);
  });

  it.each(["missing", "renamed"])("fails closed when the excluded operation is %s", (kind) => {
    const changed = structuredClone(swagger);
    for (const { path, method, operation } of operations(changed)) {
      if (operation.operationId !== TRIGGER_ID) continue;
      if (kind === "missing") delete changed.paths[path][method];
      else operation.operationId = `${TRIGGER_ID}_renamed`;
    }
    expect(() => buildManagementApiSwagger(changed)).toThrow(TRIGGER_ID);
  });
});
