import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import YAML from "yaml";

import {
  definitionFileSchema,
  type ToolDefinition,
} from "../definitions/schema.js";

type JsonObject = Record<string, unknown>;

interface SelectedTool extends ToolDefinition {
  group: string;
  method: string;
  path: string;
  parameterLocations: Record<string, string>;
  inputJsonSchema: JsonObject;
  zodExpression: string;
}

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const serviceRoot = path.resolve(scriptDirectory, "..");
const repoRoot = path.resolve(serviceRoot, "../..");
const definitionsDirectory = path.join(serviceRoot, "definitions");
const swaggerPath = path.join(repoRoot, "api_contracts/openapi/swagger.json");
const generatedDirectory = path.join(serviceRoot, "src/generated");
const sharedCatalogDirectory = path.join(repoRoot, "api_contracts/mcp");

const HTTP_METHODS = new Set(["get", "post", "put", "patch", "delete"]);
const AUTH_HEADERS = new Set(["authorization", "x-api-key", "x-secret-key"]);

function isObject(value: unknown): value is JsonObject {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function readJson(filePath: string): JsonObject {
  const value: unknown = JSON.parse(fs.readFileSync(filePath, "utf8"));
  if (!isObject(value)) throw new Error(`${filePath} must contain a JSON object`);
  return value;
}

function loadDefinitions(): Array<ToolDefinition & { group: string }> {
  const files = fs
    .readdirSync(definitionsDirectory)
    .filter((name) => name.endsWith(".yaml"))
    .sort();
  const tools: Array<ToolDefinition & { group: string }> = [];
  const names = new Set<string>();
  const operationIds = new Set<string>();

  for (const file of files) {
    const filePath = path.join(definitionsDirectory, file);
    const definition = definitionFileSchema.parse(
      YAML.parse(fs.readFileSync(filePath, "utf8")),
    );
    for (const tool of definition.tools) {
      if (names.has(tool.name)) throw new Error(`Duplicate MCP tool name: ${tool.name}`);
      if (operationIds.has(tool.operationId)) {
        throw new Error(`OpenAPI operation is selected more than once: ${tool.operationId}`);
      }
      names.add(tool.name);
      operationIds.add(tool.operationId);
      tools.push({ ...tool, group: definition.group });
    }
  }
  return tools;
}

function indexOperations(swagger: JsonObject) {
  const index = new Map<
    string,
    Array<{
      path: string;
      method: string;
      operation: JsonObject;
      pathItem: JsonObject;
    }>
  >();
  if (!isObject(swagger.paths)) throw new Error("OpenAPI contract has no paths object");

  for (const [pathName, pathValue] of Object.entries(swagger.paths)) {
    if (!isObject(pathValue)) continue;
    for (const [method, operationValue] of Object.entries(pathValue)) {
      if (!HTTP_METHODS.has(method) || !isObject(operationValue)) continue;
      const operationId = operationValue.operationId;
      if (typeof operationId !== "string" || !operationId) continue;
      const matches = index.get(operationId) ?? [];
      matches.push({
        path: pathName,
        method: method.toUpperCase(),
        operation: operationValue,
        pathItem: pathValue,
      });
      index.set(operationId, matches);
    }
  }
  return index;
}

function resolveSchema(
  input: unknown,
  definitions: JsonObject,
  references: string[] = [],
): JsonObject {
  if (!isObject(input)) return {};
  if (typeof input.$ref === "string") {
    const prefix = "#/definitions/";
    if (!input.$ref.startsWith(prefix)) {
      throw new Error(`Unsupported schema reference: ${input.$ref}`);
    }
    if (references.includes(input.$ref)) {
      throw new Error(`Recursive schemas are not supported: ${input.$ref}`);
    }
    const target = definitions[input.$ref.slice(prefix.length)];
    if (!isObject(target)) throw new Error(`Missing schema reference: ${input.$ref}`);
    return resolveSchema(target, definitions, [...references, input.$ref]);
  }
  const schema = input;
  const output: JsonObject = {};
  for (const [key, value] of Object.entries(schema)) {
    if (key === "properties" && isObject(value)) {
      output.properties = Object.fromEntries(
        Object.entries(value).map(([name, child]) => [
          name,
          resolveSchema(child, definitions, references),
        ]),
      );
    } else if (key === "items") {
      output.items = resolveSchema(value, definitions, references);
    } else if (["allOf", "oneOf", "anyOf"].includes(key) && Array.isArray(value)) {
      output[key] = value.map((child) =>
        resolveSchema(child, definitions, references),
      );
    } else {
      output[key] = value;
    }
  }
  return output;
}

function quoted(value: unknown): string {
  return JSON.stringify(value);
}

function schemaToZod(
  input: unknown,
  definitions: JsonObject,
  references: string[] = [],
): string {
  if (!isObject(input)) return "z.unknown()";
  if (typeof input.$ref === "string") {
    const prefix = "#/definitions/";
    if (!input.$ref.startsWith(prefix)) {
      throw new Error(`Unsupported schema reference: ${input.$ref}`);
    }
    if (references.includes(input.$ref)) {
      throw new Error(`Recursive schemas are not supported: ${input.$ref}`);
    }
    const target = definitions[input.$ref.slice(prefix.length)];
    if (!isObject(target)) throw new Error(`Missing schema reference: ${input.$ref}`);
    return schemaToZod(target, definitions, [...references, input.$ref]);
  }

  let expression: string;
  if (Array.isArray(input.enum) && input.enum.length > 0) {
    expression = `z.union([${input.enum
      .map((value) => `z.literal(${quoted(value)})`)
      .join(", ")}])`;
  } else if (Array.isArray(input.allOf) && input.allOf.length > 0) {
    const parts = input.allOf.map((item) =>
      schemaToZod(item, definitions, references),
    );
    expression = parts
      .slice(1)
      .reduce(
        (left, right) => `z.intersection(${left}, ${right})`,
        parts[0] ?? "z.unknown()",
      );
  } else if (Array.isArray(input.oneOf) || Array.isArray(input.anyOf)) {
    const variants = (input.oneOf ?? input.anyOf) as unknown[];
    expression = `z.union([${variants
      .map((item) => schemaToZod(item, definitions, references))
      .join(", ")}])`;
  } else if (input.type === "string") {
    expression = "z.string()";
    if (typeof input.minLength === "number") expression += `.min(${input.minLength})`;
    if (typeof input.maxLength === "number") expression += `.max(${input.maxLength})`;
    if (typeof input.pattern === "string") {
      expression += `.regex(new RegExp(${quoted(input.pattern)}))`;
    }
  } else if (input.type === "integer" || input.type === "number") {
    expression = input.type === "integer" ? "z.number().int()" : "z.number()";
    if (typeof input.minimum === "number") expression += `.min(${input.minimum})`;
    if (typeof input.maximum === "number") expression += `.max(${input.maximum})`;
  } else if (input.type === "boolean") {
    expression = "z.boolean()";
  } else if (input.type === "array") {
    expression = `z.array(${schemaToZod(input.items, definitions, references)})`;
    if (typeof input.minItems === "number") expression += `.min(${input.minItems})`;
    if (typeof input.maxItems === "number") expression += `.max(${input.maxItems})`;
  } else {
    const properties = isObject(input.properties) ? input.properties : {};
    const required = new Set(
      Array.isArray(input.required)
        ? input.required.filter((item): item is string => typeof item === "string")
        : [],
    );
    const fields = Object.entries(properties).map(([name, child]) => {
      let childExpression = schemaToZod(child, definitions, references);
      if (!required.has(name)) childExpression += ".optional()";
      return `${quoted(name)}: ${childExpression}`;
    });
    expression = `z.strictObject({${fields.join(", ")}})`;
  }

  if (input["x-nullable"] === true) expression += ".nullable()";
  if (typeof input.description === "string" && input.description) {
    expression += `.describe(${quoted(input.description)})`;
  }
  return expression;
}

function buildInputContract(
  operation: JsonObject,
  pathItem: JsonObject,
  definitions: JsonObject,
) {
  const pathParameters = Array.isArray(pathItem.parameters) ? pathItem.parameters : [];
  const operationParameters = Array.isArray(operation.parameters)
    ? operation.parameters
    : [];
  const properties: JsonObject = {};
  const required: string[] = [];
  const parameterLocations: Record<string, string> = {};
  const zodFields: string[] = [];

  for (const parameter of [...pathParameters, ...operationParameters]) {
    if (!isObject(parameter)) continue;
    if (typeof parameter.name !== "string" || typeof parameter.in !== "string") continue;
    if (parameter.in === "header" && AUTH_HEADERS.has(parameter.name.toLowerCase())) {
      continue;
    }
    if (!["path", "query", "body"].includes(parameter.in)) {
      throw new Error(
        `Selected operation has unsupported ${parameter.in} parameter: ${parameter.name}`,
      );
    }
    const exposedName = parameter.in === "body" ? "body" : parameter.name;
    if (Object.hasOwn(properties, exposedName)) {
      throw new Error(`Duplicate exposed parameter: ${exposedName}`);
    }
    const schema =
      parameter.in === "body"
        ? parameter.schema
        : Object.fromEntries(
            Object.entries(parameter).filter(
              ([key]) => !["name", "in", "required"].includes(key),
            ),
          );
    properties[exposedName] = resolveSchema(schema, definitions);
    parameterLocations[exposedName] = parameter.in;
    if (parameter.required === true) required.push(exposedName);
    let zod = schemaToZod(schema, definitions);
    if (parameter.required !== true) zod += ".optional()";
    zodFields.push(`${quoted(exposedName)}: ${zod}`);
  }

  return {
    parameterLocations,
    inputJsonSchema: {
      type: "object",
      properties,
      required,
      additionalProperties: false,
    },
    zodExpression: `z.strictObject({${zodFields.join(", ")}})`,
  };
}

export function generateArtifacts(): Map<string, string> {
  const swagger = readJson(swaggerPath);
  const definitions = isObject(swagger.definitions) ? swagger.definitions : {};
  const operationIndex = indexOperations(swagger);
  const tools: SelectedTool[] = loadDefinitions().map((definition) => {
    const matches = operationIndex.get(definition.operationId) ?? [];
    if (matches.length !== 1) {
      throw new Error(
        `Tool ${definition.name} requires exactly one OpenAPI operation ${definition.operationId}; found ${matches.length}`,
      );
    }
    const match = matches[0];
    if (!match) throw new Error(`Missing operation: ${definition.operationId}`);
    if (match.method === "DELETE" && definition.risk !== "destructive") {
      throw new Error(`DELETE tool ${definition.name} must be marked destructive`);
    }
    return {
      ...definition,
      group: definition.group,
      method: match.method,
      path: match.path,
      ...buildInputContract(match.operation, match.pathItem, definitions),
    };
  });

  const serialized = tools.map(({ zodExpression: _zod, ...tool }) => tool);
  const metadataSource = `// Generated by scripts/generate.ts. Do not edit.\nimport type { GeneratedTool } from "../types.js";\n\nexport const generatedTools: GeneratedTool[] = ${JSON.stringify(serialized, null, 2)};\n`;
  const schemaEntries = tools
    .map((tool) => `  ${quoted(tool.name)}: ${tool.zodExpression},`)
    .join("\n");
  const schemasSource = `// Generated by scripts/generate.ts. Do not edit.\nimport * as z from "zod/v4";\nimport type { ToolSchemaMap } from "../types.js";\n\nexport const generatedToolSchemas: ToolSchemaMap = {\n${schemaEntries}\n};\n`;
  const catalog = `${JSON.stringify(
    {
      version: 1,
      generatedFrom: "api_contracts/openapi/swagger.json",
      tools: serialized,
    },
    null,
    2,
  )}\n`;

  return new Map([
    [path.join(generatedDirectory, "tools.generated.ts"), metadataSource],
    [path.join(generatedDirectory, "schemas.generated.ts"), schemasSource],
    [path.join(sharedCatalogDirectory, "tools.generated.json"), catalog],
  ]);
}

function run(): void {
  const check = process.argv.includes("--check");
  const stale: string[] = [];
  for (const [filePath, content] of generateArtifacts()) {
    if (check) {
      if (!fs.existsSync(filePath) || fs.readFileSync(filePath, "utf8") !== content) {
        stale.push(path.relative(repoRoot, filePath));
      }
    } else {
      fs.mkdirSync(path.dirname(filePath), { recursive: true });
      fs.writeFileSync(filePath, content);
      console.log(`generated ${path.relative(repoRoot, filePath)}`);
    }
  }
  if (stale.length > 0) {
    throw new Error(
      `Generated direct MCP tools are stale:\n${stale.map((name) => `  - ${name}`).join("\n")}\nRun: yarn mcp:generate`,
    );
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  run();
}
