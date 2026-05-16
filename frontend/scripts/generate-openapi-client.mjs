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

const ANNOTATION_PREFIXES = [
  "/model-hub/annotation-tasks",
  "/model-hub/annotation-queues",
  "/model-hub/annotations-labels",
  "/model-hub/annotations",
  "/model-hub/scores",
  "/tracer/bulk-annotation",
  "/tracer/get-annotation-labels",
  "/tracer/trace-annotation",
  "/tracer/observation-span/add_annotations",
  "/tracer/observation-span/delete_annotation_label",
  "/tracer/project-version/add_annotations",
];

const ANNOTATION_EXACT_PATHS = [
  "/model-hub/dataset/{dataset_id}/annotation-summary/",
];

const FILTER_PREFIXES = [
  "/model-hub/ai-filter",
  "/api/traces",
  "/tracer/dashboard",
  "/tracer/observation-span",
  "/tracer/project",
  "/tracer/trace",
  "/tracer/trace-session",
  "/tracer/users",
];

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

const hasPrefix = (pathName, prefixes) =>
  prefixes.some(
    (prefix) => pathName === `${prefix}/` || pathName.startsWith(`${prefix}/`),
  );

const isProtectedPath = (pathName) =>
  hasPrefix(pathName, ANNOTATION_PREFIXES) ||
  hasPrefix(pathName, FILTER_PREFIXES) ||
  ANNOTATION_EXACT_PATHS.includes(pathName);

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

function filterSwagger(swagger) {
  const paths = {};
  const refs = new Set();

  Object.entries(swagger.paths || {}).forEach(([pathName, pathSpec]) => {
    if (!isProtectedPath(pathName)) return;
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
      title: `${swagger.info?.title || "Future AGI API"} - annotation/filter contracts`,
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

  normalizeGeneratedFileEndings();
}

const swagger = JSON.parse(fs.readFileSync(swaggerPath, "utf8"));
const filtered = filterSwagger(swagger);
const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "futureagi-openapi-"));
const tempSchemaPath = path.join(tempDir, "annotation-filter-openapi.json");
fs.writeFileSync(tempSchemaPath, JSON.stringify(filtered, null, 2));

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
