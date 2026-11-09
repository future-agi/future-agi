/* eslint-env node */
/* eslint-disable no-console */
import fs from "node:fs";
import path from "node:path";

// Orval drops Swagger 2.0 x-nullable and narrows x-json-value to an object.
// The row read returns null cell values and arbitrary JSON cell details
// (value_infos is often a list), so its generated response schema is widened
// to exactly what the serializer declares. Every anchor comes from the source
// schema and must match once, so a renamed field fails generation instead of
// silently narrowing the validator. Remove when the generator handles both
// extensions natively.
const RESPONSE = "export const ModelHubDatasetsRowsReadResponse =";
const CELL = "DatasetRowReadCell";
const CELL_METADATA = "DatasetRowReadCellMetadata";
const RESULT = "DatasetRowReadResult";

function readFieldKinds(schemaPath) {
  const schema = JSON.parse(fs.readFileSync(schemaPath, "utf8"));
  const definitions = schema.definitions || {};
  const cells = definitions[RESULT]?.properties?.cells;
  if (cells?.additionalProperties?.$ref !== `#/definitions/${CELL}`) {
    throw new Error(`${RESULT}.cells must map column ids to ${CELL}`);
  }
  const nullable = [];
  const json = [];
  for (const name of [CELL, CELL_METADATA]) {
    const properties = definitions[name]?.properties;
    if (!properties) throw new Error(`Row read schema lacks ${name}`);
    for (const [key, property] of Object.entries(properties)) {
      if (property["x-json-value"]) json.push(key);
      else if (property["x-nullable"]) nullable.push(key);
    }
  }
  if (!nullable.length || !json.length) {
    throw new Error("Row read schema declares no nullable or JSON cell fields");
  }
  return { nullable, json };
}

function exportBlock(text, declaration) {
  const start = text.indexOf(declaration);
  if (start < 0)
    throw new Error(`Missing generated declaration: ${declaration}`);
  const next = text.indexOf("\nexport ", start + declaration.length);
  return { start, end: next < 0 ? text.length : next };
}

const CLOSERS = { ")": "(", "]": "[", "}": "{" };

// The end of the expression that starts at `from`: the first comma or closing
// bracket outside any bracket or string literal opened after `from`.
function expressionEnd(text, from) {
  const stack = [];
  let quote = null;
  for (let i = from; i < text.length; i += 1) {
    const ch = text[i];
    if (quote) {
      if (ch === "\\") i += 1;
      else if (ch === quote) quote = null;
      continue;
    }
    if (ch === '"' || ch === "'" || ch === "`") quote = ch;
    else if (ch === "(" || ch === "[" || ch === "{") stack.push(ch);
    else if (CLOSERS[ch]) {
      if (!stack.length) return i;
      stack.pop();
    } else if (ch === "," && !stack.length) return i;
  }
  throw new Error("Unterminated generated expression");
}

function propertyExpressions(block, key) {
  const found = [];
  const pattern = new RegExp(`(["']?)\\b${key}\\1\\s*:\\s*`, "g");
  for (const match of block.matchAll(pattern)) {
    const start = match.index + match[0].length;
    let end = expressionEnd(block, start);
    while (end > start && /\s/.test(block[end - 1])) end -= 1;
    found.push({ start, end, text: block.slice(start, end) });
  }
  return found;
}

function rewriteBlock(block, { nullable, json }, label) {
  const edits = [];
  for (const key of nullable) {
    const hits = propertyExpressions(block, key);
    if (hits.length !== 1) {
      throw new Error(`${label}: expected one ${key}, found ${hits.length}`);
    }
    const [hit] = hits;
    if (/\.(nullable|nullish)\(\)$/.test(hit.text)) continue;
    const text = hit.text.endsWith(".optional()")
      ? `${hit.text.slice(0, -".optional()".length)}.nullish()`
      : `${hit.text}.nullable()`;
    edits.push({ ...hit, replacement: text });
  }
  for (const key of json) {
    const hits = propertyExpressions(block, key);
    if (hits.length !== 1 || !hits[0].text.includes(".passthrough()")) {
      throw new Error(`${label}: expected one object-only ${key}`);
    }
    edits.push({
      ...hits[0],
      replacement: `jsonValueSchema.describe("Any valid JSON value.")`,
    });
  }
  edits.sort((a, b) => b.start - a.start);
  let out = block;
  for (const edit of edits) {
    out = out.slice(0, edit.start) + edit.replacement + out.slice(edit.end);
  }
  return out;
}

// The row read is in the schema only while its server switch
// (DSX_DATASET_ROW_SOURCE_ENABLED) is on, and the checked-in contract is
// generated with it off. With none of its definitions there is nothing to
// widen; a partial set still fails generation in readFieldKinds.
function rowReadInSchema(schemaPath) {
  const schema = JSON.parse(fs.readFileSync(schemaPath, "utf8"));
  const definitions = schema.definitions || {};
  return [RESULT, CELL, CELL_METADATA].some((name) => name in definitions);
}

export function normalizeRowReadContract(schemaPath, outputDir) {
  if (!rowReadInSchema(schemaPath)) return;
  const fields = readFieldKinds(schemaPath);

  const zodPath = path.join(outputDir, "api.zod.ts");
  const zod = fs.readFileSync(zodPath, "utf8");
  if (!zod.includes("const jsonValueSchema")) {
    throw new Error("api.zod.ts lacks the shared jsonValueSchema");
  }
  const { start, end } = exportBlock(zod, RESPONSE);
  fs.writeFileSync(
    zodPath,
    zod.slice(0, start) +
      rewriteBlock(zod.slice(start, end), fields, "row read zod") +
      zod.slice(end),
  );

  // The TypeScript types only describe the same values for editors; they do
  // not validate, so a shape orval may emit differently is reported, not fatal.
  const schemasPath = path.join(outputDir, "api.schemas.ts");
  let schemas = fs.readFileSync(schemasPath, "utf8");
  let changed = 0;
  for (const name of [CELL, CELL_METADATA]) {
    const declaration = `export interface ${name}Api {`;
    if (!schemas.includes(declaration)) continue;
    const block = exportBlock(schemas, declaration);
    let body = schemas.slice(block.start, block.end);
    for (const key of fields.nullable) {
      const next = body.replace(
        new RegExp(`(\\n\\s+${key}\\??: )([^;\\n]+);`),
        (all, head, type) =>
          type.includes("null") ? all : `${head}${type} | null;`,
      );
      if (next !== body) changed += 1;
      body = next;
    }
    schemas = schemas.slice(0, block.start) + body + schemas.slice(block.end);
  }
  const aliases = new RegExp(
    `(export type ${CELL}Api\\w+ = )\\{ \\[key: string\\]: unknown \\};`,
    "g",
  );
  schemas = schemas.replace(aliases, (all, head) => {
    changed += 1;
    return `${head}JsonValueApi;`;
  });
  fs.writeFileSync(schemasPath, schemas);
  if (changed !== fields.nullable.length + fields.json.length) {
    console.warn(
      `row read TS types: rewrote ${changed} of ${
        fields.nullable.length + fields.json.length
      } fields`,
    );
  }
}
