import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import ts from "typescript";

import {
  deduplicateTypeAliases,
  makeObjectPropertiesNullable,
  preserveDefaultLiterals,
} from "./openapi-typescript.mjs";

function diagnostics(source) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "openapi-types-test-"));
  try {
    const file = path.join(dir, "fixture.ts");
    fs.writeFileSync(file, source);
    const program = ts.createProgram([file], {
      strict: true,
      noEmit: true,
      types: [],
      skipLibCheck: true,
    });
    return ts.getPreEmitDiagnostics(program).map((error) => error.code);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

test("shares identical aliases without changing other declarations", () => {
  const source = `export type Params = { limit?: number };
export type Response = { results: string[] };
/** Another route shares the schema. */
export type Params = { limit?: number };
export const route = 'second';`;
  const result = deduplicateTypeAliases(source);
  assert.equal(result.match(/export type Params/g).length, 1);
  assert.ok(result.includes("export type Response"));
  assert.ok(result.includes("export const route"));
  assert.deepEqual(diagnostics(result), []);
});

test("fails on conflicting duplicates, including generic parameters", () => {
  for (const source of [
    "export type Params = { id: string }; export type Params = { id: number };",
    "export type Params<T> = T; export type Params<T extends string> = T;",
  ]) {
    assert.throws(
      () => deduplicateTypeAliases(source),
      /Conflicting generated type alias: Params/,
    );
  }
});

test("rejects invalid syntax rather than repairing partial output", () => {
  assert.throws(
    () => deduplicateTypeAliases("export type Params = {"),
    /invalid generated TypeScript/,
  );
});

test("preserves nested enum defaults without readonly arrays or assertions on schemas", () => {
  const source = `export const runtimeDefault = {
    isolation: 'dedicated_vm', nested: { policy: 'live' },
    domains: ['infrastructure', 'connectivity'], attempts: 2,
  };
  const value: { isolation: 'dedicated_vm'; nested: { policy: 'live' };
    domains: ('infrastructure' | 'connectivity')[]; attempts: number } = runtimeDefault;
  value.domains.push('infrastructure');`;
  assert.ok(diagnostics(source).includes(2322));
  const result = preserveDefaultLiterals(source);
  assert.deepEqual(diagnostics(result), []);
  assert.equal(preserveDefaultLiterals(result), result);
});

test("leaves invalid defaults visible to the compiler", () => {
  const source = `export const invalidDefault = { mode: 'invalid' };
    const value: { mode: 'valid' } = invalidDefault;`;
  assert.ok(diagnostics(preserveDefaultLiterals(source)).includes(2322));
  assert.equal(
    preserveDefaultLiterals("export const arrayDefault = [];"),
    "export const arrayDefault = [];",
  );
});

test("does not narrow unrelated constants or object keys", () => {
  const source = `export const ordinary = { 'mode': 'live' };
export const scalarDefault = 'live';`;
  assert.equal(preserveDefaultLiterals(source), source);
});

test("nullability is scoped to named fields of the selected object", () => {
  const source = `export const Body = zod.object({
    temperature: zod.number(),
    config: zod.object({ temperature: zod.number().optional(), model: zod.string() }).default(configDefault)
  });
  export const Other = zod.object({ config: zod.object({ temperature: zod.number() }) });`;
  const result = makeObjectPropertiesNullable(source, ["Body"], "config", [
    "temperature",
  ]);
  assert.equal(
    result,
    source.replace(
      "zod.number().optional()",
      "zod.number().optional().nullable()",
    ),
  );
  assert.throws(
    () => makeObjectPropertiesNullable(source, ["Missing"], "config", []),
    /Missing generated exports/,
  );
  assert.throws(
    () => makeObjectPropertiesNullable(source, ["Body"], "missing", []),
    /Missing generated property/,
  );
  assert.throws(
    () => makeObjectPropertiesNullable(source, ["Body"], "config", ["missing"]),
    /Missing generated property/,
  );
});
