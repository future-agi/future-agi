import assert from "node:assert/strict";
import test from "node:test";

import { buildManagementApiSwagger } from "./openapi-client-schema.mjs";

function fixture() {
  return {
    swagger: "2.0",
    info: { title: "Fixture", version: "1" },
    paths: {
      "/agentcc/managed-credits/auto-reload/trigger/": {
        post: {
          operationId: "agentcc_managed-credits_auto-reload_trigger_create",
          responses: { 202: { description: "Queued" } },
        },
      },
      "/settings/": {
        parameters: [{ name: "id", in: "path", type: "string" }],
        put: {
          parameters: [
            { in: "body", schema: { $ref: "#/definitions/Settings" } },
          ],
          responses: { 200: { description: "Saved" } },
        },
      },
    },
    definitions: {
      Settings: {
        type: "object",
        properties: { description: { type: "string" } },
      },
      Legacy: {
        type: "object",
        properties: { config: { $ref: "#/definitions/Config" } },
        "x-client-retained": true,
      },
      Config: { type: "object" },
      Orphan: { type: "object" },
    },
  };
}

test("keeps explicitly retained definitions and transitive dependencies", () => {
  const source = fixture();
  const before = structuredClone(source);
  const result = buildManagementApiSwagger(source);
  assert.deepEqual(Object.keys(result.definitions), [
    "Settings",
    "Legacy",
    "Config",
  ]);
  assert.deepEqual(result.definitions.Legacy, source.definitions.Legacy);
  assert.deepEqual(result.paths, { "/settings/": source.paths["/settings/"] });
  assert.deepEqual(source, before);
});

test("does not retain unmarked or non-boolean compatibility definitions", () => {
  for (const marker of [undefined, false, "true"]) {
    const source = fixture();
    source.definitions.Legacy["x-client-retained"] = marker;
    assert.deepEqual(
      Object.keys(buildManagementApiSwagger(source).definitions),
      ["Settings"],
    );
  }
});

test("keeps the replacement as the active body, never the compatibility type", () => {
  const result = buildManagementApiSwagger(fixture());
  assert.equal(
    result.paths["/settings/"].put.parameters[0].schema.$ref,
    "#/definitions/Settings",
  );
});

test("resolves retained cyclic dependencies once", () => {
  const source = fixture();
  source.definitions.Config.properties = {
    parent: { $ref: "#/definitions/Legacy" },
  };
  assert.deepEqual(Object.keys(buildManagementApiSwagger(source).definitions), [
    "Settings",
    "Legacy",
    "Config",
  ]);
});

test("supports a retained definition even with no selected operations", () => {
  const source = fixture();
  delete source.paths["/settings/"];
  assert.deepEqual(Object.keys(buildManagementApiSwagger(source).definitions), [
    "Legacy",
    "Config",
  ]);
});
