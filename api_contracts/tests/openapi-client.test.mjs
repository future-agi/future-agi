import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";
import { after, test } from "node:test";
import { fileURLToPath } from "node:url";

import { normalizeSwaggerSchema } from "../../frontend/scripts/generate-openapi-client.mjs";

const root = fileURLToPath(new URL("../../", import.meta.url));
const require = createRequire(path.join(root, "frontend/package.json"));
const { buildSync } = require("esbuild");
const temp = fs.mkdtempSync(path.join(os.tmpdir(), "openapi-client-test-"));
after(() => fs.rmSync(temp, { recursive: true, force: true }));
buildSync({
  entryPoints: [
    path.join(root, "frontend/src/generated/api-contracts/api.zod.ts"),
  ],
  bundle: true,
  platform: "node",
  format: "cjs",
  outfile: path.join(temp, "validators.cjs"),
});
const generated = require(path.join(temp, "validators.cjs"));
const validator = (suffix) => {
  const name = `ModelHubSkills${suffix}`;
  assert.ok(generated[name], `Missing generated validator ${name}`);
  return generated[name];
};

// These are deliberately not Skills schemas: nullability must work everywhere.
for (const type of ["string", "integer", "boolean", "array", "object"]) {
  test(`generic normalization preserves nullable ${type}, including nested fields`, () => {
    const field = { type, "x-nullable": true, description: "Synthetic field" };
    const schema = { type: "object", properties: { value: field } };
    const normalized = normalizeSwaggerSchema(schema);
    assert.deepEqual(normalized.properties.value, {
      type,
      nullable: true,
      description: "Synthetic field",
    });
    assert.equal(schema.properties.value["x-nullable"], true);
    assert.deepEqual(normalizeSwaggerSchema(normalized), normalized);
  });
}

test("nullable parameters put nullability on the schema, not the parameter", () => {
  assert.deepEqual(
    normalizeSwaggerSchema({
      name: "cursor",
      in: "query",
      schema: { type: "string" },
      "x-nullable": true,
    }),
    { name: "cursor", in: "query", schema: { type: "string", nullable: true } },
  );
});

test("nullable named objects use unions so generated TypeScript remains valid", () => {
  const object = { type: "object", properties: { name: { type: "string" } } };
  assert.deepEqual(normalizeSwaggerSchema({ ...object, "x-nullable": true }), {
    anyOf: [object, { type: "null" }],
  });
});

test("recursive JSON values already admit null without changing their alias shape", () => {
  const json = { type: "object", "x-json-value": true };
  assert.deepEqual(
    normalizeSwaggerSchema({ ...json, "x-nullable": true }),
    json,
  );
});

test("normalization preserves explicit unions rather than accepting arbitrary JSON", () => {
  const branches = [
    { type: "integer", minimum: 1 },
    { type: "string", enum: ["draft"] },
  ];
  assert.deepEqual(
    normalizeSwaggerSchema({ type: "object", "x-oneOf": branches }),
    {
      description: undefined,
      oneOf: branches,
    },
  );
});

test("generated draft accepts a null base and rejects non-number bases", () => {
  const payload = {
    status: true,
    result: {
      base_version: null,
      files: [],
      note: "",
      revision: 1,
      updated_at: "2026-11-06T00:00:00Z",
    },
  };
  const schema = validator("DraftListResponse");
  assert.equal(schema.safeParse(payload).success, true);
  assert.equal(
    schema.safeParse({
      ...payload,
      result: { ...payload.result, base_version: "draft" },
    }).success,
    false,
  );
});

test("generated compare accepts exact integer-or-draft refs and nullable hashes", () => {
  const payload = {
    status: true,
    result: {
      from: 1,
      to: "draft",
      files: [
        {
          path: "new.md",
          status: "added",
          from_sha256: null,
          to_sha256: "abc",
          from_text: "",
          to_text: "new",
        },
      ],
      summary: { added: 1, removed: 0, modified: 0, unchanged: 0 },
    },
  };
  const schema = validator("CompareListResponse");
  assert.equal(schema.safeParse(payload).success, true);
  for (const value of [null, {}, [], "1", "latest", 0, -1]) {
    assert.equal(
      schema.safeParse({
        ...payload,
        result: { ...payload.result, from: value },
      }).success,
      false,
      JSON.stringify(value),
    );
  }
});

test("generated publication accepts an optional reviewed revision", () => {
  const schema = validator("DraftPublishCreateBody");
  assert.equal(schema.safeParse({ revision: 2 }).success, true);
  assert.equal(schema.safeParse({}).success, true);
  assert.equal(schema.safeParse({ revision: null }).success, false);
});

test("existing nullable portable unions retain their string and object variants", () => {
  for (const response_format of ["text", { type: "json_object" }, null]) {
    assert.equal(
      generated.ModelHubPromptTemplatesRunTemplateBody.safeParse({
        prompt_config: [{ configuration: { response_format } }],
      }).success,
      true,
    );
  }
});

test("nullable object defaults are preserved when omitted", () => {
  assert.deepEqual(
    generated.TracerEvalTaskCreateBody.shape.filters.parse(undefined),
    {},
  );
  assert.equal(
    generated.TracerEvalTaskCreateBody.shape.filters.parse(null),
    null,
  );
});

test("generated chunk accepts the null end-of-file marker", () => {
  assert.equal(
    validator("VersionsFilesListResponse").safeParse({
      status: true,
      result: {
        path: "SKILL.md",
        sha256: "abc",
        size_bytes: 0,
        total_chars: 0,
        offset: 0,
        next_offset: null,
        eof: true,
        text: "",
      },
    }).success,
    true,
  );
});
