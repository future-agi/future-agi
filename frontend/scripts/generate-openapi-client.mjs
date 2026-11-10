/* eslint-env node */
/* eslint-disable no-console */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { generate } from "orval";
import prettier from "prettier";
import { checkGeneratedOutput } from "./lib/check-generated-output.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(__dirname, "..");
const repoRoot = path.resolve(frontendRoot, "..");
const swaggerPath = path.join(
  repoRoot,
  "api_contracts",
  "openapi",
  "swagger.json",
);
const outputDir = path.join(frontendRoot, "src", "generated", "api-contracts");
const mutatorPath = path.join(
  frontendRoot,
  "src",
  "api",
  "contracts",
  "openapi-mutator.js",
);

const HTTP_METHODS = new Set([
  "get",
  "put",
  "post",
  "delete",
  "options",
  "head",
  "patch",
  "trace",
]);

function collectDefinitionRefs(obj, refs = new Set()) {
  if (!obj || typeof obj !== "object") return refs;
  if (Array.isArray(obj)) {
    obj.forEach((item) => collectDefinitionRefs(item, refs));
    return refs;
  }
  if (typeof obj.$ref === "string" && obj.$ref.startsWith("#/definitions/")) {
    refs.add(obj.$ref);
  }
  Object.values(obj).forEach((value) => collectDefinitionRefs(value, refs));
  return refs;
}

function resolveTransitiveDefinitions(allDefinitions, refs) {
  const allRefs = new Set(refs);
  let changed = true;
  while (changed) {
    changed = false;
    for (const ref of [...allRefs]) {
      const name = ref.replace("#/definitions/", "");
      const definition = allDefinitions[name];
      if (!definition) continue;
      const nestedRefs = collectDefinitionRefs(definition);
      for (const nestedRef of nestedRefs) {
        if (!allRefs.has(nestedRef)) {
          allRefs.add(nestedRef);
          changed = true;
        }
      }
    }
  }
  return allRefs;
}

// Swagger 2 needs vendor extensions for nullability and discriminated unions.
// Orval understands the equivalent OpenAPI 3 keywords, but drops x-nullable.
// Normalize every schema, including inline request/response fields. New nullable
// fields must not require endpoint-specific TypeScript or Zod rewrites.
export function normalizeSwaggerSchema(schema) {
  if (!schema || typeof schema !== "object") return schema;
  if (Array.isArray(schema)) return schema.map(normalizeSwaggerSchema);
  let result = Object.fromEntries(
    Object.entries(schema).map(([key, value]) => [
      key,
      normalizeSwaggerSchema(value),
    ]),
  );
  // The legacy alternative exposes additive properties for Swagger tooling,
  // but maxProperties == required.length forbids every optional property.
  // Orval drops maxProperties, so express that same closed branch structurally
  // before generation. Otherwise a partial restore group matches the legacy
  // branch and is silently accepted (or stripped) by the generated parser.
  if (
    result.type === "object" &&
    result.additionalProperties === false &&
    Array.isArray(result.required) &&
    result.maxProperties === result.required.length
  ) {
    result.properties = Object.fromEntries(
      Object.entries(result.properties || {}).filter(([key]) =>
        result.required.includes(key),
      ),
    );
  }
  const nullable = result["x-nullable"] === true;
  delete result["x-nullable"];
  // The recursive JSON-value rewrite already includes null. Adding another
  // wrapper would change its alias shape before that rewrite can recognize it.
  if (result["x-json-value"]) return result;
  // swagger2openapi leaves parameter-level vendor extensions on the parameter,
  // although nullability belongs to its converted schema, not the OAS3 parameter.
  if (nullable && typeof result.in === "string" && result.schema) {
    return { ...result, schema: { ...result.schema, nullable: true } };
  }
  if (result["x-oneOf"]) {
    result = { description: result.description, oneOf: result["x-oneOf"] };
  } else if (result["x-anyOf"]) {
    result = { description: result.description, anyOf: result["x-anyOf"] };
  }
  if (!nullable) return result;
  // Orval otherwise emits invalid `interface Name { ... } | null` for named
  // objects. An explicit union produces a type alias and preserves object shape.
  if (result.properties || result.$ref || result.oneOf || result.anyOf) {
    const annotations = {};
    for (const key of ["default", "description", "readOnly"]) {
      if (Object.hasOwn(result, key)) {
        annotations[key] = result[key];
        delete result[key];
      }
    }
    return { ...annotations, anyOf: [result, { type: "null" }] };
  }
  return { ...result, nullable: true };
}

// The Orval input transformer runs AFTER its validated Swagger-to-OpenAPI 3.1
// upgrade. Never insert native unions/null types into the Swagger 2 input, or
// disable validation to make a nonconforming input pass.
function normalizeSwaggerSpec(spec) {
  return normalizeSwaggerSchema(spec);
}

function buildManagementApiSwagger(swagger) {
  const paths = {};
  const refs = new Set();

  Object.entries(swagger.paths || {}).forEach(([pathName, pathSpec]) => {
    const filteredSpec = {};
    Object.entries(pathSpec || {}).forEach(([method, operation]) => {
      if (method === "parameters" || HTTP_METHODS.has(method)) {
        filteredSpec[method] = operation;
        collectDefinitionRefs(operation, refs);
      }
    });
    paths[pathName] = filteredSpec;
  });

  const allRefs = resolveTransitiveDefinitions(swagger.definitions || {}, refs);
  const definitions = {};
  for (const ref of allRefs) {
    const name = ref.replace("#/definitions/", "");
    if (swagger.definitions?.[name])
      definitions[name] = swagger.definitions[name];
  }

  return {
    ...swagger,
    info: {
      ...(swagger.info || {}),
      title: `${swagger.info?.title || "Future AGI API"} - management contracts`,
    },
    paths,
    definitions,
  };
}

function snapshotGeneratedFiles() {
  if (!fs.existsSync(outputDir)) return new Map();
  return new Map(
    fs
      .readdirSync(outputDir)
      .filter((name) => name.endsWith(".ts"))
      .map((name) => {
        const filePath = path.join(outputDir, name);
        return [filePath, fs.readFileSync(filePath, "utf8")];
      }),
  );
}

function restoreSnapshot(snapshot) {
  fs.rmSync(outputDir, { recursive: true, force: true });
  fs.mkdirSync(outputDir, { recursive: true });
  for (const [filePath, content] of snapshot.entries()) {
    fs.mkdirSync(path.dirname(filePath), { recursive: true });
    fs.writeFileSync(filePath, content);
  }
}

function normalizeGeneratedFileEndings(outputDir) {
  if (!fs.existsSync(outputDir)) return;
  for (const name of fs.readdirSync(outputDir)) {
    if (!name.endsWith(".ts")) continue;
    const filePath = path.join(outputDir, name);
    const content = fs.readFileSync(filePath, "utf8");
    fs.writeFileSync(filePath, content.replace(/\n+$/u, "\n"));
  }
}

async function formatGeneratedFiles(outputDir) {
  if (!fs.existsSync(outputDir)) return;
  for (const name of fs.readdirSync(outputDir)) {
    if (!name.endsWith(".ts")) continue;
    const filePath = path.join(outputDir, name);
    const content = fs.readFileSync(filePath, "utf8");
    fs.writeFileSync(
      filePath,
      await prettier.format(content, { filepath: filePath }),
    );
  }
}

function normalizeGeneratedQueryParamSerialization(apiOutputPath) {
  if (!fs.existsSync(apiOutputPath)) return;
  const content = fs.readFileSync(apiOutputPath, "utf8");
  fs.writeFileSync(
    apiOutputPath,
    content.replaceAll(
      `if (value !== undefined) {
      normalizedParams.append(key, value === null ? 'null' : value.toString())
    }`,
      `if (Array.isArray(value)) {
      value
        .filter((item) => item !== undefined && item !== null)
        .forEach((item) => normalizedParams.append(key, item.toString()))
    } else if (value !== undefined && value !== null) {
      normalizedParams.append(key, value.toString())
    }`,
    ),
  );
}

async function runGeneration(schemaPath, swagger, outputDir, mutatorPath) {
  const apiOutputPath = path.join(outputDir, "api.ts");
  const zodOutputPath = path.join(outputDir, "api.zod.ts");
  fs.rmSync(outputDir, { recursive: true, force: true });
  fs.mkdirSync(outputDir, { recursive: true });

  const baseOverride = {
    header: (info) => [
      "Auto-generated from the Django backend OpenAPI schema.",
      "To modify these types, update Django serializers/views, regenerate OpenAPI, then run:",
      "  yarn contracts:generate",
      "",
      ...(info?.title ? [info.title] : []),
      ...(info?.version ? [`OpenAPI spec version: ${info.version}`] : []),
    ],
    mutator: {
      path: mutatorPath,
      name: "apiMutator",
    },
    components: {
      schemas: { suffix: "Api" },
    },
  };

  await generate({
    input: {
      target: schemaPath,
      override: { transformer: normalizeSwaggerSpec },
    },
    output: {
      target: apiOutputPath,
      mode: "split",
      client: "fetch",
      prettier: true,
      override: baseOverride,
    },
  });

  await generate({
    input: {
      target: schemaPath,
      override: { transformer: normalizeSwaggerSpec },
    },
    output: {
      target: zodOutputPath,
      mode: "single",
      client: "zod",
      prettier: true,
      override: {
        header: baseOverride.header,
        components: baseOverride.components,
        // This endpoint is a closed public report, including both exact legacy
        // and complete restore alternatives. Do not change the permissive
        // generation semantics of unrelated, established API operations.
        operations: {
          "api_host-update-status_list": {
            zod: { strict: { response: true } },
          },
        },
        zod: {
          generate: {
            body: true,
            query: true,
            param: true,
            response: true,
            header: false,
          },
        },
      },
    },
  });

  // Orval can log a validation error without rejecting generate(). Treat
  // missing output as failure and let the caller restore the previous snapshot.
  for (const name of ["api.ts", "api.schemas.ts", "api.zod.ts"]) {
    if (!fs.existsSync(path.join(outputDir, name))) {
      throw new Error(`OpenAPI generation did not produce ${name}`);
    }
  }
  normalizeGeneratedQueryParamSerialization(apiOutputPath);

  // Post-processing for orval-narrow types.
  //
  // Long-term goal (tracked in TH-6029): emit standard JSON Schema `oneOf` and
  // `additionalProperties: true` from drf-yasg so orval generates these unions
  // and passthrough natively, and delete this whole block.
  //
  // Until then, every rewrite below MUST fail loudly if its anchor goes
  // missing. Silent no-op was the original concern on review — `assertReplace`
  // throws when the anchor isn't found (rename, docstring edit, whitespace
  // change), so a future refactor breaks the build instead of dropping the
  // union into `unknown`.
  function assertReplace(source, anchor, replacement, label) {
    const before = source;
    const after = source.replaceAll(anchor, replacement);
    if (after === before) {
      throw new Error(
        `Contract post-processing failed: anchor for "${label}" no longer matches. ` +
          `Either restore the anchor, or migrate to native oneOf / additionalProperties ` +
          `(TH-6029) and delete this rewrite.`,
      );
    }
    return after;
  }

  function assertReplaceRegex(source, pattern, replacement, label) {
    if (!pattern.test(source)) {
      throw new Error(
        `Contract post-processing failed: regex anchor for "${label}" no longer matches. ` +
          `Either restore the anchor, or migrate to native oneOf / additionalProperties ` +
          `(TH-6029) and delete this rewrite.`,
      );
    }
    return source.replace(pattern, replacement);
  }

  function assertReplaceInNamedBlock(
    source,
    declaration,
    anchor,
    replacement,
    label,
  ) {
    const start = source.indexOf(declaration);
    if (start < 0) {
      throw new Error(
        `Contract post-processing failed: declaration for "${label}" no longer matches.`,
      );
    }
    const nextExport = source.indexOf("\nexport ", start + declaration.length);
    const end = nextExport < 0 ? source.length : nextExport;
    const block = source.slice(start, end);
    const rewritten = block.replaceAll(anchor, replacement);
    if (rewritten === block) {
      throw new Error(
        `Contract post-processing failed: block anchor for "${label}" no longer matches.`,
      );
    }
    return source.slice(0, start) + rewritten + source.slice(end);
  }

  function assertReplaceRegexInNamedBlock(
    source,
    declaration,
    pattern,
    replacement,
    label,
  ) {
    const start = source.indexOf(declaration);
    if (start < 0) {
      throw new Error(
        `Contract post-processing failed: declaration for "${label}" no longer matches.`,
      );
    }
    const nextExport = source.indexOf("\nexport ", start + declaration.length);
    const end = nextExport < 0 ? source.length : nextExport;
    const block = source.slice(start, end);
    const rewritten = block.replace(pattern, replacement);
    if (rewritten === block) {
      throw new Error(
        `Contract post-processing failed: block regex anchor for "${label}" no longer matches.`,
      );
    }
    return source.slice(0, start) + rewritten + source.slice(end);
  }

  const schemasOutputPath = path.join(outputDir, "api.schemas.ts");
  if (fs.existsSync(schemasOutputPath)) {
    let schemas = fs.readFileSync(schemasOutputPath, "utf8");

    // x-string-or-array: type aliases preceded by "Plain text string or array
    // of content-part objects." are generated as { [key: string]: unknown } but
    // must be string | unknown[]. Keyed off the description so any future
    // StringOrArrayField gets rewritten, not just MessageItemApiContent.
    schemas = assertReplaceRegex(
      schemas,
      /\/\*\*\n \* Plain text string or array of content-part objects\.\n \*\/\nexport type (\w+) = \{ \[key: string\]: unknown \}( \| null)?;/g,
      "/**\n * Plain text string or array of content-part objects.\n */\nexport type $1 = string | unknown[]$2;",
      "x-string-or-array TS aliases → string | unknown[]",
    );

    // x-string-or-object: type aliases preceded by "String or JSON object."
    // are generated as { [key: string]: unknown } but must be string | { ... }.
    schemas = assertReplaceRegex(
      schemas,
      /\/\*\*\n \* String or JSON object\.\n \*\/\nexport type (\w+) = \{ \[key: string\]: unknown \}( \| null)?;/g,
      "/**\n * String or JSON object.\n */\nexport type $1 = string | { [key: string]: unknown }$2;",
      "x-string-or-object TS aliases → string | object",
    );

    // Orval ignores x-json-value and narrows arbitrary JSON to object-only.
    // Define one recursive JSON type, then use it for every field carrying the
    // extension (including dynamic trace/span list row cells).
    schemas = assertReplace(
      schemas,
      `/**
 * Any valid JSON value.
 */
export type SpanAttributeTopValueApiValue = { [key: string]: unknown };`,
      `export type JsonValueApi =
  | string
  | number
  | boolean
  | null
  | JsonValueApi[]
  | { [key: string]: JsonValueApi };

/** @deprecated Use JsonValueApi. */
export type SpanAttributeJsonValueApi = JsonValueApi;

/**
 * Any valid JSON value.
 */
export type SpanAttributeTopValueApiValue = JsonValueApi;`,
      "declare recursive JSON TS type",
    );
    for (const jsonAlias of [
      "SpanAttributeValueApiValue",
      "DashboardFilterValueOptionApiValue",
      "TraceSessionTableRowApiFirstMessage",
      "TraceSessionTableRowApiLastMessage",
      "SpanListColumnConfigApiSettings",
      "SpanListColumnConfigApiChoicesMap",
      "SpanListColumnConfigApiAnnotators",
      "TraceObserveColumnConfigApiSettings",
      "TraceObserveColumnConfigApiChoicesMap",
      "TraceObserveColumnConfigApiAnnotators",
      "EvalRollupSpanApiValue",
      "EvalRollupEvalApiChoicesMap",
      "EvalRollupEvalApiAggregate",
    ]) {
      schemas = assertReplace(
        schemas,
        `/**
 * Any valid JSON value.
 */
export type ${jsonAlias} = { [key: string]: unknown };`,
        `/**
 * Any valid JSON value.
 */
export type ${jsonAlias} = JsonValueApi;`,
        `${jsonAlias} → recursive JSON value`,
      );
    }

    for (const rowType of [
      "TracePrototypeListResultApiTableItem",
      "TraceObserveListResultApiTableItem",
      "SpanPrototypeListResultApiTableItem",
      "SpanObserveListResultApiTableItem",
      "TraceVoiceCallListResponseApiResultsItem",
      "TraceVoiceCallDetailResultApiTranscriptItem",
      "TraceVoiceCallDetailResultApiMessagesItem",
      "TraceVoiceCallDetailResultApiObservationSpanItem",
    ]) {
      schemas = assertReplace(
        schemas,
        `export type ${rowType} = {[key: string]: { [key: string]: unknown }};`,
        `export type ${rowType} = { [key: string]: JsonValueApi };`,
        `${rowType} → recursive JSON row`,
      );
    }

    schemas = assertReplaceInNamedBlock(
      schemas,
      "export interface TraceSessionTableRowApi {",
      "[key: string]: unknown;",
      "[key: string]: JsonValueApi | undefined;",
      "TraceSessionTableRowApi dynamic JSON values",
    );

    // The compact conversation record passes provider values on as they were
    // logged, and the times it computes are null when the provider gave none.
    schemas = assertReplaceRegex(
      schemas,
      /(export type Conversation(?:CallAgent|Call|Analysis|Latency|Turn)Api\w+ = )\{ \[key: string\]: unknown \};/g,
      "$1JsonValueApi;",
      "conversation record JSON values → recursive JSON value",
    );
    for (const recordType of [
      "ConversationVariablesApiConfigured",
      "ConversationVariablesApiCollected",
      "ConversationAnalysisApiFlags",
    ]) {
      schemas = assertReplace(
        schemas,
        `export type ${recordType} = {[key: string]: { [key: string]: unknown }};`,
        `export type ${recordType} = { [key: string]: JsonValueApi };`,
        `${recordType} → recursive JSON values`,
      );
    }
    fs.writeFileSync(schemasOutputPath, schemas);
  }

  if (fs.existsSync(zodOutputPath)) {
    let zod = fs.readFileSync(zodOutputPath, "utf8");

    zod = assertReplace(
      zod,
      `import * as zod from 'zod';`,
      `import * as zod from 'zod';

type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [key: string]: JsonValue };

const jsonValueSchema: zod.ZodType<JsonValue> =
  zod.lazy(() =>
    zod.union([
      zod.string(),
      zod.number(),
      zod.boolean(),
      zod.null(),
      zod.array(jsonValueSchema),
      zod.record(jsonValueSchema),
    ]),
  );`,
      "recursive JSON zod schema",
    );
    zod = assertReplaceRegex(
      zod,
      /(export const ApiTracesSpanAttributeDetailListResponse = zod\.object\(\{[\s\S]*?"top_values": zod\.array\(zod\.object\(\{\n {2}"value": )zod\.object\(\{\n\n\}\)\.passthrough\(\)\.describe\('Any valid JSON value\.'\),/,
      "$1jsonValueSchema.describe('Any valid JSON value.'),",
      "SpanAttributeTopValue zod value → recursive JSON value",
    );
    zod = assertReplaceRegex(
      zod,
      /(export const ApiTracesSpanAttributeValuesListResponse = zod\.object\(\{[\s\S]*?"result": zod\.array\(zod\.object\(\{\n {2}"value": )zod\.object\(\{\n\n\}\)\.passthrough\(\)\.describe\('Any valid JSON value\.'\),/,
      "$1jsonValueSchema.describe('Any valid JSON value.'),",
      "SpanAttributeValue zod value → recursive JSON value",
    );
    zod = assertReplaceRegex(
      zod,
      /(export const TracerDashboardFilterValuesResponse = zod\.object\(\{[\s\S]*?"values": zod\.array\(zod\.object\(\{\n {2}"value": )zod\.object\(\{\n\n\}\)\.passthrough\(\)\.describe\('Any valid JSON value\.'\),/,
      "$1jsonValueSchema.describe('Any valid JSON value.'),",
      "DashboardFilterValueOption zod value → recursive JSON value",
    );

    // Both detail endpoints expose the same typed rollup. Scope scalar
    // rewrites to that property so provider-level fields keep their own rules.
    for (const responseName of [
      "TracerTraceReadResponse",
      "TracerTraceVoiceCallDetailResponse",
    ]) {
      const declaration = `export const ${responseName} = zod.object({`;
      zod = assertReplaceRegexInNamedBlock(
        zod,
        declaration,
        /zod\.object\(\{\n\n\}\)\.passthrough\(\)(?=(?:\.(?:optional|nullish|nullable)\(\)|\.default\([^)]*\))*\.describe\('Any valid JSON value\.'\))/g,
        "jsonValueSchema",
        `${responseName} JSON values → recursive JSON value`,
      );
    }

    const listResponseExports = [
      "TracerTraceListTracesResponse",
      "TracerTraceListTracesOfSessionResponse",
      "TracerTraceSessionListSessionsResponse",
      "TracerObservationSpanListSpansResponse",
      "TracerObservationSpanListSpansObserveResponse",
      "TracerTraceListVoiceCallsResponse",
    ];
    for (const exportName of listResponseExports) {
      zod = assertReplaceRegexInNamedBlock(
        zod,
        `export const ${exportName} = zod.object({`,
        /zod\.object\(\{\n\n\}\)\.passthrough\(\)(?=(?:\.(?:optional|nullish|nullable)\(\)|\.default\([^)]*\))*\.describe\('Any valid JSON value\.'\))/g,
        "jsonValueSchema",
        `${exportName} JSON cells → recursive JSON value`,
      );
    }
    const conversationEvidenceResponse =
      "export const TracerInternalErrorFeedV2AttemptsConversationEvidenceCreateResponse = zod.object({";
    zod = assertReplaceRegexInNamedBlock(
      zod,
      conversationEvidenceResponse,
      /zod\.object\(\{\n\n\}\)\.passthrough\(\)(?=(?:\.(?:optional|nullish|nullable)\(\)|\.default\([^)]*\))*\.describe\('Any valid JSON value\.'\))/g,
      "jsonValueSchema",
      "conversation evidence JSON values → recursive JSON value",
    );
    zod = assertReplaceRegexInNamedBlock(
      zod,
      "export const TracerTraceSessionListSessionsResponse = zod.object({",
      /("table": zod\.array\(\s*zod\.object\(\{[\s\S]*?"user_id_hash": zod\.string\(\)\.nullish\(\),?\s*)\}\)(\s*\),)/,
      "$1}).catchall(jsonValueSchema)$2",
      "TracerTraceSessionListSessionsResponse dynamic JSON row values",
    );

    // Preserve every modifier (nullable/optional/default) around portable unions.
    for (const [description, replacement] of [
      [
        "Plain text string or array of content-part objects.",
        "zod.union([zod.string(), zod.array(zod.unknown())])",
      ],
      [
        "String or JSON object.",
        "zod.union([zod.string(), zod.object({}).passthrough()])",
      ],
    ]) {
      const escaped = description.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      zod = assertReplaceRegex(
        zod,
        new RegExp(
          `zod\\.object\\(\\{\\s*\\}\\)\\.passthrough\\(\\)(?=(?:\\.(?:optional|nullish|nullable)\\(\\)|\\.default\\([^)]*\\))*\\.describe\\('${escaped}'\\))`,
          "g",
        ),
        replacement,
        `${description} Zod union`,
      );
    }

    // additionalProperties:true on PromptModelParams / PromptConfiguration:
    // orval does not add .passthrough() for inline object schemas. Anchor on
    // the }).default(CONSTANT) suffix orval emits for each serializer. Split
    // per-target so a missing anchor for one field fails the build instead of
    // hiding behind a sibling that still matches.
    for (const target of ["ModelParams", "Configuration"]) {
      zod = assertReplaceRegex(
        zod,
        new RegExp(
          `\\}\\)\\.default\\((modelHubExperimentsV2(?:Create|Update)Body[A-Za-z]+${target}[A-Za-z]*Default)\\),`,
          "g",
        ),
        "}).passthrough().default($1),",
        `${target} → .passthrough() escape hatch`,
      );
    }

    // MessageItem: additionalProperties:true on the swagger, but messages has
    // no orval "*Default" constant to anchor on (it's .optional(), not .default()).
    // Anchor on the unique closing field pair "tool_call_id" + "id" instead —
    // no other object in the generated file shares that pair, and this fails
    // loudly if MessageItemSerializer's field list changes.
    zod = assertReplace(
      zod,
      `"tool_call_id": zod.string().min(1).optional(),\n  "id": zod.string().min(1).optional()\n})).optional(),`,
      `"tool_call_id": zod.string().min(1).optional(),\n  "id": zod.string().min(1).optional()\n}).passthrough()).optional(),`,
      "MessageItem → .passthrough() (additionalProperties: true)",
    );

    // Read aliases share the existing GET validator, including its JSON/nullable fixes.
    const responseName = (id) =>
      id
        .split(/[-_]/)
        .map((part) => part[0].toUpperCase() + part.slice(1))
        .join("") + "Response";
    for (const operation of Object.values(swagger.paths)) {
      if (!operation.post?.["x-read-query-post"]) continue;
      if (
        JSON.stringify(operation.get.responses) !==
        JSON.stringify(operation.post.responses)
      ) {
        throw new Error("Read POST and GET response contracts differ.");
      }
      const getName = responseName(operation.get.operationId);
      const postName = responseName(operation.post.operationId);
      if (!zod.includes(`export const ${getName} =`))
        throw new Error(`Missing read response ${getName}`);
      zod = assertReplaceRegexInNamedBlock(
        zod,
        `export const ${postName} =`,
        / =[\s\S]*/,
        ` = ${getName};\n\n`,
        postName,
      );
    }
    fs.writeFileSync(zodOutputPath, zod);
  }
  await formatGeneratedFiles(outputDir);
  normalizeGeneratedFileEndings(outputDir);
}

async function main() {
  const swagger = JSON.parse(fs.readFileSync(swaggerPath, "utf8"));
  const managementApiSwagger = buildManagementApiSwagger(swagger);
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "futureagi-openapi-"));
  const tempSchemaPath = path.join(tempDir, "management-openapi.json");
  fs.writeFileSync(
    tempSchemaPath,
    JSON.stringify(managementApiSwagger, null, 2),
  );

  try {
    if (process.argv.includes("--check")) {
      const changed = await checkGeneratedOutput({
        outputDir,
        mutatorPath,
        generate: (directory, mutator) =>
          runGeneration(tempSchemaPath, swagger, directory, mutator),
      });
      if (changed.length) {
        console.error(
          [
            "Generated OpenAPI clients are out of date. Run `yarn contracts:generate`.",
            ...changed.map(
              (name) => `  - ${path.relative(frontendRoot, path.join(outputDir, name))}`,
            ),
          ].join("\n"),
        );
        process.exitCode = 1;
      }
    } else {
      const before = snapshotGeneratedFiles();
      try {
        await runGeneration(tempSchemaPath, swagger, outputDir, mutatorPath);
      } catch (error) {
        restoreSnapshot(before);
        throw error;
      }
    }
  } finally {
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
}

if (
  process.argv[1] &&
  path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  await main();
}
