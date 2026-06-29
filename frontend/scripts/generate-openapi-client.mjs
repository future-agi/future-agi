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
  // x-string-or-object fields: orval can't read the custom extension so it
  // generates zod.object({}).passthrough() instead of the correct string|object
  // union. Replace all occurrences matched by the unique description string,
  // handling .optional(), .default(...), and bare variants.
  //
  // Native OpenAPI 3.0 oneOf would let us delete this whole block — tracked in
  // TH-6030. Until then, both this zod patch and the api.schemas.ts patch below
  // throw if their anchor no longer matches, so we never silently regress to
  // object-only.
  if (fs.existsSync(zodOutputPath)) {
    let zod = fs.readFileSync(zodOutputPath, "utf8");
    const zodPattern =
      /zod\.object\(\{\s*\}\)\.passthrough\(\)((?:\.optional\(\))?(?:\.default\([^)]+\))?(?:\.optional\(\))?)\.describe\('String or JSON object\.'\)/g;
    if (!zodPattern.test(zod)) {
      throw new Error(
        "x-string-or-object post-processor: zod anchor no longer matches. " +
          "Either restore the orval emit shape (`zod.object({...}).passthrough()" +
          "[.optional()|.default(...)].describe('String or JSON object.')`) or " +
          "migrate to native OpenAPI 3.0 oneOf (TH-6030) and delete this block.",
      );
    }
    zod = zod.replace(
      zodPattern,
      (match, middle) =>
        `zod.union([zod.string(), zod.object({\n\n}).passthrough()])${middle}.describe('String or JSON object.')`,
    );
    fs.writeFileSync(zodOutputPath, zod);
  }
  // Fix api.schemas.ts: x-string-or-object fields show as `{ [key: string]: unknown }`
  // but should be `string | { [key: string]: unknown }`. Orval emits a multi-line
  // JSDoc block immediately above each affected type alias and a separate
  // `interface` field with a leading `/** String or JSON object. */` JSDoc — both
  // need to flip from object-only to union.
  const schemasOutputPath = path.join(outputDir, "api.schemas.ts");
  if (fs.existsSync(schemasOutputPath)) {
    let schemas = fs.readFileSync(schemasOutputPath, "utf8");

    // (a) Type alias: orval emits
    //   /**
    //    * String or JSON object.
    //    */
    //   export type FooApiResponseFormat = { [key: string]: unknown };
    // Flip the right-hand side to the union. Capture the alias name so we
    // only rewrite the alias that carries the "String or JSON object."
    // docblock — not any unrelated `{ [key: string]: unknown }` declaration.
    const aliasPattern =
      /(\/\*\*\n \* String or JSON object\.\n \*\/\nexport type \w+ = )\{ \[key: string\]: unknown \};/g;
    if (!aliasPattern.test(schemas)) {
      throw new Error(
        "x-string-or-object post-processor: type-alias anchor no longer matches. " +
          "Either restore the orval emit shape (`/** String or JSON object. */` " +
          "above `export type X = { [key: string]: unknown };`) or migrate to " +
          "native OpenAPI 3.0 oneOf (TH-6030) and delete this block.",
      );
    }
    schemas = schemas.replace(
      aliasPattern,
      "$1string | { [key: string]: unknown };",
    );

    fs.writeFileSync(schemasOutputPath, schemas);
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
