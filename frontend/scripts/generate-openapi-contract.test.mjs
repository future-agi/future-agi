import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import vm from "node:vm";
import test from "node:test";

const scripts = path.dirname(fileURLToPath(import.meta.url));
const generator =
  process.env.RUNTIME_GENERATOR_SOURCE ||
  path.join(scripts, "generate-openapi-contract.mjs");

function generate(swagger) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "runtime-contract-test-"));
  try {
    fs.mkdirSync(path.join(root, "api_contracts/openapi"), { recursive: true });
    fs.mkdirSync(path.join(root, "frontend/scripts"), { recursive: true });
    fs.writeFileSync(
      path.join(root, "api_contracts/openapi/swagger.json"),
      JSON.stringify(swagger),
    );
    const script = path.join(root, "frontend/scripts/generate.mjs");
    fs.copyFileSync(generator, script);
    fs.symlinkSync(
      path.resolve(scripts, "../node_modules"),
      path.join(root, "node_modules"),
    );
    execFileSync(process.execPath, [script], { stdio: "pipe" });
    const source = fs.readFileSync(
      path.join(
        root,
        "frontend/src/api/contracts/openapi-contract.generated.js",
      ),
      "utf8",
    );
    return JSON.parse(
      vm.runInNewContext(
        source.replace(
          "export const OPENAPI_CONTRACT",
          "const OPENAPI_CONTRACT",
        ) + "\nJSON.stringify(OPENAPI_CONTRACT)",
        {},
        { timeout: 1000 },
      ),
    );
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
}

function fixture() {
  return {
    swagger: "2.0",
    paths: {
      "/settings/": {
        put: {
          parameters: [
            {
              in: "body",
              schema: { $ref: "#/definitions/DatasetSettingsRequest" },
            },
          ],
          responses: { 200: { schema: { $ref: "#/definitions/Response" } } },
        },
      },
    },
    definitions: {
      DatasetSettingsRequest: { type: "object" },
      Response: { type: "object" },
      DatasetBehaviorRequest: {
        type: "object",
        properties: { config: { $ref: "#/definitions/Config" } },
        "x-client-retained": true,
      },
      Config: { type: "object" },
      Orphan: { type: "object" },
    },
  };
}

test("runtime generator retains compatibility definitions and transitive refs", () => {
  const source = fixture();
  const result = generate(source);
  assert.deepEqual(Object.keys(result.definitions).sort(), [
    "Config",
    "DatasetBehaviorRequest",
    "DatasetSettingsRequest",
    "Response",
  ]);
  for (const [name, definition] of Object.entries(result.definitions)) {
    assert.deepEqual(definition, source.definitions[name]);
  }
});

test("runtime generator ignores unmarked and non-boolean retention", () => {
  for (const marker of [undefined, false, "true", 1]) {
    const source = fixture();
    source.definitions.DatasetBehaviorRequest["x-client-retained"] = marker;
    assert.deepEqual(Object.keys(generate(source).definitions).sort(), [
      "DatasetSettingsRequest",
      "Response",
    ]);
  }
});

test("runtime generator leaves the current endpoint body unchanged", () => {
  const result = generate(fixture());
  assert.deepEqual(result.endpoints["/settings/"].put.requestBody, {
    $ref: "#/definitions/DatasetSettingsRequest",
  });
});

test("runtime generator terminates retained cyclic reference traversal", () => {
  const source = fixture();
  source.definitions.Config.properties = {
    parent: { $ref: "#/definitions/DatasetBehaviorRequest" },
  };
  const result = generate(source);
  assert.deepEqual(result.definitions.Config, source.definitions.Config);
  assert.ok(result.definitions.DatasetBehaviorRequest);
  assert.equal(Object.keys(result.definitions).length, 4);
});

test("runtime generator retains definitions without any endpoint", () => {
  const source = fixture();
  source.paths = {};
  assert.deepEqual(Object.keys(generate(source).definitions).sort(), [
    "Config",
    "DatasetBehaviorRequest",
  ]);
});

test("runtime generator preserves query-only and nested response references", () => {
  const source = fixture();
  source.paths["/settings/"].put.parameters.push({
    in: "query",
    name: "ids",
    type: "array",
    items: { $ref: "#/definitions/QueryItem" },
  });
  source.definitions.QueryItem = { type: "string" };
  source.definitions.Response.properties = {
    items: { type: "array", items: { $ref: "#/definitions/Nested" } },
  };
  source.definitions.Nested = { type: "number" };
  const result = generate(source);
  assert.deepEqual(result.definitions.QueryItem, source.definitions.QueryItem);
  assert.deepEqual(result.definitions.Nested, source.definitions.Nested);
});
