/* eslint-env node */
/* eslint-disable no-console */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { generate } from "orval";
import prettier from "prettier";

import { buildManagementApiSwagger } from "./openapi-client-schema.mjs";

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

  const interruptionNullableFields = {
    RunCallApi: ["avg_stop_time_after_interruption", "ai_interruption_count"],
    GroupAggregatesApi: [
      "avg_stop_time_after_interruption",
      "ai_interruptions",
    ],
  };
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
    "eval_rollup",
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

    // BEGIN F44 optional owners types
    // tfc.settings.openapi always advertises these; runtime remains default-OFF.
    for (const field of ["created_by", "created_by_name"]) {
      schemas = assertReplaceInNamedBlock(
        schemas,
        "export interface DatasetListItemApi {",
        `${field}?: string;`,
        `${field}?: string | null;`,
        `DatasetListItemApi.${field} optional nullable`,
      );
    }
    // The pre-existing nullable folder UUID loses x-nullable in Orval too.
    schemas = assertReplaceInNamedBlock(
      schemas,
      "export interface DatasetListItemApi {",
      "folder_id: string;",
      "folder_id: string | null;",
      "DatasetListItemApi.folder_id required nullable",
    );
    // END F44 optional owners types

    for (const [typeName, fields] of Object.entries(
      interruptionNullableFields,
    )) {
      for (const field of fields) {
        schemas = assertReplaceInNamedBlock(
          schemas,
          `export interface ${typeName} {`,
          `${field}: number;`,
          `${field}: number | null;`,
          `${typeName}.${field} nullable`,
        );
      }
    }

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
      "EvalRollupSpanApiValue",
      "EvalRollupEvalApiChoicesMap",
      "EvalRollupEvalApiAggregate",
      "DatasetVersionItemCreateApiExpected",
      "DatasetVersionItemReadApiExpected",
      "ExternalRunBindingApiBindingConfig",
      "ExternalRunBindingApiPromptMessages",
      "ExternalRunBindingApiConfigSnapshot",
      "ExternalRunBindingApiChoiceScores",
      "ExternalRunResultApiOutput",
      "ExternalRunResultApiError",
      "ExternalRunResultApiTokenUsage",
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

    for (const alias of [
      "DatasetVersionItemCreateApiInput",
      "DatasetVersionItemReadApiInput",
    ]) {
      schemas = assertReplace(
        schemas,
        `export type ${alias} = { [key: string]: unknown };`,
        `export type ${alias} = Exclude<JsonValueApi, null>;`,
        `${alias} excludes top-level null`,
      );
    }
    for (const alias of [
      "DatasetVersionCreateApiMetadata",
      "DatasetVersionItemCreateApiMetadata",
      "DatasetVersionItemReadApiMetadata",
      "RunComparisonRequestApiMetadataEquals",
      "ComparisonSelectionApiMetadataEquals",
    ]) {
      schemas = assertReplaceInNamedBlock(
        schemas,
        `export type ${alias} = {`,
        "[key: string]: { [key: string]: unknown }",
        "[key: string]: JsonValueApi",
        `${alias} JSON values`,
      );
    }
    for (const [typeName, fields] of [
      ["DatasetVersionCreateApi", ["parent_version_id"]],
      [
        "DatasetVersionItemCreateApi",
        [
          "example_id",
          "external_id",
          "source_trace_id",
          "source_span_id",
          "legacy_row_id",
        ],
      ],
    ]) {
      for (const field of fields) {
        schemas = assertReplaceInNamedBlock(
          schemas,
          `export interface ${typeName} {`,
          `${field}?: string;`,
          `${field}?: string | null;`,
          `${typeName}.${field} nullable`,
        );
      }
    }

    for (const alias of [
      "ExternalRunCreateRequestApiModelConfig",
      "ExternalRunCreateRequestApiMetadata",
      "ExternalRunHeaderApiModelConfig",
      "ExternalRunHeaderApiMetadata",
      "OutputDiffSideApiOutputPreview",
    ]) {
      if (alias === "OutputDiffSideApiOutputPreview") {
        schemas = assertReplace(
          schemas,
          `export type ${alias} = { [key: string]: unknown };`,
          `export type ${alias} = JsonValueApi;`,
          `${alias} recursive JSON value`,
        );
        continue;
      }
      schemas = assertReplace(
        schemas,
        `export type ${alias} = {[key: string]: unknown};`,
        `export type ${alias} = { [key: string]: JsonValueApi };`,
        `${alias} recursive JSON object values`,
      );
    }
    for (const [typeName, fields] of [
      ["ExternalRunCreateRequestApi", ["experiment_id"]],
      [
        "ExternalRunHeaderApi",
        [
          "ordinal",
          "label",
          "app_url",
          "experiment_id",
          "started_at",
          "completed_at",
        ],
      ],
      [
        "ExternalRunBindingApi",
        [
          "binding_model",
          "criteria",
          "model",
          "output_type_normalized",
          "pass_threshold",
        ],
      ],
      [
        "ExternalRunResultApi",
        ["trace_id", "root_span_id", "latency_ms", "cost"],
      ],
      ["ExternalRunScoreApi", ["value", "explanation", "trace_id", "span_id"]],
      ["ExternalRunItemReceiptApi", ["trace_id", "root_span_id"]],
      [
        "DatasetVersionReadApi",
        ["parent_version", "created_by", "organization", "workspace"],
      ],
      ["DatasetVersionItemReadApi", ["external_id", "legacy_row"]],
      ["RunComparisonRequestApi", ["passing_score"]],
      ["ReleaseGateReportApi", ["passing_score"]],
      ["ReleaseGateBlockerApi", ["example_id", "trial_number"]],
      [
        "RunAggregateApi",
        ["score_average", "score_min", "score_max", "pass_count", "pass_rate"],
      ],
      [
        "TrialAggregateApi",
        ["score_average", "score_min", "score_max", "score_stddev"],
      ],
      ["TrialDeltaAggregateApi", ["delta_average", "delta_stddev"]],
      [
        "RunCaseComparisonApi",
        [
          "baseline_status",
          "candidate_status",
          "baseline_score",
          "candidate_score",
          "score_delta",
          "missing_side",
        ],
      ],
      ["RunTraceReferenceApi", ["trace_id", "root_span_id"]],
      ["RunCaseTraceReferencesApi", ["baseline", "candidate"]],
      [
        "OutputDiffSideApi",
        [
          "item_id",
          "status",
          "output_digest",
          "output_bytes",
          "output_preview",
        ],
      ],
      ["OutputDiffComparisonApi", ["missing_side", "changed_path_count"]],
      ["ComparisonSelectionApi", ["split_name"]],
    ]) {
      for (const field of fields) {
        schemas = assertReplaceRegexInNamedBlock(
          schemas,
          `export interface ${typeName} {`,
          new RegExp(`(^\\s*(?:readonly )?${field}\\??: [^;]+)(;)$`, "m"),
          "$1 | null$2",
          `${typeName}.${field} nullable`,
        );
      }
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

    // Eval rollups use nullable JSON/scalar fields in the wire response. Keep
    // the generated TypeScript contract aligned with the DRF serializer rather
    // than requiring clients to pretend omitted values are never explicit null.
    for (const [field, type] of [
      ["value", "EvalRollupSpanApiValue"],
      ["explanation", "string"],
      ["status", "string"],
    ]) {
      schemas = assertReplaceInNamedBlock(
        schemas,
        "export interface EvalRollupSpanApi {",
        `${field}?: ${type};`,
        `${field}?: ${type} | null;`,
        `EvalRollupSpanApi.${field} nullable`,
      );
    }
    for (const [field, type] of [
      ["output_type", "string"],
      ["template_type", "string"],
      ["target_type", "EvalRollupEvalApiTargetType"],
      ["choices_map", "EvalRollupEvalApiChoicesMap"],
    ]) {
      schemas = assertReplaceInNamedBlock(
        schemas,
        "export interface EvalRollupEvalApi {",
        `${field}?: ${type};`,
        `${field}?: ${type} | null;`,
        `EvalRollupEvalApi.${field} nullable`,
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

    schemas = assertReplaceInNamedBlock(
      schemas,
      "export interface SubGoalResultApi {",
      "passed: boolean;",
      "passed: boolean | null;",
      "SubGoalResultApi.passed nullable",
    );

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
      // Non-eval columns serialize target_type as an explicit null.
      schemas = assertReplaceInNamedBlock(
        schemas,
        `export interface ${configType} {`,
        `target_type?: ${configType}TargetType;`,
        `target_type?: ${configType}TargetType | null;`,
        `${configType} nullable target_type`,
      );
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

    // Orval drops Swagger 2.0 x-nullable: environment versions carry null labels
    // (unnumbered drafts), creators, origins, hashes, parents and pre-versioning
    // diffs. A draft item is a version item plus its editor (its parent and
    // default labels match the "label" anchor too).
    for (const [typeName, extra] of [
      ["HarnessVersionApi", []],
      ["HarnessDraftItemApi", [["last_edited_by", "HarnessVersionPersonApi"]]],
    ]) {
      for (const [field, valueType] of [
        ["label", "string"],
        ["created_by", "HarnessVersionPersonApi"],
        ["origin", "string"],
        ["hash", "string"],
        ["parent_id", "string"],
        ["derived_from_agent_version_id", "string"],
        ["scenario_count", "number"],
        ["source", `${typeName}Source`],
        ["prompt", "string"],
        ["preflight_status", "string"],
        ["preflight_report", `${typeName}PreflightReport`],
        ["preflighted_at", "string"],
        ["check_started_at", "string"],
        ["credential_names", "string[]"],
        ["required_variable_names", "string[]"],
        ["activation_outcome", "string"],
        ["requested_here", "boolean"],
        ["pickable", "boolean"],
        ["discardable", "boolean"],
        ...extra,
      ]) {
        schemas = assertReplaceInNamedBlock(
          schemas,
          `export interface ${typeName} {`,
          `${field}: ${valueType};`,
          `${field}: ${valueType} | null;`,
          `${typeName}.${field} nullable`,
        );
      }
    }
    // A piece without unsaved changes has no draft; a Save names the parent it saw.
    for (const piece of ["environment", "suite"]) {
      schemas = assertReplaceInNamedBlock(
        schemas,
        "export interface HarnessDraftsApi {",
        `${piece}: HarnessDraftItemApi;`,
        `${piece}: HarnessDraftItemApi | null;`,
        `HarnessDraftsApi.${piece} nullable`,
      );
    }
    schemas = assertReplaceInNamedBlock(
      schemas,
      "export interface HarnessDraftSaveItemApi {",
      "expected_parent_id: string;",
      "expected_parent_id: string | null;",
      "HarnessDraftSaveItemApi.expected_parent_id nullable",
    );
    schemas = assertReplaceInNamedBlock(
      schemas,
      "export interface HarnessVersionDiffApi {",
      "diff: HarnessVersionDiffApiDiff;",
      "diff: HarnessVersionDiffApiDiff | null;",
      "HarnessVersionDiffApi.diff nullable",
    );
    schemas = assertReplaceInNamedBlock(
      schemas,
      "export interface HarnessVersionDiffApi {",
      "snapshot_diff?: HarnessVersionDiffApiSnapshotDiff;",
      "snapshot_diff?: HarnessVersionDiffApiSnapshotDiff | null;",
      "HarnessVersionDiffApi.snapshot_diff nullable",
    );
    // A move sends the default it saw, which is null for a piece without one.
    schemas = assertReplaceInNamedBlock(
      schemas,
      "export interface HarnessDefaultMoveApi {",
      "expected_default_id: string;",
      "expected_default_id: string | null;",
      "HarnessDefaultMoveApi.expected_default_id nullable",
    );
    // A run's pinned versions are null for a piece it has none of; a bundle-wide
    // warning names no piece; and the cache does not apply to an unassembled run.
    for (const piece of ["agent", "environment", "suite"]) {
      schemas = assertReplaceInNamedBlock(
        schemas,
        "export interface HarnessRunPinnedVersionsApi {",
        `${piece}: RunVersionLabelApi;`,
        `${piece}: RunVersionLabelApi | null;`,
        `HarnessRunPinnedVersionsApi.${piece} nullable`,
      );
      schemas = assertReplaceInNamedBlock(
        schemas,
        "export interface HarnessRunCreateApi {",
        `${piece}_version_id?: string;`,
        `${piece}_version_id?: string | null;`,
        `HarnessRunCreateApi.${piece}_version_id nullable`,
      );
    }
    schemas = assertReplaceInNamedBlock(
      schemas,
      "export interface HarnessRunWarningApi {",
      "piece: HarnessRunWarningApiPiece;",
      "piece: HarnessRunWarningApiPiece | null;",
      "HarnessRunWarningApi.piece nullable",
    );
    schemas = assertReplaceInNamedBlock(
      schemas,
      "export interface HarnessRunPlanApi {",
      "bundle_cached: boolean;",
      "bundle_cached: boolean | null;",
      "HarnessRunPlanApi.bundle_cached nullable",
    );
    // A compare side with no decided trials has no rate; a pair missing from one
    // run has no verdict, interval or side; only a removed key has a reason.
    for (const [typeName, fields] of [
      [
        "HarnessCompareSideApi",
        ["  pass_rate: number;", "  eval_pass_rate: number;"],
      ],
      [
        "HarnessComparePairApi",
        [
          "verdict: HarnessComparePairApiVerdict;",
          "difference: number;",
          "interval: HarnessCompareIntervalApi;",
          "current: HarnessCompareSideApi;",
          "against: HarnessCompareSideApi;",
          "reason: string;",
          "removed_in: RunVersionLabelApi;",
        ],
      ],
      [
        "HarnessCompareSummaryApi",
        ["current_pass_rate: number;", "against_pass_rate: number;"],
      ],
      ["HarnessCompareVersionsApi", ["against: HarnessRunPinnedVersionsApi;"]],
      ["HarnessCompareApi", ["against_execution_id: string;"]],
    ]) {
      for (const field of fields) {
        schemas = assertReplaceInNamedBlock(
          schemas,
          `export interface ${typeName} {`,
          field,
          field.replace(/;$/, " | null;"),
          `${typeName}.${field.split(":")[0].trim()} nullable`,
        );
      }
    }
    // A run's version labels are null for an unnumbered version, and its pins null
    // when it has none.
    schemas = assertReplaceInNamedBlock(
      schemas,
      "export interface RunVersionLabelApi {",
      "readonly label?: string;",
      "readonly label?: string | null;",
      "RunVersionLabelApi.label nullable",
    );
    // Orval drops Swagger 2.0 x-nullable: a nudge's versions, outcome and answer are
    // null until they apply, and a file's counts and reasons when unknown.
    for (const [typeName, fields] of [
      ["HarnessNudgeReplacementApi", [["trigger_label", "string"]]],
      [
        "HarnessNudgeFileApi",
        [
          ["previous_path", "string"],
          ["additions", "number"],
          ["deletions", "number"],
          ["patch_omitted", "string"],
        ],
      ],
      [
        "HarnessNudgeFactsApi",
        [
          ["files_total?", "number"],
          ["files_unavailable?", "string"],
          ["environment_diff?", "HarnessNudgeFactsApiEnvironmentDiff"],
          ["from_environment_version_id?", "string"],
        ],
      ],
      [
        "HarnessNudgeApi",
        [
          ["trigger_agent_version", "RunVersionLabelApi"],
          ["base_agent_version", "RunVersionLabelApi"],
          ["base_environment_version", "RunVersionLabelApi"],
          ["base_suite_version", "RunVersionLabelApi"],
          ["result_environment_version", "RunVersionLabelApi"],
          ["result_suite_version", "RunVersionLabelApi"],
          ["summary", "HarnessNudgeApiSummary"],
          ["error", "HarnessNudgeErrorApi"],
          ["closed_at", "string"],
          ["replaced_by", "HarnessNudgeReplacementApi"],
          ["card_message_id", "string"],
          ["answered_by", "HarnessVersionPersonApi"],
          ["answered_at", "string"],
        ],
      ],
    ]) {
      for (const [field, valueType] of fields) {
        schemas = assertReplaceInNamedBlock(
          schemas,
          `export interface ${typeName} {`,
          `${field}: ${valueType};`,
          `${field}: ${valueType} | null;`,
          `${typeName}.${field} nullable`,
        );
      }
    }
    for (const typeName of ["TestExecutionItemResponseApi", "RunExecutionApi"]) {
      for (const field of [
        "environment_version",
        "suite_version",
        "pinned_agent_version",
      ]) {
        schemas = assertReplaceInNamedBlock(
          schemas,
          `export interface ${typeName} {`,
          `${field}?: RunVersionLabelApi;`,
          `${field}?: RunVersionLabelApi | null;`,
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

    // A key that never expires syncs to the gateway with expires_at: null.
    schemas = assertReplaceInNamedBlock(
      schemas,
      "export interface APIKeyBulkItemApi {",
      "expires_at: string;",
      "expires_at: string | null;",
      "APIKeyBulkItemApi.expires_at nullable",
    );

    // shared-edit(F46): a copy preview has no persisted dataset ID yet.
    schemas = assertReplaceInNamedBlock(
      schemas,
      "export interface DatasetTransferResultApi {",
      "\n  dataset_id: string;",
      "\n  dataset_id: string | null;",
      "DatasetTransferResultApi.dataset_id nullable",
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
      "AgentccProviderCredentialCreateApiExtraConfig",
      "AgentccProviderCredentialUpdateApiExtraConfig",
    ]) {
      schemas = assertReplace(
        schemas,
        `export type ${recordType} = {[key: string]: { [key: string]: unknown }};`,
        `export type ${recordType} = { [key: string]: JsonValueApi };`,
        `${recordType} → recursive JSON values`,
      );
    }
    for (const [typeName, field] of [
      ["ConversationEvidenceRowApi", "end_time"],
      ["ConversationDossierApi", "agent_instructions"],
      ["ConversationCallApi", "duration_seconds"],
      ["ConversationTurnApi", "start"],
      ["ConversationTurnApi", "end"],
      ["ConversationTurnApi", "at"],
    ]) {
      schemas = assertReplaceRegexInNamedBlock(
        schemas,
        `export interface ${typeName} {`,
        new RegExp(`(^\\s*${field}\\??: [^;]+)(;)$`, "m"),
        "$1 | null$2",
        `${typeName}.${field} nullable`,
      );
    }

    fs.writeFileSync(schemasOutputPath, schemas);
  }

  if (fs.existsSync(zodOutputPath)) {
    let zod = fs.readFileSync(zodOutputPath, "utf8");

    // BEGIN F44 optional owners zod
    for (const [field, parser] of [
      ["created_by", "zod.string().uuid()"],
      ["created_by_name", "zod.string()"],
    ]) {
      zod = assertReplaceInNamedBlock(
        zod,
        "export const ModelHubDevelopsGetDatasetsListResponse = zod.object({",
        `"${field}": ${parser}.optional(),`,
        `"${field}": ${parser}.nullable().optional(),`,
        `DatasetListItemApi.${field} optional nullable`,
      );
    }
    zod = assertReplaceInNamedBlock(
      zod,
      "export const ModelHubDevelopsGetDatasetsListResponse = zod.object({",
      '"folder_id": zod.string().uuid(),',
      '"folder_id": zod.string().uuid().nullable(),',
      "DatasetListItemApi.folder_id required nullable",
    );
    // END F44 optional owners zod

    for (const field of new Set(
      Object.values(interruptionNullableFields).flat(),
    )) {
      zod = assertReplaceInNamedBlock(
        zod,
        "export const SimulateV3TestExecutionCallsResponse =",
        `"${field}": zod.number()`,
        `"${field}": zod.number().nullable()`,
        `SimulateV3TestExecutionCallsResponse.${field} nullable`,
      );
    }

    // The same nullable version fields, in every list of the versions responses.
    const VERSIONS_RESPONSES = [
      "SimulateApiHarnessEnvironmentsVersionsResponse",
      "SimulateApiHarnessEnvironmentsVersionsMoveDefaultResponse",
      "SimulateApiHarnessEnvironmentsVersionsAgentDiscardAgentVersionResponse",
      "SimulateApiHarnessEnvironmentsDraftsResponse",
      "SimulateApiHarnessEnvironmentsDraftsSaveDraftsResponse",
    ];
    for (const [anchor, label] of [
      ['"label": zod.string().min(1)', "label"],
      ['"origin": zod.string().min(1)', "origin"],
      ['"hash": zod.string().min(1)', "hash"],
      ['"parent_id": zod.string().uuid()', "parent_id"],
      [
        '"derived_from_agent_version_id": zod.string().uuid()',
        "derived_from_agent_version_id",
      ],
      ['"scenario_count": zod.number()', "scenario_count"],
      ['"preflight_status": zod.string().min(1)', "preflight_status"],
      [
        `"created_by": zod.object({
  "id": zod.string().min(1),
  "name": zod.string()
})`,
        "created_by",
      ],
    ]) {
      for (const response of VERSIONS_RESPONSES) {
        zod = assertReplaceInNamedBlock(
          zod,
          `export const ${response} =`,
          anchor,
          `${anchor}.nullable()`,
          `${response}.${label} nullable`,
        );
      }
    }
    // Only agent versions are checked, hold credentials or can be picked.
    for (const response of VERSIONS_RESPONSES) {
      for (const [pattern, label] of [
        [
          /("preflight_report": zod\.object\(\{\s*\}\)\.passthrough\(\)(?:\.describe\([^)]*\))?)/g,
          "preflight_report",
        ],
        [
          /("preflighted_at": zod\.string\(\)\.datetime\(\{[^}]*\}\))/g,
          "preflighted_at",
        ],
        [
          /("check_started_at": zod\.string\(\)\.datetime\(\{[^}]*\}\)(?:\.describe\([^)]*\))?)/g,
          "check_started_at",
        ],
        [
          /("credential_names": zod\.array\(zod\.string\(\)\.min\(1\)\)(?:\.describe\([^)]*\))?)/g,
          "credential_names",
        ],
        [
          /("required_variable_names": zod\.array\(zod\.string\(\)\.min\(1\)\)(?:\.describe\([^)]*\))?)/g,
          "required_variable_names",
        ],
        [
          /("activation_outcome": zod\.string\(\)\.min\(1\)(?:\.describe\([^)]*\))?)/g,
          "activation_outcome",
        ],
        [
          /("requested_here": zod\.boolean\(\)(?:\.describe\([^)]*\))?)/g,
          "requested_here",
        ],
        [/("pickable": zod\.boolean\(\)(?:\.describe\([^)]*\))?)/g, "pickable"],
        [
          /("source": zod\.object\(\{\s*\}\)\.passthrough\(\)(?:\.describe\([^)]*\))?)/g,
          "source",
        ],
        [/("prompt": zod\.string\(\)(?:\.describe\([^)]*\))?)/g, "prompt"],
        [
          /("discardable": zod\.boolean\(\)(?:\.describe\([^)]*\))?)/g,
          "discardable",
        ],
      ]) {
        zod = assertReplaceRegexInNamedBlock(
          zod,
          `export const ${response} =`,
          pattern,
          "$1.nullable()",
          `${response}.${label} nullable`,
        );
      }
    }
    // A draft item adds its editor and labels; a piece without a draft is null, and a
    // Save names the parent it saw, null for none.
    const draftsResponse =
      "export const SimulateApiHarnessEnvironmentsDraftsResponse =";
    for (const [anchor, label] of [
      ['"parent_label": zod.string().min(1)', "parent_label"],
      ['"default_label": zod.string().min(1)', "default_label"],
      [
        `"last_edited_by": zod.object({
  "id": zod.string().min(1),
  "name": zod.string()
})`,
        "last_edited_by",
      ],
    ]) {
      zod = assertReplaceInNamedBlock(
        zod,
        draftsResponse,
        anchor,
        `${anchor}.nullable()`,
        `SimulateApiHarnessEnvironmentsDraftsResponse.${label} nullable`,
      );
    }
    zod = assertReplaceRegexInNamedBlock(
      zod,
      draftsResponse,
      /("(?:environment|suite)": zod\.object\(\{(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*\}\))/g,
      "$1.nullable()",
      "SimulateApiHarnessEnvironmentsDraftsResponse.pieces nullable",
    );
    zod = assertReplaceInNamedBlock(
      zod,
      "export const SimulateApiHarnessEnvironmentsDraftsSaveDraftsBody =",
      '"expected_parent_id": zod.string().uuid()',
      '"expected_parent_id": zod.string().uuid().nullable()',
      "SimulateApiHarnessEnvironmentsDraftsSaveDraftsBody.expected_parent_id nullable",
    );
    zod = assertReplaceInNamedBlock(
      zod,
      "export const SimulateApiHarnessEnvironmentsVersionsMoveDefaultBody =",
      '"expected_default_id": zod.string().uuid()',
      '"expected_default_id": zod.string().uuid().nullable()',
      "SimulateApiHarnessEnvironmentsVersionsMoveDefaultBody.expected_default_id nullable",
    );
    zod = assertReplaceInNamedBlock(
      zod,
      "export const SimulateApiHarnessEnvironmentsVersionsVersionDiffResponse =",
      `"diff": zod.object({

}).passthrough()`,
      `"diff": zod.object({

}).passthrough().nullable()`,
      "SimulateApiHarnessEnvironmentsVersionsVersionDiffResponse.diff nullable",
    );
    zod = assertReplaceRegexInNamedBlock(
      zod,
      "export const SimulateApiHarnessEnvironmentsVersionsVersionDiffResponse =",
      /("snapshot_diff": zod\.object\(\{\s*\}\)\.passthrough\(\))\.optional\(\)/g,
      "$1.nullish()",
      "SimulateApiHarnessEnvironmentsVersionsVersionDiffResponse.snapshot_diff nullable",
    );
    // The same nullable run-plan fields, and picks a run may send as null.
    zod = assertReplaceRegexInNamedBlock(
      zod,
      "export const SimulateApiHarnessEnvironmentsRunPlanResponse =",
      /("(?:agent|environment|suite)": zod\.object\(\{[^{}]*\}\))/g,
      "$1.nullable()",
      "SimulateApiHarnessEnvironmentsRunPlanResponse.versions nullable",
    );
    for (const [anchor, label] of [
      ['"label": zod.string().min(1).optional()', "label"],
      [`"piece": zod.enum(['agent', 'environment', 'suite'])`, "piece"],
      ['"bundle_cached": zod.boolean()', "bundle_cached"],
    ]) {
      zod = assertReplaceInNamedBlock(
        zod,
        "export const SimulateApiHarnessEnvironmentsRunPlanResponse =",
        anchor,
        anchor.endsWith(".optional()")
          ? anchor.replace(/\.optional\(\)$/, ".nullish()")
          : `${anchor}.nullable()`,
        `SimulateApiHarnessEnvironmentsRunPlanResponse.${label} nullable`,
      );
    }
    // The same nullable compare fields. Only a pair's sides have no nested object,
    // and only the against run's versions may be null as a whole.
    const compareResponse =
      "export const SimulateApiHarnessEnvironmentsCompareResponse =";
    for (const [pattern, label] of [
      [
        /("(?:interval|current|removed_in)": zod\.object\(\{[^{}]*\}\))/g,
        "pair",
      ],
      [/("against": zod\.object\(\{(?:[^{}]|\{[^{}]*\})*\}\))/g, "against"],
      [
        /("(?:agent|environment|suite)": zod\.object\(\{[^{}]*\}\))/g,
        "versions",
      ],
    ]) {
      zod = assertReplaceRegexInNamedBlock(
        zod,
        compareResponse,
        pattern,
        "$1.nullable()",
        `SimulateApiHarnessEnvironmentsCompareResponse.${label} nullable`,
      );
    }
    for (const [anchor, label] of [
      ['"against_execution_id": zod.string().uuid()', "against_execution_id"],
      [
        `"verdict": zod.enum(['improved', 'regressed', 'no_meaningful_change', 'inconclusive'])`,
        "verdict",
      ],
      ['"difference": zod.number()', "difference"],
      ['"pass_rate": zod.number()', "pass_rate"],
      ['"eval_pass_rate": zod.number()', "eval_pass_rate"],
      ['"reason": zod.string().min(1)', "reason"],
      ['"current_pass_rate": zod.number()', "current_pass_rate"],
      ['"against_pass_rate": zod.number()', "against_pass_rate"],
      ['"label": zod.string().min(1).optional()', "label"],
    ]) {
      zod = assertReplaceInNamedBlock(
        zod,
        compareResponse,
        anchor,
        anchor.endsWith(".optional()")
          ? anchor.replace(/\.optional\(\)$/, ".nullish()")
          : `${anchor}.nullable()`,
        `SimulateApiHarnessEnvironmentsCompareResponse.${label} nullable`,
      );
    }
    for (const response of [
      "SimulateRunTestsExecutionsListResponse",
      "SimulateV3TestExecutionCallsResponse",
    ]) {
      zod = assertReplaceRegexInNamedBlock(
        zod,
        `export const ${response} = zod.object({`,
        /("(?:environment_version|suite_version|pinned_agent_version)": zod\.object\(\{[^{}]*"label": zod\.string\(\)\.min\(1\))\.optional\(\)([^{}]*\}\))\.optional\(\)/g,
        "$1.nullish()$2.nullish()",
        `${response} run versions nullable`,
      );
    }
    // The same nullable nudge fields.
    for (const response of [
      "SimulateApiHarnessEnvironmentsNudgesResponseItem",
      "SimulateApiHarnessEnvironmentsNudgesAnswerNudgeResponse",
      "SimulateApiHarnessEnvironmentsNudgesStopNudgeResponse",
    ]) {
      for (const [pattern, label] of [
        [
          /("(?:label|trigger_label|previous_path|patch_omitted|files_unavailable)": zod\.string\(\)\.min\(1\))/g,
          "texts",
        ],
        [/("(?:additions|deletions|files_total)": zod\.number\(\))/g, "counts"],
        [
          /("(?:environment_diff|summary)": zod\.object\(\{\s*\}\)\.passthrough\(\))/g,
          "documents",
        ],
        [
          /("(?:from_environment_version_id|card_message_id)": zod\.string\(\)\.uuid\(\))/g,
          "ids",
        ],
        [
          // One level of nesting: an error's problems are objects too.
          /("(?:trigger_agent_version|base_agent_version|base_environment_version|base_suite_version|result_environment_version|result_suite_version|replaced_by|error|answered_by)": zod\.object\(\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}\))/g,
          "objects",
        ],
        [
          /("(?:closed_at|answered_at)": zod\.string\(\)\.datetime\(\{[^}]*\}\))/g,
          "times",
        ],
      ]) {
        zod = assertReplaceRegexInNamedBlock(
          zod,
          `export const ${response} =`,
          pattern,
          "$1.nullable()",
          `${response}.${label} nullable`,
        );
      }
    }
    for (const body of [
      "SimulateApiHarnessEnvironmentsRunBody",
      "SimulateApiHarnessJobsRunsBody",
    ]) {
      zod = assertReplaceRegexInNamedBlock(
        zod,
        `export const ${body} = zod.object({`,
        /("(?:agent|environment|suite)_version_id": zod\.string\(\)\.uuid\(\))\.optional\(\)/g,
        "$1.nullish()",
        `${body} version picks nullable`,
      );
    }

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

    // Provider settings carry scalar values too (weight, api_path_prefix),
    // not just objects. Preserve JsonValueField's contract in Orval output.
    for (const bodyName of [
      "AgentccProviderCredentialsCreateBody",
      "AgentccProviderCredentialsUpdateBody",
      "AgentccProviderCredentialsPartialUpdateBody",
    ]) {
      zod = assertReplaceRegexInNamedBlock(
        zod,
        `export const ${bodyName} = zod.object({`,
        /zod\.object\(\{\n\n\}\)\.passthrough\(\)(?=(?:\.(?:optional|nullish|nullable)\(\)|\.default\([^)]*\))*\.describe\('Any valid JSON value\.'\))/g,
        "jsonValueSchema",
        `${bodyName} settings → recursive JSON value`,
      );
    }

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
      for (const [field, pattern] of [
        ["output_type", "zod\\.string\\(\\)\\.min\\(1\\)"],
        ["template_type", "zod\\.string\\(\\)\\.min\\(1\\)"],
        ["target_type", "zod\\.enum\\(\\['span', 'trace'\\]\\)"],
        ["explanation", "zod\\.string\\(\\)\\.min\\(1\\)"],
        ["status", "zod\\.string\\(\\)\\.min\\(1\\)"],
      ]) {
        zod = assertReplaceRegexInNamedBlock(
          zod,
          declaration,
          new RegExp(
            `("eval_rollup": zod\\.object\\(\\{[\\s\\S]*?"${field}": ${pattern})\\.optional\\(\\)`,
          ),
          "$1.nullish()",
          `${responseName} eval rollup ${field} nullable`,
        );
      }
    }
    zod = assertReplaceInNamedBlock(
      zod,
      "export const TracerTraceVoiceCallDetailResponse = zod.object({",
      '"error": zod.boolean().optional()\n}).optional(),',
      '"error": zod.boolean().optional()\n}).nullish(),',
      "TracerTraceVoiceCallDetailResponse.eval_rollup nullable",
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
    const externalRunExports = [
      "ExternalExperimentRunsCreateBody",
      "ExternalExperimentRunsCreateResponse",
      "ExternalExperimentRunResultsIngestBody",
      "ExternalExperimentRunResultsIngestResponse",
    ];
    for (const exportName of externalRunExports) {
      const declaration = `export const ${exportName} = zod.object({`;
      if (exportName !== "ExternalExperimentRunsCreateBody") {
        zod = assertReplaceRegexInNamedBlock(
          zod,
          declaration,
          /zod\.object\(\{\n\n\}\)\.passthrough\(\)(?=(?:\.(?:optional|nullish|nullable)\(\)|\.default\([^)]*\))*\.describe\('Any valid JSON value\.'\))/g,
          "jsonValueSchema",
          `${exportName} arbitrary JSON values`,
        );
      }
      if (exportName !== "ExternalExperimentRunResultsIngestBody") {
        zod = assertReplaceInNamedBlock(
          zod,
          declaration,
          "zod.record(zod.string(), zod.unknown())",
          "zod.record(zod.string(), jsonValueSchema)",
          `${exportName} JSON object values`,
        );
      }
      const nullableFields =
        exportName === "ExternalExperimentRunsCreateBody"
          ? ["experiment_id"]
          : exportName === "ExternalExperimentRunResultsIngestBody"
            ? [
                "trace_id",
                "root_span_id",
                "span_id",
                "value",
                "explanation",
                "latency_ms",
                "cost",
              ]
            : [
                "ordinal",
                "label",
                "app_url",
                "experiment_id",
                "started_at",
                "completed_at",
                "binding_model",
                "criteria",
                "model",
                "output_type_normalized",
                "pass_threshold",
                ...(exportName.endsWith("IngestResponse")
                  ? [
                      "trace_id",
                      "root_span_id",
                      "span_id",
                      "value",
                      "explanation",
                    ]
                  : []),
              ];
      for (const field of nullableFields) {
        zod = assertReplaceRegexInNamedBlock(
          zod,
          declaration,
          new RegExp(`("${field}": zod\\.[^\\n]+?)(,?)$`, "gm"),
          "$1.nullable()$2",
          `${exportName}.${field} nullable`,
        );
      }
    }

    for (const [exportName, anchors] of [
      [
        "ExternalExperimentRunsCreateBody",
        [
          [/("trial_number": [^\n]+)\n\}\)\)/, "$1\n}).strict())"],
          [/("metadata": [^\n]+)\n\}\)/, "$1\n}).strict()"],
        ],
      ],
      [
        "ExternalExperimentRunResultsIngestBody",
        [
          [/("span_id": [^\n]+)\n\}\)\)/, "$1\n}).strict())"],
          [
            /(\.default\(externalExperimentRunResultsIngestBodyItemsItemScoreRefsDefault\))\n\}\)\)/,
            "$1\n}).strict())",
          ],
          [
            /(\.max\(externalExperimentRunResultsIngestBodyItemsMax\))\n\}\)/,
            "$1\n}).strict()",
          ],
        ],
      ],
    ]) {
      for (const [pattern, replacement] of anchors) {
        zod = assertReplaceRegexInNamedBlock(
          zod,
          `export const ${exportName} = zod.object({`,
          pattern,
          replacement,
          `${exportName} rejects unknown nested/request fields`,
        );
      }
    }

    for (const [exportName, nullableFields] of [
      [
        "ModelHubDatasetsVersionsListResponse",
        ["parent_version", "created_by", "organization", "workspace"],
      ],
      [
        "ModelHubDatasetsVersionsReadResponse",
        [
          "parent_version",
          "created_by",
          "organization",
          "workspace",
          "external_id",
          "legacy_row",
        ],
      ],
      ["ExperimentRunsCompareBody", ["passing_score"]],
      [
        "ExperimentRunsCompareResponse",
        [
          "score_average",
          "score_min",
          "score_max",
          "pass_count",
          "pass_rate",
          "baseline_status",
          "candidate_status",
          "baseline_score",
          "candidate_score",
          "score_delta",
          "missing_side",
          "score_stddev",
          "delta_average",
          "delta_stddev",
          "trace_id",
          "root_span_id",
          "passing_score",
          "item_id",
          "output_digest",
          "output_bytes",
          "changed_path_count",
          "split_name",
        ],
      ],
    ]) {
      for (const field of nullableFields) {
        zod = assertReplaceRegexInNamedBlock(
          zod,
          `export const ${exportName} = zod.object({`,
          new RegExp(`("${field}": zod\\.[^\\n]+?)(,?)$`, "gm"),
          "$1.nullable()$2",
          `${exportName}.${field} nullable`,
        );
      }
    }
    zod = assertReplaceRegexInNamedBlock(
      zod,
      "export const ExperimentRunsCompareResponse = zod.object({",
      /("(?:baseline|candidate)": zod\.object\(\{\n\s*"run_item_id":[\s\S]*?\n\s*\}\))/g,
      "$1.nullable()",
      "missing comparison trace references are nullable",
    );
    for (const field of ["max_regressions", "trial_number"]) {
      zod = assertReplaceRegexInNamedBlock(
        zod,
        "export const ExperimentRunsCompareBody = zod.object({",
        new RegExp(`("${field}": zod\\.number\\(\\))`, "g"),
        "$1.int()",
        `release gate ${field} is an integer`,
      );
    }
    for (const [anchor, replacement] of [
      [/("trial_number": [^\n]+)\n\}\)\)/, "$1\n}).strict())"],
      [
        /(\.default\(experimentRunsCompareBodyReleaseGateCriticalCasesDefault\))\n\}\)\.optional\(\)/,
        "$1\n}).strict().optional()",
      ],
      [
        /("diff_case": zod\.object\(\{\n[\s\S]*?\n\}\))\.optional\(\)/,
        "$1.strict().optional()",
      ],
      [/\n\}\)(\s*)$/, "\n}).strict()$1"],
    ]) {
      zod = assertReplaceRegexInNamedBlock(
        zod,
        "export const ExperimentRunsCompareBody = zod.object({",
        anchor,
        replacement,
        "release gate rejects unknown request and nested fields",
      );
    }
    zod = assertReplaceRegexInNamedBlock(
      zod,
      "export const ExperimentRunsCompareResponse = zod.object({",
      /("blockers": zod\.array\(zod\.object\(\{\n\s*"example_id": [^\n]+?)(,?)\n(\s*"trial_number": [^\n]+?)(,?)\n/g,
      "$1.nullable()$2\n$3.nullable()$4\n",
      "release gate empty-plan blocker preserves null identity",
    );
    // metadata_equals values are any JSON value (JsonValueField), at most 20 keys (the serializer's limit).
    zod = assertReplaceRegexInNamedBlock(
      zod,
      "export const ExperimentRunsCompareBody = zod.object({",
      /("metadata_equals": zod\.record\(zod\.string\(\), )zod\.object\(\{\n\n\}\)\.passthrough\(\)(\.describe\('Any valid JSON value\.'\)\))\.optional\(\)/,
      "$1jsonValueSchema$2.refine((value) => Object.keys(value).length <= 20, { message: 'metadata_equals supports at most 20 keys' }).optional()",
      "comparison metadata selector holds at most 20 JSON values",
    );
    zod = assertReplaceRegexInNamedBlock(
      zod,
      "export const ExperimentRunsCompareResponse = zod.object({",
      /zod\.object\(\{\n\n\}\)\.passthrough\(\)(?=\.describe\('Any valid JSON value\.'\))/g,
      "jsonValueSchema",
      "comparison output previews and selection metadata are JSON values",
    );
    // The output diff sides' status is a nullable string; the cases' status enums stay required.
    zod = assertReplaceRegexInNamedBlock(
      zod,
      "export const ExperimentRunsCompareResponse = zod.object({",
      /("status": zod\.string\(\)\.min\(1\))(,?)$/gm,
      "$1.nullable()$2",
      "output diff side status nullable",
    );
    const versionRead =
      "export const ModelHubDatasetsVersionsReadResponse = zod.object({";
    zod = assertReplaceRegexInNamedBlock(
      zod,
      versionRead,
      /zod\.object\(\{\n\n\}\)\.passthrough\(\)(?=(?:\.(?:optional|nullish|nullable)\(\)|\.default\([^)]*\))*\.describe\('Any valid JSON value\.'\))/g,
      "jsonValueSchema",
      "dataset item expected and metadata JSON values",
    );
    zod = assertReplaceInNamedBlock(
      zod,
      versionRead,
      "zod.object({\n\n}).passthrough().optional().describe('Any non-null JSON value; nested nulls are allowed.')",
      "jsonValueSchema.refine((value) => value !== null).optional().describe('Any non-null JSON value; nested nulls are allowed.')",
      "dataset read input preserves non-null JSON values",
    );

    const snapshotBody =
      "export const DatasetVersionsCreateBody = zod.object({";
    zod = assertReplaceRegexInNamedBlock(
      zod,
      snapshotBody,
      /zod\.object\(\{\n\n\}\)\.passthrough\(\)(?=(?:\.(?:optional|nullish|nullable)\(\)|\.default\([^)]*\))*\.describe\('Any valid JSON value\.'\))/g,
      "jsonValueSchema",
      "snapshot expected and metadata JSON values",
    );
    zod = assertReplaceInNamedBlock(
      zod,
      snapshotBody,
      "zod.object({\n\n}).passthrough().describe('Any non-null JSON value; nested nulls are allowed.')",
      "jsonValueSchema.refine((value) => value !== null).describe('Any non-null JSON value; nested nulls are allowed.')",
      "snapshot input excludes top-level null",
    );
    for (const field of [
      "parent_version_id",
      "example_id",
      "external_id",
      "source_trace_id",
      "source_span_id",
      "legacy_row_id",
    ]) {
      zod = assertReplaceRegexInNamedBlock(
        zod,
        snapshotBody,
        new RegExp(`("${field}": zod\\.[^\\n]+)\\.optional\\(\\)`),
        "$1.nullish()",
        `snapshot ${field} nullable`,
      );
    }
    zod = assertReplaceInNamedBlock(
      zod,
      snapshotBody,
      '"legacy_row_id": zod.string().uuid().nullish()\n}))',
      '"legacy_row_id": zod.string().uuid().nullish()\n}).strict())',
      "snapshot item rejects unknown fields",
    );
    zod = assertReplaceInNamedBlock(
      zod,
      snapshotBody,
      '"source_revision": zod.string().max(datasetVersionsCreateBodySourceRevisionMax).optional()\n})',
      '"source_revision": zod.string().max(datasetVersionsCreateBodySourceRevisionMax).optional()\n}).strict()',
      "snapshot body rejects unknown fields",
    );
    const conversationEvidenceResponse =
      "export const TracerInternalErrorFeedV2AttemptsConversationEvidenceCreateResponse = zod.object({";
    zod = assertReplaceRegexInNamedBlock(
      zod,
      conversationEvidenceResponse,
      /zod\.object\(\{\n\n\}\)\.passthrough\(\)(?=(?:\.(?:optional|nullish|nullable)\(\)|\.default\([^)]*\))*\.describe\('Any valid JSON value\.'\))/g,
      "jsonValueSchema",
      "conversation evidence JSON values → recursive JSON value",
    );
    for (const [anchor, label] of [
      [/("end_time": zod\.string\(\)\.datetime\([^)]*\))/, "end_time"],
      [/("agent_instructions": zod\.string\(\))/, "agent_instructions"],
      [/("duration_seconds": zod\.number\(\))/, "duration_seconds"],
      [/("start": zod\.number\(\))/, "turn start"],
      [/("end": zod\.number\(\))/, "turn end"],
      [/("at": zod\.number\(\))/, "turn at"],
    ]) {
      zod = assertReplaceRegexInNamedBlock(
        zod,
        conversationEvidenceResponse,
        anchor,
        "$1.nullable()",
        `conversation evidence ${label} nullable`,
      );
    }

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

    for (const responseName of [
      "SimulateV3CallExecutionDetailResponse",
      "SimulateV3TestExecutionCallsResponse",
    ]) {
      zod = assertReplaceRegexInNamedBlock(
        zod,
        `export const ${responseName} = zod.object({`,
        /("sub_goal_results": zod\.array\(zod\.object\(\{[\s\S]*?"passed": )zod\.boolean\(\)/,
        "$1zod.boolean().nullable()",
        `${responseName}.sub_goal_results.passed nullable`,
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
      zod = assertReplaceRegexInNamedBlock(
        zod,
        `export const ${exportName} = zod.object({`,
        /("target_type": zod\.enum\(\[[^\]]*\]\))\.optional\(\)/,
        "$1.nullish()",
        `${exportName}.target_type nullable`,
      );
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

    zod = assertReplaceInNamedBlock(
      zod,
      "export const AgentccApiKeysBulkListResponse = zod.object({",
      '"expires_at": zod.string().datetime({"offset":true})',
      '"expires_at": zod.string().datetime({"offset":true}).nullable()',
      "AgentccApiKeysBulkListResponse.expires_at nullable",
    );

    // shared-edit(F46): preserve x-nullable without making the ID optional.
    zod = assertReplaceInNamedBlock(
      zod,
      "export const ModelHubDatasetsTransferCreateResponse = zod.object({",
      '"dataset_id": zod.string().uuid(),',
      '"dataset_id": zod.string().uuid().nullable(),',
      "ModelHubDatasetsTransferCreateResponse.dataset_id nullable",
    );

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
  // hook(F49): Preserve the source schema's nullable saved-view continuation.
  const savedViews = await import("./saved-view-contract.mjs");
  savedViews.normalizeSavedViewContract(schemaPath, outputDir);
  // hook(F82): Keep the row read's nullable and JSON cell values.
  const rowRead = await import("./row-read-contract.mjs");
  rowRead.normalizeRowReadContract(schemaPath, outputDir);
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
