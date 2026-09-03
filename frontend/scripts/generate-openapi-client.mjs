/* eslint-env node */
/* eslint-disable no-console */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { generate } from "orval";
import prettier from "prettier";

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
const apiOutputPath = path.join(outputDir, "api.ts");
const zodOutputPath = path.join(outputDir, "api.zod.ts");
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

function normalizeGeneratedFileEndings() {
  if (!fs.existsSync(outputDir)) return;
  for (const name of fs.readdirSync(outputDir)) {
    if (!name.endsWith(".ts")) continue;
    const filePath = path.join(outputDir, name);
    const content = fs.readFileSync(filePath, "utf8");
    fs.writeFileSync(filePath, content.replace(/\n+$/u, "\n"));
  }
}

async function formatGeneratedFiles() {
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

function normalizeGeneratedQueryParamSerialization() {
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

async function runGeneration(schemaPath) {
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
    input: schemaPath,
    output: {
      target: apiOutputPath,
      mode: "split",
      client: "fetch",
      prettier: true,
      override: baseOverride,
    },
  });

  await generate({
    input: schemaPath,
    output: {
      target: zodOutputPath,
      mode: "single",
      client: "zod",
      prettier: true,
      override: {
        header: baseOverride.header,
        components: baseOverride.components,
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

  normalizeGeneratedQueryParamSerialization();

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

  const voiceCallDetailNullableFields = [
    "provider_call_id",
    "phone_number",
    "customer_name",
    "call_id",
    "status",
    "started_at",
    "ended_at",
    "created_at",
    "duration_seconds",
    "recording_url",
    "stereo_recording_url",
    "cost_cents",
    "cost_breakdown",
    "error_message",
    "call_summary",
    "ended_reason",
    "overall_score",
    "response_time_ms",
    "response_time_seconds",
    "assistant_id",
    "assistant_phone_number",
    "call_type",
    "message_count",
    "transcript_available",
    "transcript",
    "messages",
    "analysis_data",
    "evaluation_data",
    "call_execution_id",
    "test_execution_id",
    "scenario_id",
    "scenario_name",
    "scenario_graph_id",
    "turn_count",
    "talk_ratio",
    "agent_talk_percentage",
    "bot_talk_pct",
    "user_talk_pct",
    "avg_agent_latency_ms",
    "user_wpm",
    "bot_wpm",
    "user_interruption_count",
    "ai_interruption_count",
  ];
  const schemasOutputPath = path.join(outputDir, "api.schemas.ts");
  if (fs.existsSync(schemasOutputPath)) {
    let schemas = fs.readFileSync(schemasOutputPath, "utf8");

    // x-string-or-array: type aliases preceded by "Plain text string or array
    // of content-part objects." are generated as { [key: string]: unknown } but
    // must be string | unknown[]. Keyed off the description so any future
    // StringOrArrayField gets rewritten, not just MessageItemApiContent.
    schemas = assertReplaceRegex(
      schemas,
      /\/\*\*\n \* Plain text string or array of content-part objects\.\n \*\/\nexport type (\w+) = \{ \[key: string\]: unknown \};/g,
      "/**\n * Plain text string or array of content-part objects.\n */\nexport type $1 = string | unknown[];",
      "x-string-or-array TS aliases → string | unknown[]",
    );

    // x-string-or-object: type aliases preceded by "String or JSON object."
    // are generated as { [key: string]: unknown } but must be string | { ... }.
    schemas = assertReplaceRegex(
      schemas,
      /\/\*\*\n \* String or JSON object\.\n \*\/\nexport type (\w+) = \{ \[key: string\]: unknown \};/g,
      "/**\n * String or JSON object.\n */\nexport type $1 = string | { [key: string]: unknown };",
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

    // Orval drops Swagger 2.0 x-nullable from generated TypeScript. Voice
    // detail deliberately returns null when a provider did not emit an
    // optional value or a computed metric could not be derived, so preserve
    // that wire contract in the generated client instead of lying to callers.
    for (const field of voiceCallDetailNullableFields) {
      schemas = assertReplaceRegexInNamedBlock(
        schemas,
        "export interface TraceVoiceCallDetailResultApi {",
        new RegExp(`(^\\s*${field}\\??: [^;]+)(;)$`, "m"),
        "$1 | null$2",
        `TraceVoiceCallDetailResultApi.${field} nullable`,
      );
    }

    const columnConfigNullableFields = [
      ["group_by?: string;", "group_by?: string | null;"],
      ["output_type?: string;", "output_type?: string | null;"],
      ["reverse_output?: boolean;", "reverse_output?: boolean | null;"],
      [
        "annotation_label_type?: string;",
        "annotation_label_type?: string | null;",
      ],
      ["choices?: string[];", "choices?: (string | null)[] | null;"],
      ["eval_template_id?: string;", "eval_template_id?: string | null;"],
      ["source_field?: string;", "source_field?: string | null;"],
      ["parent_eval_id?: string;", "parent_eval_id?: string | null;"],
    ];
    for (const configType of [
      "SpanListColumnConfigApi",
      "TraceObserveColumnConfigApi",
    ]) {
      for (const [anchor, replacement] of columnConfigNullableFields) {
        schemas = assertReplaceInNamedBlock(
          schemas,
          `export interface ${configType} {`,
          anchor,
          replacement,
          `${configType} nullable ${anchor}`,
        );
      }
    }

    // Orval also drops Swagger 2.0 x-nullable for the exact continuation
    // contracts. Null is the truthful terminal marker, while omission means
    // the optional legacy metadata was not returned at all.
    for (const [anchor, replacement] of [
      ["next_page_index?: number;", "next_page_index?: number | null;"],
      ["next_cursor?: string;", "next_cursor?: string | null;"],
    ]) {
      schemas = assertReplaceInNamedBlock(
        schemas,
        "export interface DatasetTableMetadataApi {",
        anchor,
        replacement,
        `DatasetTableMetadataApi nullable ${anchor}`,
      );
    }
    schemas = assertReplaceInNamedBlock(
      schemas,
      "export interface SimulationPreviewPageApi {",
      "next_cursor: string;",
      "next_cursor: string | null;",
      "SimulationPreviewPageApi.next_cursor nullable",
    );

    for (const [typeName, fields] of [
      ["DashboardFilterValuesResultApi", [["next_cursor", "string"]]],
      [
        "DashboardMetricsCatalogResultApi",
        [
          ["total", "number"],
          ["next_cursor", "string"],
        ],
      ],
    ]) {
      for (const [field, valueType] of fields) {
        schemas = assertReplaceInNamedBlock(
          schemas,
          `export interface ${typeName} {`,
          `${field}?: ${valueType};`,
          `${field}?: ${valueType} | null;`,
          `${typeName}.${field} nullable`,
        );
      }
    }

    for (const metadataType of [
      "SpanListMetadataApi",
      "TraceObserveListMetadataApi",
      "TraceSessionListMetadataApi",
    ]) {
      for (const field of [
        "total_rows_exact",
        "next_cursor",
        "next_cursor_fingerprint",
        "query_error_code",
      ]) {
        const valueType = field === "total_rows_exact" ? "number" : "string";
        schemas = assertReplaceInNamedBlock(
          schemas,
          `export interface ${metadataType} {`,
          `${field}?: ${valueType};`,
          `${field}?: ${valueType} | null;`,
          `${metadataType}.${field} nullable`,
        );
      }
    }

    for (const graphType of [
      "TraceAgentGraphNodeApi",
      "TraceAgentGraphEdgeApi",
    ]) {
      schemas = assertReplaceInNamedBlock(
        schemas,
        `export interface ${graphType} {`,
        "trace_count: number;",
        "trace_count: number | null;",
        `${graphType}.trace_count nullable`,
      );
    }

    for (const field of ["next", "previous"]) {
      schemas = assertReplaceInNamedBlock(
        schemas,
        "export interface TraceVoiceCallListResponseApi {",
        `${field}: number;`,
        `${field}: number | null;`,
        `TraceVoiceCallListResponseApi.${field} nullable`,
      );
    }
    schemas = assertReplaceInNamedBlock(
      schemas,
      "export interface TraceVoiceCallListResponseApi {",
      "next_cursor?: string;",
      "next_cursor?: string | null;",
      "TraceVoiceCallListResponseApi.next_cursor nullable",
    );
    schemas = assertReplaceInNamedBlock(
      schemas,
      "export interface TraceVoiceCallListResponseApi {",
      "next_cursor_fingerprint?: string;",
      "next_cursor_fingerprint?: string | null;",
      "TraceVoiceCallListResponseApi.next_cursor_fingerprint nullable",
    );
    schemas = assertReplaceInNamedBlock(
      schemas,
      "export interface QueueAddItemsResultApi {",
      "next_cursor?: string;",
      "next_cursor?: string | null;",
      "QueueAddItemsResultApi.next_cursor nullable",
    );
    schemas = assertReplaceInNamedBlock(
      schemas,
      "export interface QueueAddItemsResultApi {",
      "next_cursor_fingerprint?: string;",
      "next_cursor_fingerprint?: string | null;",
      "QueueAddItemsResultApi.next_cursor_fingerprint nullable",
    );

    for (const [field, valueType] of [
      ["session_id", "string"],
      ["session_name", "string"],
      ["project_id", "string"],
      ["start_time", "string"],
      ["end_time", "string"],
      ["created_at", "string"],
      ["duration", "number"],
      ["total_cost", "number"],
      ["total_tokens", "number"],
      ["total_traces_count", "number"],
      ["first_message", "TraceSessionTableRowApiFirstMessage"],
      ["last_message", "TraceSessionTableRowApiLastMessage"],
      ["user_id", "string"],
      ["user_id_type", "string"],
      ["user_id_hash", "string"],
    ]) {
      schemas = assertReplaceInNamedBlock(
        schemas,
        "export interface TraceSessionTableRowApi {",
        `${field}?: ${valueType};`,
        `${field}?: ${valueType} | null;`,
        `TraceSessionTableRowApi.${field} nullable`,
      );
    }
    schemas = assertReplaceInNamedBlock(
      schemas,
      "export interface TraceSessionTableRowApi {",
      "[key: string]: unknown;",
      "[key: string]: JsonValueApi | undefined;",
      "TraceSessionTableRowApi dynamic JSON values",
    );

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
    zod = assertReplaceRegexInNamedBlock(
      zod,
      "export const TracerTraceVoiceCallDetailResponse = zod.object({",
      /zod\.object\(\{\n\n\}\)\.passthrough\(\)(?=(?:\.(?:optional|nullish|nullable)\(\)|\.default\([^)]*\))*\.describe\('Any valid JSON value\.'\))/g,
      "jsonValueSchema",
      "TracerTraceVoiceCallDetailResponse JSON cells → recursive JSON value",
    );

    for (const responseName of [
      "ModelHubDevelopsGetDatasetTableListResponse",
      "ModelHubDevelopsGetExperimentDatasetTableListResponse",
    ]) {
      zod = assertReplaceInNamedBlock(
        zod,
        `export const ${responseName} = zod.object({`,
        "zod.number().optional().describe('Next zero-based page index, or null at exact exhaustion.')",
        "zod.number().nullish().describe('Next zero-based page index, or null at exact exhaustion.')",
        `${responseName}.next_page_index nullable`,
      );
      zod = assertReplaceInNamedBlock(
        zod,
        `export const ${responseName} = zod.object({`,
        "zod.string().min(1).optional().describe('Signed exact continuation cursor, or null at exhaustion.')",
        "zod.string().min(1).nullish().describe('Signed exact continuation cursor, or null at exhaustion.')",
        `${responseName}.next_cursor nullable`,
      );
    }
    for (const responseName of [
      "SimulateRunTestsPreviewExecutionsListResponse",
      "SimulateTestExecutionsPreviewCallsListResponse",
    ]) {
      zod = assertReplaceInNamedBlock(
        zod,
        `export const ${responseName} = zod.object({`,
        '"next_cursor": zod.string().min(1),',
        '"next_cursor": zod.string().min(1).nullable(),',
        `${responseName}.next_cursor nullable`,
      );
    }

    // As with the generated TypeScript model above, Orval ignores Swagger
    // 2.0 x-nullable. Keep the generated runtime parser aligned with the
    // provider-normalized response so legitimate nulls do not blank the
    // detail drawer.
    for (const [anchor, replacement] of [
      [
        '"provider_call_id": zod.string().min(1),',
        '"provider_call_id": zod.string().min(1).nullable(),',
      ],
      [
        '"phone_number": zod.string().min(1).optional(),',
        '"phone_number": zod.string().min(1).nullish(),',
      ],
      [
        '"customer_name": zod.string().min(1).optional(),',
        '"customer_name": zod.string().min(1).nullish(),',
      ],
      [
        '"call_id": zod.string().min(1).optional(),',
        '"call_id": zod.string().min(1).nullish(),',
      ],
      [
        '"status": zod.string().min(1).optional(),',
        '"status": zod.string().min(1).nullish(),',
      ],
      [
        '"started_at": zod.string().min(1).optional(),',
        '"started_at": zod.string().min(1).nullish(),',
      ],
      [
        '"ended_at": zod.string().min(1).optional(),',
        '"ended_at": zod.string().min(1).nullish(),',
      ],
      [
        '"created_at": zod.string().min(1).optional(),',
        '"created_at": zod.string().min(1).nullish(),',
      ],
      [
        '"duration_seconds": zod.number().optional(),',
        '"duration_seconds": zod.number().nullish(),',
      ],
      [
        '"recording_url": zod.string().optional(),',
        '"recording_url": zod.string().nullish(),',
      ],
      [
        '"stereo_recording_url": zod.string().min(1).optional(),',
        '"stereo_recording_url": zod.string().min(1).nullish(),',
      ],
      [
        '"cost_cents": zod.number().optional(),',
        '"cost_cents": zod.number().nullish(),',
      ],
      [
        '"cost_breakdown": zod.record(zod.string(), zod.unknown()).optional(),',
        '"cost_breakdown": zod.record(zod.string(), zod.unknown()).nullish(),',
      ],
      [
        '"error_message": zod.string().min(1).optional(),',
        '"error_message": zod.string().min(1).nullish(),',
      ],
      [
        '"call_summary": zod.string().optional(),',
        '"call_summary": zod.string().nullish(),',
      ],
      [
        '"ended_reason": zod.string().min(1).optional(),',
        '"ended_reason": zod.string().min(1).nullish(),',
      ],
      [
        '"overall_score": zod.number().optional(),',
        '"overall_score": zod.number().nullish(),',
      ],
      [
        '"response_time_ms": zod.number().optional(),',
        '"response_time_ms": zod.number().nullish(),',
      ],
      [
        '"response_time_seconds": zod.number().optional(),',
        '"response_time_seconds": zod.number().nullish(),',
      ],
      [
        '"assistant_id": zod.string().min(1).optional(),',
        '"assistant_id": zod.string().min(1).nullish(),',
      ],
      [
        '"assistant_phone_number": zod.string().min(1).optional(),',
        '"assistant_phone_number": zod.string().min(1).nullish(),',
      ],
      [
        '"call_type": zod.string().min(1).optional(),',
        '"call_type": zod.string().min(1).nullish(),',
      ],
      [
        '"message_count": zod.number().optional(),',
        '"message_count": zod.number().nullish(),',
      ],
      [
        '"transcript_available": zod.boolean().optional(),',
        '"transcript_available": zod.boolean().nullish(),',
      ],
      [
        "\"transcript\": zod.array(zod.record(zod.string(), jsonValueSchema.describe('Any valid JSON value.'))).optional(),",
        "\"transcript\": zod.array(zod.record(zod.string(), jsonValueSchema.describe('Any valid JSON value.'))).nullish(),",
      ],
      [
        "\"messages\": zod.array(zod.record(zod.string(), jsonValueSchema.describe('Any valid JSON value.'))).optional(),",
        "\"messages\": zod.array(zod.record(zod.string(), jsonValueSchema.describe('Any valid JSON value.'))).nullish(),",
      ],
      [
        '"analysis_data": zod.record(zod.string(), zod.unknown()).optional(),',
        '"analysis_data": zod.record(zod.string(), zod.unknown()).nullish(),',
      ],
      [
        '"evaluation_data": zod.record(zod.string(), zod.unknown()).optional(),',
        '"evaluation_data": zod.record(zod.string(), zod.unknown()).nullish(),',
      ],
      [
        '"call_execution_id": zod.string().min(1).optional(),',
        '"call_execution_id": zod.string().min(1).nullish(),',
      ],
      [
        '"test_execution_id": zod.string().min(1).optional(),',
        '"test_execution_id": zod.string().min(1).nullish(),',
      ],
      [
        '"scenario_id": zod.string().min(1).optional(),',
        '"scenario_id": zod.string().min(1).nullish(),',
      ],
      [
        '"scenario_name": zod.string().min(1).optional(),',
        '"scenario_name": zod.string().min(1).nullish(),',
      ],
      [
        '"scenario_graph_id": zod.string().min(1).optional(),',
        '"scenario_graph_id": zod.string().min(1).nullish(),',
      ],
      ['"turn_count": zod.number(),', '"turn_count": zod.number().nullable(),'],
      ['"talk_ratio": zod.number(),', '"talk_ratio": zod.number().nullable(),'],
      [
        '"agent_talk_percentage": zod.number(),',
        '"agent_talk_percentage": zod.number().nullable(),',
      ],
      [
        '"bot_talk_pct": zod.number(),',
        '"bot_talk_pct": zod.number().nullable(),',
      ],
      [
        '"user_talk_pct": zod.number(),',
        '"user_talk_pct": zod.number().nullable(),',
      ],
      [
        '"avg_agent_latency_ms": zod.number(),',
        '"avg_agent_latency_ms": zod.number().nullable(),',
      ],
      ['"user_wpm": zod.number(),', '"user_wpm": zod.number().nullable(),'],
      ['"bot_wpm": zod.number(),', '"bot_wpm": zod.number().nullable(),'],
      [
        '"user_interruption_count": zod.number(),',
        '"user_interruption_count": zod.number().nullable(),',
      ],
      [
        '"ai_interruption_count": zod.number()',
        '"ai_interruption_count": zod.number().nullable()',
      ],
    ]) {
      zod = assertReplaceInNamedBlock(
        zod,
        "export const TracerTraceVoiceCallDetailResponse = zod.object({",
        anchor,
        replacement,
        `TracerTraceVoiceCallDetailResponse nullable ${anchor}`,
      );
    }
    const columnConfigNullableZodFields = [
      [
        '"group_by": zod.string().min(1).optional(),',
        '"group_by": zod.string().min(1).nullish(),',
      ],
      [
        '"output_type": zod.string().min(1).optional(),',
        '"output_type": zod.string().min(1).nullish(),',
      ],
      [
        '"reverse_output": zod.boolean().optional(),',
        '"reverse_output": zod.boolean().nullish(),',
      ],
      [
        '"annotation_label_type": zod.string().min(1).optional(),',
        '"annotation_label_type": zod.string().min(1).nullish(),',
      ],
      [
        '"choices": zod.array(zod.string().min(1)).optional(),',
        '"choices": zod.array(zod.string().min(1).nullable()).nullish(),',
      ],
      [
        '"eval_template_id": zod.string().min(1).optional(),',
        '"eval_template_id": zod.string().min(1).nullish(),',
      ],
      [
        '"source_field": zod.string().min(1).optional(),',
        '"source_field": zod.string().min(1).nullish(),',
      ],
      [
        '"parent_eval_id": zod.string().min(1).optional()',
        '"parent_eval_id": zod.string().min(1).nullish()',
      ],
    ];
    for (const exportName of listResponseExports) {
      for (const [anchor, replacement] of columnConfigNullableZodFields) {
        zod = assertReplaceInNamedBlock(
          zod,
          `export const ${exportName} = zod.object({`,
          anchor,
          replacement,
          `${exportName} nullable column config`,
        );
      }
    }

    for (const exportName of listResponseExports.slice(0, -1)) {
      for (const [field, pattern] of [
        [
          "total_rows_exact",
          /("total_rows_exact": zod\.number\(\)(?:\.min\([^)]*\))?)\.optional\(\),/,
        ],
        [
          "next_cursor",
          /("next_cursor": zod\.string\(\)\.min\(1\))\.optional\(\),/,
        ],
        [
          "next_cursor_fingerprint",
          /("next_cursor_fingerprint": zod\.string\(\)\.min\(1\)\.regex\([\s\S]*?\))\.optional\(\),/,
        ],
        [
          "query_error_code",
          /("query_error_code": zod\.string\(\)\.min\(1\))\.optional\(\),/,
        ],
      ]) {
        zod = assertReplaceRegexInNamedBlock(
          zod,
          `export const ${exportName} = zod.object({`,
          pattern,
          "$1.nullish(),",
          `${exportName}.${field} nullable`,
        );
      }
    }

    for (const field of ["next", "previous"]) {
      zod = assertReplaceInNamedBlock(
        zod,
        "export const TracerTraceListVoiceCallsResponse = zod.object({",
        `"${field}": zod.number().min(1),`,
        `"${field}": zod.number().min(1).nullable(),`,
        `TracerTraceListVoiceCallsResponse.${field} nullable`,
      );
    }
    zod = assertReplaceInNamedBlock(
      zod,
      "export const TracerTraceListVoiceCallsResponse = zod.object({",
      '"next_cursor": zod.string().min(1).optional(),',
      '"next_cursor": zod.string().min(1).nullish(),',
      "TracerTraceListVoiceCallsResponse.next_cursor nullable",
    );
    zod = assertReplaceRegexInNamedBlock(
      zod,
      "export const TracerTraceListVoiceCallsResponse = zod.object({",
      /("next_cursor_fingerprint": zod\.string\(\)\.min\(1\)\.regex\([\s\S]*?\))\.optional\(\),/,
      "$1.nullish(),",
      "TracerTraceListVoiceCallsResponse.next_cursor_fingerprint nullable",
    );
    zod = assertReplaceRegexInNamedBlock(
      zod,
      "export const ModelHubAnnotationQueuesItemsAddItemsResponse = zod.object({",
      /("next_cursor": zod\.string\(\)\.min\(1\))\.optional\(\)(,?)/,
      "$1.nullish()$2",
      "ModelHubAnnotationQueuesItemsAddItemsResponse.next_cursor nullable",
    );
    zod = assertReplaceRegexInNamedBlock(
      zod,
      "export const ModelHubAnnotationQueuesItemsAddItemsResponse = zod.object({",
      /("next_cursor_fingerprint": zod\.string\(\)\.min\(1\)\.regex\([\s\S]*?\))\.optional\(\)(,?)/,
      "$1.nullish()$2",
      "ModelHubAnnotationQueuesItemsAddItemsResponse.next_cursor_fingerprint nullable",
    );

    for (const field of [
      "session_id",
      "session_name",
      "project_id",
      "start_time",
      "end_time",
      "created_at",
      "duration",
      "total_cost",
      "total_tokens",
      "total_traces_count",
      "user_id",
      "user_id_type",
      "user_id_hash",
    ]) {
      zod = assertReplaceRegexInNamedBlock(
        zod,
        "export const TracerTraceSessionListSessionsResponse = zod.object({",
        new RegExp(`("${field}": zod\\.[^\\n]+)\\.optional\\(\\)(,?)`),
        "$1.nullish()$2",
        `TracerTraceSessionListSessionsResponse.${field} nullable`,
      );
    }
    for (const field of ["first_message", "last_message"]) {
      zod = assertReplaceRegexInNamedBlock(
        zod,
        "export const TracerTraceSessionListSessionsResponse = zod.object({",
        new RegExp(
          `("${field}": jsonValueSchema)\\.optional\\(\\)(\\.describe\\('Any valid JSON value\\.'\\))(,?)`,
        ),
        "$1.nullish()$2$3",
        `TracerTraceSessionListSessionsResponse.${field} nullable`,
      );
    }
    zod = assertReplaceRegexInNamedBlock(
      zod,
      "export const TracerTraceSessionListSessionsResponse = zod.object({",
      /("table": zod\.array\(\s*zod\.object\(\{[\s\S]*?"user_id_hash": zod\.string\(\)\.nullish\(\),?\s*)\}\)(\s*\),)/,
      "$1}).catchall(jsonValueSchema)$2",
      "TracerTraceSessionListSessionsResponse dynamic JSON row values",
    );

    zod = assertReplaceRegexInNamedBlock(
      zod,
      "export const TracerDashboardFilterValuesResponse = zod.object({",
      /("next_cursor": zod\.string\(\)\.min\(1\)(?:\.max\([^)]*\))?)\.optional\(\),/,
      "$1.nullish(),",
      "TracerDashboardFilterValuesResponse.next_cursor nullable",
    );
    for (const [field, pattern] of [
      ["total", /("total": zod\.number\(\)(?:\.min\([^)]*\))?)\.optional\(\),/],
      [
        "next_cursor",
        /("next_cursor": zod\.string\(\)\.min\(1\)(?:\.max\([^)]*\))?)\.optional\(\),/,
      ],
    ]) {
      zod = assertReplaceRegexInNamedBlock(
        zod,
        "export const TracerDashboardMetricsResponse = zod.object({",
        pattern,
        "$1.nullish(),",
        `TracerDashboardMetricsResponse.${field} nullable`,
      );
    }

    for (const fieldPrefix of ["Nodes", "Edges", "PathEdges"]) {
      zod = assertReplaceInNamedBlock(
        zod,
        "export const TracerTraceAgentGraphResponse = zod.object({",
        `"trace_count": zod.number().min(tracerTraceAgentGraphResponseResult${fieldPrefix}ItemTraceCountMin),`,
        `"trace_count": zod.number().min(tracerTraceAgentGraphResponseResult${fieldPrefix}ItemTraceCountMin).nullable(),`,
        `TracerTraceAgentGraphResponse.${fieldPrefix}.trace_count nullable`,
      );
    }

    // x-string-or-array: orval generates zod.object({}).passthrough() for these
    // fields. Use the unique description emitted by StringOrArrayField as anchor.
    zod = assertReplace(
      zod,
      `zod.object({\n\n}).passthrough().describe('Plain text string or array of content-part objects.')`,
      `zod.union([zod.string(), zod.array(zod.unknown())]).describe('Plain text string or array of content-part objects.')`,
      "x-string-or-array zod (required) → union(string, array)",
    );

    // x-string-or-object: orval generates zod.object({}).passthrough() for these
    // fields too. Use the unique description emitted by StringOrObjectField as anchor.
    zod = assertReplace(
      zod,
      `zod.object({\n\n}).passthrough().optional().describe('String or JSON object.')`,
      `zod.union([zod.string(), zod.object({}).passthrough()]).optional().describe('String or JSON object.')`,
      "x-string-or-object zod (optional) → union(string, object)",
    );
    // Default variant: fields declared with a default (e.g.
    // PromptTemplateData.response_format's default="text") emit
    // `.default(<generated constant>)` between .passthrough() and .describe().
    // Generic over the constant name so any future string-or-object field
    // with a default is rewritten too, and fail-loud so the union can't
    // silently regress to object-only for exactly these sites again.
    zod = assertReplaceRegex(
      zod,
      /zod\.object\(\{\n\n\}\)\.passthrough\(\)\.default\((\w+)\)\.describe\('String or JSON object\.'\)/g,
      "zod.union([zod.string(), zod.object({}).passthrough()]).default($1).describe('String or JSON object.')",
      "x-string-or-object zod (default) → union(string, object)",
    );
    // Required variant: kept for forward-compat. No StringOrObjectField is
    // currently declared without `required=False`, so this is intentionally
    // soft — silent no-op is fine because the optional variant above is the
    // one that locks today's behavior.
    zod = zod.replaceAll(
      `zod.object({\n\n}).passthrough().describe('String or JSON object.')`,
      `zod.union([zod.string(), zod.object({}).passthrough()]).describe('String or JSON object.')`,
    );

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

    fs.writeFileSync(zodOutputPath, zod);
  }
  await formatGeneratedFiles();
  normalizeGeneratedFileEndings();
}

const swagger = JSON.parse(fs.readFileSync(swaggerPath, "utf8"));
const managementApiSwagger = buildManagementApiSwagger(swagger);
const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "futureagi-openapi-"));
const tempSchemaPath = path.join(tempDir, "management-openapi.json");
fs.writeFileSync(tempSchemaPath, JSON.stringify(managementApiSwagger, null, 2));

const before = snapshotGeneratedFiles();

try {
  await runGeneration(tempSchemaPath);
} catch (error) {
  if (process.argv.includes("--check")) restoreSnapshot(before);
  throw error;
} finally {
  fs.rmSync(tempDir, { recursive: true, force: true });
}

if (process.argv.includes("--check")) {
  const after = snapshotGeneratedFiles();
  const filePaths = new Set([...before.keys(), ...after.keys()]);
  const changed = [...filePaths].filter(
    (filePath) => before.get(filePath) !== after.get(filePath),
  );
  restoreSnapshot(before);
  if (changed.length) {
    console.error(
      [
        "Generated OpenAPI clients are out of date. Run `yarn contracts:generate`.",
        ...changed.map(
          (filePath) => `  - ${path.relative(frontendRoot, filePath)}`,
        ),
      ].join("\n"),
    );
    process.exit(1);
  }
}
