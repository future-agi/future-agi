/* eslint-env node */
/* eslint-disable no-console */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(__dirname, "..");
const repoRoot = path.resolve(frontendRoot, "..");
const swaggerPath = path.join(
  repoRoot,
  "api_contracts",
  "openapi",
  "swagger.json",
);

const MIN_ENDPOINTS = 960;
const MAX_MUTATIONS_WITHOUT_BODY_SCHEMA = 0;
const MAX_OPERATIONS_WITHOUT_RESPONSE_SCHEMA = 0;
const MAX_BROAD_SUCCESS_RESPONSE_SCHEMAS = 0;
const MAX_OPERATIONS_WITHOUT_ERROR_RESPONSE_SCHEMA = 0;
const MAX_BROAD_ERROR_RESPONSE_SCHEMAS = 0;
const MIN_GROUP_PATHS = {
  accounts: 75,
  agentcc: 100,
  "falcon-ai": 15,
  "model-hub": 360,
  simulate: 100,
  // OTLP trace ingestion routes migrated to fi-collector; Django no longer serves them.
  tracer: 154,
  usage: 55,
};
const MUTATION_METHODS = new Set(["post", "put", "patch"]);
const NON_RESPONSE_OPTIONAL_METHODS = new Set(["delete"]);
const NO_BODY_RESPONSE_STATUS = /^(204|205|304|3\d\d)$/;
const SUCCESS_RESPONSE_STATUS = /^2\d\d$/;
const ERROR_RESPONSE_STATUS = /^[45]\d\d$/;
const DEFAULT_ERROR_RESPONSE_STATUS = "default";
const EXACT_EMPTY_SUCCESS_RESPONSE_SCHEMAS = new Set([
  // OTLP export success responses are intentionally an empty JSON object.
  // This is an exact protocol acknowledgement, not an untyped application DTO.
  "OTLPHTTPTraceResponse",
]);
const UNSUPPORTED_SWAGGER_2_SCHEMA_KEYS = new Set([
  "anyOf",
  "nullable",
  "oneOf",
]);

const swagger = JSON.parse(fs.readFileSync(swaggerPath, "utf8"));
const paths = swagger.paths || {};
const definitions = swagger.definitions || {};
const pathNames = Object.keys(paths);

const pathGroups = {};
pathNames.forEach((pathName) => {
  const groupName = pathName.split("/").filter(Boolean)[0] || "root";
  pathGroups[groupName] ||= [];
  pathGroups[groupName].push(pathName);
});

const operations = [];
Object.entries(paths).forEach(([pathName, pathSpec]) => {
  Object.entries(pathSpec || {}).forEach(([method, operation]) => {
    if (method === "parameters") return;
    operations.push({
      method,
      pathName,
      operation,
    });
  });
});

const mutationWithoutBodySchema = operations.filter(({ method, operation }) => {
  if (!MUTATION_METHODS.has(method)) return false;
  const parameters = operation.parameters || [];
  const hasBodySchema = parameters.some(
    (parameter) => parameter.in === "body" && parameter.schema,
  );
  const hasFormDataContract = parameters.some(
    (parameter) => parameter.in === "formData" && parameter.type,
  );
  return !hasBodySchema && !hasFormDataContract;
});

const operationWithoutResponseSchema = operations.filter(
  ({ method, operation }) => {
    if (NON_RESPONSE_OPTIONAL_METHODS.has(method)) return false;
    const responses = operation.responses || {};
    const hasResponseSchema = Object.values(responses).some(
      (response) => response?.schema,
    );
    if (hasResponseSchema) return false;

    const statusCodes = Object.keys(responses);
    const isDocumentedNoBodyOperation =
      statusCodes.length > 0 &&
      statusCodes.every((statusCode) => NO_BODY_RESPONSE_STATUS.test(statusCode));

    return !isDocumentedNoBodyOperation;
  },
);

function responseSchemaEntries(operation) {
  return Object.entries(operation.responses || {})
    .filter(
      ([statusCode, response]) =>
        SUCCESS_RESPONSE_STATUS.test(statusCode) &&
        !NO_BODY_RESPONSE_STATUS.test(statusCode) &&
        response?.schema,
    )
    .map(([statusCode, response]) => ({
      statusCode,
      schema: response.schema,
    }));
}

function errorResponseSchemaEntries(operation) {
  return Object.entries(operation.responses || {})
    .filter(
      ([statusCode, response]) =>
        (ERROR_RESPONSE_STATUS.test(statusCode) ||
          statusCode === DEFAULT_ERROR_RESPONSE_STATUS) &&
        response?.schema,
    )
    .map(([statusCode, response]) => ({
      statusCode,
      schema: response.schema,
    }));
}

function refName(schema) {
  return schema?.$ref?.replace("#/definitions/", "") || null;
}

function dereference(schema) {
  const name = refName(schema);
  if (!name) return schema;
  return definitions[name] || schema;
}

function isUnshapedObject(schema) {
  if (!schema) return false;
  const resolved = dereference(schema);
  if (resolved.type !== "object") return false;
  if (resolved.additionalProperties) {
    return resolved.additionalProperties === true
      ? true
      : isUnshapedObject(resolved.additionalProperties);
  }
  return !resolved.properties || Object.keys(resolved.properties).length === 0;
}

function broadSuccessResponseReason(schema) {
  const schemaName = refName(schema);
  if (schemaName && EXACT_EMPTY_SUCCESS_RESPONSE_SCHEMAS.has(schemaName)) {
    return null;
  }

  const resolved = dereference(schema);
  if (isUnshapedObject(resolved)) {
    return schemaName
      ? `${schemaName} is an unshaped object response`
      : "inline success response is an unshaped object";
  }

  const result = resolved.properties?.result;
  if (isUnshapedObject(result)) {
    return schemaName
      ? `${schemaName}.result is an unshaped object`
      : "inline success response result is an unshaped object";
  }

  const data = resolved.properties?.data;
  if (isUnshapedObject(data)) {
    return schemaName
      ? `${schemaName}.data is an unshaped object`
      : "inline success response data is an unshaped object";
  }

  return null;
}

const broadSuccessResponseSchemas = operations.flatMap(
  ({ method, pathName, operation }) =>
    responseSchemaEntries(operation).flatMap(({ statusCode, schema }) => {
      const reason = broadSuccessResponseReason(schema);
      if (!reason) return [];
      return [
        {
          method,
          pathName,
          statusCode,
          schema: refName(schema) || "inline",
          reason,
        },
      ];
    }),
);

const operationsWithoutErrorResponseSchema = operations.filter(({ operation }) => {
  const errorResponses = errorResponseSchemaEntries(operation);
  return errorResponses.length === 0;
});

function broadErrorResponseReason(schema) {
  const schemaName = refName(schema);
  const resolved = dereference(schema);

  if (isUnshapedObject(resolved)) {
    return schemaName
      ? `${schemaName} is an unshaped object response`
      : "inline error response is an unshaped object";
  }

  for (const key of ["error", "message", "detail", "result", "data"]) {
    if (isUnshapedObject(resolved.properties?.[key])) {
      return schemaName
        ? `${schemaName}.${key} is an unshaped object`
        : `inline error response ${key} is an unshaped object`;
    }
  }

  return null;
}

const broadErrorResponseSchemas = operations.flatMap(
  ({ method, pathName, operation }) =>
    errorResponseSchemaEntries(operation).flatMap(({ statusCode, schema }) => {
      const reason = broadErrorResponseReason(schema);
      if (!reason) return [];
      return [
        {
          method,
          pathName,
          statusCode,
          schema: refName(schema) || "inline",
          reason,
        },
      ];
    }),
);

const unsupportedSchemaKeys = [];
function walkSchema(value, pathParts = []) {
  if (!value || typeof value !== "object") return;
  if (Array.isArray(value)) {
    value.forEach((item, index) => walkSchema(item, [...pathParts, index]));
    return;
  }

  Object.entries(value).forEach(([key, child]) => {
    const nextPath = [...pathParts, key];
    if (UNSUPPORTED_SWAGGER_2_SCHEMA_KEYS.has(key)) {
      unsupportedSchemaKeys.push(nextPath.join("/"));
    }
    walkSchema(child, nextPath);
  });
}
walkSchema(swagger);

const malformedPaths = pathNames.filter((pathName) =>
  pathName.split("/").some((segment) => segment === "." || segment === ".."),
);

const failures = [];

if (pathNames.length < MIN_ENDPOINTS) {
  failures.push(
    `Expected at least ${MIN_ENDPOINTS} Swagger paths, found ${pathNames.length}.`,
  );
}

Object.entries(MIN_GROUP_PATHS).forEach(([groupName, minPaths]) => {
  const count = pathGroups[groupName]?.length || 0;
  if (count < minPaths) {
    failures.push(
      `Expected at least ${minPaths} /${groupName} Swagger paths, found ${count}.`,
    );
  }
});

if (mutationWithoutBodySchema.length > MAX_MUTATIONS_WITHOUT_BODY_SCHEMA) {
  failures.push(
    [
      `Mutation endpoints without request body schemas increased from ${MAX_MUTATIONS_WITHOUT_BODY_SCHEMA} to ${mutationWithoutBodySchema.length}.`,
      ...mutationWithoutBodySchema
        .slice(0, 40)
        .map(
          ({ method, pathName }) => `  - ${method.toUpperCase()} ${pathName}`,
        ),
    ].join("\n"),
  );
}

if (
  operationWithoutResponseSchema.length > MAX_OPERATIONS_WITHOUT_RESPONSE_SCHEMA
) {
  failures.push(
    [
      `Operations without response schemas increased from ${MAX_OPERATIONS_WITHOUT_RESPONSE_SCHEMA} to ${operationWithoutResponseSchema.length}.`,
      ...operationWithoutResponseSchema
        .slice(0, 40)
        .map(
          ({ method, pathName }) => `  - ${method.toUpperCase()} ${pathName}`,
        ),
    ].join("\n"),
  );
}

if (broadSuccessResponseSchemas.length > MAX_BROAD_SUCCESS_RESPONSE_SCHEMAS) {
  failures.push(
    [
      `Broad success response schemas increased from ${MAX_BROAD_SUCCESS_RESPONSE_SCHEMAS} to ${broadSuccessResponseSchemas.length}.`,
      ...broadSuccessResponseSchemas
        .slice(0, 40)
        .map(
          ({ method, pathName, statusCode, schema, reason }) =>
            `  - ${method.toUpperCase()} ${pathName} -> ${statusCode} ${schema}: ${reason}`,
        ),
    ].join("\n"),
  );
}

if (
  operationsWithoutErrorResponseSchema.length >
  MAX_OPERATIONS_WITHOUT_ERROR_RESPONSE_SCHEMA
) {
  failures.push(
    [
      `Operations without error response schemas increased from ${MAX_OPERATIONS_WITHOUT_ERROR_RESPONSE_SCHEMA} to ${operationsWithoutErrorResponseSchema.length}.`,
      ...operationsWithoutErrorResponseSchema
        .slice(0, 40)
        .map(
          ({ method, pathName }) => `  - ${method.toUpperCase()} ${pathName}`,
        ),
    ].join("\n"),
  );
}

if (broadErrorResponseSchemas.length > MAX_BROAD_ERROR_RESPONSE_SCHEMAS) {
  failures.push(
    [
      `Broad error response schemas increased from ${MAX_BROAD_ERROR_RESPONSE_SCHEMAS} to ${broadErrorResponseSchemas.length}.`,
      ...broadErrorResponseSchemas
        .slice(0, 40)
        .map(
          ({ method, pathName, statusCode, schema, reason }) =>
            `  - ${method.toUpperCase()} ${pathName} -> ${statusCode} ${schema}: ${reason}`,
        ),
    ].join("\n"),
  );
}

if (unsupportedSchemaKeys.length) {
  failures.push(
    [
      "Swagger 2 schema contains unsupported JSON Schema keywords.",
      ...unsupportedSchemaKeys.slice(0, 40).map((item) => `  - ${item}`),
    ].join("\n"),
  );
}

if (malformedPaths.length) {
  failures.push(
    [
      "Swagger paths contain malformed dot segments.",
      ...malformedPaths.slice(0, 40).map((pathName) => `  - ${pathName}`),
    ].join("\n"),
  );
}

if (failures.length) {
  console.error(
    [
      "Management API contract coverage check failed.",
      ...failures,
      "",
      "Add explicit DRF request/response serializers or lower the baseline in the same PR with a written reason.",
    ].join("\n\n"),
  );
  process.exit(1);
}

console.log(
  [
    "Management API contract coverage:",
    `  paths: ${pathNames.length}`,
    `  operations: ${operations.length}`,
    `  mutation endpoints without request body schemas: ${mutationWithoutBodySchema.length}/${MAX_MUTATIONS_WITHOUT_BODY_SCHEMA}`,
    `  operations without response schemas: ${operationWithoutResponseSchema.length}/${MAX_OPERATIONS_WITHOUT_RESPONSE_SCHEMA}`,
    `  broad success response schemas: ${broadSuccessResponseSchemas.length}/${MAX_BROAD_SUCCESS_RESPONSE_SCHEMAS}`,
    `  operations without error response schemas: ${operationsWithoutErrorResponseSchema.length}/${MAX_OPERATIONS_WITHOUT_ERROR_RESPONSE_SCHEMA}`,
    `  broad error response schemas: ${broadErrorResponseSchemas.length}/${MAX_BROAD_ERROR_RESPONSE_SCHEMAS}`,
  ].join("\n"),
);
