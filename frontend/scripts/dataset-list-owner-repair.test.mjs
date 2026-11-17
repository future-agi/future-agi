import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

// Exercise the generator's actual named-block helper and F44 post-processing
// without importing Orval or running a generator as a side effect.
const source = fs.readFileSync(
  new URL("./generate-openapi-client.mjs", import.meta.url),
  "utf8",
);
function repair(input, kind) {
  const helperStart = source.indexOf("  function assertReplaceInNamedBlock(");
  const helperEnd = source.indexOf(
    "  function assertReplaceRegexInNamedBlock(",
    helperStart,
  );
  assert.ok(helperStart >= 0 && helperEnd > helperStart);
  const startMarker = `// BEGIN F44 optional owners ${kind}`;
  const endMarker = `// END F44 optional owners ${kind}`;
  const start = source.indexOf(startMarker);
  const end = source.indexOf(endMarker, start);
  assert.ok(start >= 0 && end > start, `missing ${kind} repair`);
  const name = kind === "types" ? "schemas" : "zod";
  return vm.runInNewContext(
    source.slice(helperStart, helperEnd) +
      `\nlet ${name} = input;\n` +
      source.slice(start + startMarker.length, end) +
      `\n${name};`,
    { input },
    { timeout: 1000 },
  );
}

const types = `export interface DatasetListItemApi {
  created_by?: string;
  created_by_name?: string;
  folder_id: string;
}
export interface UnrelatedApi {
  created_by?: string;
  folder_id: string;
}
`;
const zod = `export const ModelHubDevelopsGetDatasetsListResponse = zod.object({
  "created_by": zod.string().uuid().optional(),
  "created_by_name": zod.string().optional(),
  "folder_id": zod.string().uuid(),
});
export const UnrelatedResponse = zod.object({
  "created_by": zod.string().uuid().optional(),
  "folder_id": zod.string().uuid(),
});
`;

for (const [kind, fixture, declaration] of [
  ["types", types, "export interface DatasetListItemApi {"],
  [
    "zod",
    zod,
    "export const ModelHubDevelopsGetDatasetsListResponse = zod.object({",
  ],
]) {
  test(`${kind} repair adds nullable while preserving optional and unrelated blocks`, () => {
    const output = repair(fixture, kind);
    if (kind === "types") {
      assert.ok(output.includes("created_by?: string | null;"));
      assert.ok(output.includes("created_by_name?: string | null;"));
    } else {
      assert.ok(
        output.includes(
          '"created_by": zod.string().uuid().nullable().optional(),',
        ),
      );
      assert.ok(
        output.includes(
          '"created_by_name": zod.string().nullable().optional(),',
        ),
      );
    }
    const tail = fixture.slice(fixture.indexOf("\nexport "));
    assert.ok(output.endsWith(tail));
  });
  test(`${kind} repair makes folder_id nullable without making it optional`, () => {
    const output = repair(fixture, kind);
    assert.ok(
      output.includes(
        kind === "types"
          ? "folder_id: string | null;"
          : '"folder_id": zod.string().uuid().nullable(),',
      ),
    );
    assert.ok(output.endsWith(fixture.slice(fixture.indexOf("\nexport "))));
  });
  test(`${kind} repair fails loudly when folder_id is absent, optional or not a UUID string`, () => {
    const anchor =
      kind === "types"
        ? "folder_id: string;"
        : '"folder_id": zod.string().uuid(),';
    const malformed =
      kind === "types"
        ? ["", "folder_id?: string;", "folder_id: number;"]
        : [
            "",
            '"folder_id": zod.string().uuid().optional(),',
            '"folder_id": zod.string(),',
          ];
    for (const replacement of malformed) {
      assert.throws(
        () => repair(fixture.replace(anchor, replacement), kind),
        /block anchor/,
      );
    }
  });
  test(`${kind} repair fails loudly when the declaration is missing`, () => {
    assert.throws(
      () =>
        repair(
          fixture.replace(declaration, declaration.replace("Dataset", "Other")),
          kind,
        ),
      /declaration/,
    );
  });
  for (const field of ["created_by", "created_by_name"]) {
    test(`${kind} repair fails loudly when ${field} is absent or unexpectedly required`, () => {
      assert.throws(
        () => repair(fixture.replace(field, "removed_field"), kind),
        /block anchor/,
      );
      const required =
        kind === "types"
          ? fixture.replace(`${field}?:`, `${field}:`)
          : fixture.replace(
              field === "created_by"
                ? "uuid().optional()"
                : '"created_by_name": zod.string().optional()',
              field === "created_by"
                ? "uuid()"
                : '"created_by_name": zod.string()',
            );
      assert.throws(() => repair(required, kind), /block anchor/);
    });
  }
}
