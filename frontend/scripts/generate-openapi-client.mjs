/* eslint-env node */
/* eslint-disable no-console */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { generate } from "orval";

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

    fs.writeFileSync(schemasOutputPath, schemas);
  }

  if (fs.existsSync(zodOutputPath)) {
    let zod = fs.readFileSync(zodOutputPath, "utf8");

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

    // AnyValueDictField (`additionalProperties: {}` + `x-json-value: true`,
    // used for dynamic-column table rows in eval usage) is now emitted by
    // orval directly as `zod.record(zod.string(), zod.unknown())` — no
    // post-processing needed. If orval regresses and emits
    // `zod.object({}).passthrough()` again for these row schemas, add a
    // dedicated `assertReplace` here anchored on the description
    // "Row with dynamic columns — cell values are any valid JSON."

    fs.writeFileSync(zodOutputPath, zod);
  }
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
