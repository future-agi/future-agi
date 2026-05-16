import { z } from "zod";

import { OPENAPI_CONTRACT } from "./openapi-contract.generated";

const PARAM_RE = /\{([^}]+)\}/g;
const JSON_CONTENT_TYPE_RE = /application\/json|\+json/i;
const definitionSchemaCache = new Map();

class ApiContractValidationError extends Error {
  constructor(message, details = {}) {
    super(message);
    this.name = "ApiContractValidationError";
    this.details = details;
  }
}

const envValue = (key) => {
  try {
    return import.meta.env?.[key];
  } catch {
    return undefined;
  }
};

const nodeEnv = () => {
  try {
    return globalThis.process?.env?.NODE_ENV;
  } catch {
    return undefined;
  }
};

const appMode = () => envValue("MODE") || nodeEnv() || "development";

export const shouldEnforceApiRequestContracts = () =>
  envValue("VITE_API_CONTRACTS") !== "off" && appMode() !== "production";

export const shouldEnforceApiResponseContracts = () =>
  envValue("VITE_API_CONTRACT_STRICT_RESPONSES") === "true";

function pathTemplateToRegex(template) {
  let pattern = "^";
  let lastIndex = 0;
  for (const match of template.matchAll(PARAM_RE)) {
    pattern += escapeRegExp(template.slice(lastIndex, match.index));
    pattern += "[^/]+";
    lastIndex = match.index + match[0].length;
  }
  pattern += escapeRegExp(template.slice(lastIndex));
  pattern += "$";
  return new RegExp(pattern);
}

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

const endpointMatchers = Object.keys(OPENAPI_CONTRACT.endpoints)
  .sort((a, b) => b.length - a.length)
  .map((template) => ({
    template,
    regex: pathTemplateToRegex(template),
  }));

function normalizeRequestPath(url) {
  if (!url) return "";
  try {
    return new URL(String(url), "http://futureagi.local").pathname;
  } catch {
    const [path] = String(url).split("?");
    return path.startsWith("/") ? path : `/${path}`;
  }
}

function parseUrlSearchParams(url) {
  try {
    return Object.fromEntries(
      new URL(String(url), "http://futureagi.local").searchParams.entries(),
    );
  } catch {
    return {};
  }
}

function lowerMethod(method) {
  return String(method || "get").toLowerCase();
}

export function findOpenApiEndpoint(url, method = "get") {
  const pathname = normalizeRequestPath(url);
  const httpMethod = lowerMethod(method);
  const match = endpointMatchers.find((endpoint) =>
    endpoint.regex.test(pathname),
  );
  if (!match) return null;
  const methodContract =
    OPENAPI_CONTRACT.endpoints[match.template]?.[httpMethod];
  if (!methodContract) return null;
  return {
    template: match.template,
    method: httpMethod,
    contract: methodContract,
  };
}

function resolveRef(ref) {
  const match = String(ref || "").match(/^#\/definitions\/(.+)$/);
  if (!match) return null;
  return {
    name: match[1],
    schema: OPENAPI_CONTRACT.definitions[match[1]],
  };
}

function enumSchema(values) {
  const literals = values.map((value) => z.literal(value));
  if (literals.length === 0) return z.never();
  if (literals.length === 1) return literals[0];
  return z.union(literals);
}

function schemaToZod(schema, options = {}) {
  if (!schema || typeof schema !== "object") return z.any();

  if (schema.$ref) {
    const resolved = resolveRef(schema.$ref);
    if (!resolved?.schema) return z.any();
    if (!definitionSchemaCache.has(resolved.name)) {
      definitionSchemaCache.set(
        resolved.name,
        z.lazy(() => schemaToZod(resolved.schema, options)),
      );
    }
    return nullableIfNeeded(definitionSchemaCache.get(resolved.name), schema);
  }

  if (Array.isArray(schema.enum)) {
    return nullableIfNeeded(enumSchema(schema.enum), schema);
  }

  if (Array.isArray(schema.allOf) && schema.allOf.length) {
    const [first, ...rest] = schema.allOf.map((item) =>
      schemaToZod(item, options),
    );
    return nullableIfNeeded(
      rest.reduce((combined, item) => z.intersection(combined, item), first),
      schema,
    );
  }

  const type = schema.type || (schema.properties ? "object" : undefined);
  let compiled;

  switch (type) {
    case "array":
      compiled = z.array(schemaToZod(schema.items, options));
      if (Number.isInteger(schema.minItems))
        compiled = compiled.min(schema.minItems);
      if (Number.isInteger(schema.maxItems))
        compiled = compiled.max(schema.maxItems);
      break;
    case "object":
      compiled = objectSchemaToZod(schema, options);
      break;
    case "integer":
      compiled = options.coercePrimitives
        ? z.coerce.number().int()
        : z.number().int();
      break;
    case "number":
      compiled = options.coercePrimitives ? z.coerce.number() : z.number();
      break;
    case "boolean":
      compiled = options.coercePrimitives
        ? z.union([z.boolean(), z.literal("true"), z.literal("false")])
        : z.boolean();
      break;
    case "string":
      compiled = z.string();
      if (schema.format === "uuid") compiled = compiled.uuid();
      if (Number.isInteger(schema.minLength))
        compiled = compiled.min(schema.minLength);
      if (Number.isInteger(schema.maxLength))
        compiled = compiled.max(schema.maxLength);
      break;
    case "file":
      compiled = z.any();
      break;
    default:
      compiled = z.any();
      break;
  }

  return nullableIfNeeded(compiled, schema);
}

function objectSchemaToZod(schema, options) {
  const properties = schema.properties || {};
  const required = new Set(schema.required || []);
  const keys = Object.keys(properties);
  const additionalProperties = schema.additionalProperties;

  if (!keys.length) {
    if (additionalProperties && typeof additionalProperties === "object") {
      return z.record(schemaToZod(additionalProperties, options));
    }
    return z.record(z.any());
  }

  const shape = Object.fromEntries(
    keys.map((key) => {
      let field = schemaToZod(properties[key], options);
      if (!required.has(key)) field = field.optional();
      return [key, field];
    }),
  );

  let compiled = z.object(shape);
  if (additionalProperties && typeof additionalProperties === "object") {
    compiled = compiled.catchall(schemaToZod(additionalProperties, options));
  } else {
    compiled = compiled.passthrough();
  }
  return compiled;
}

function nullableIfNeeded(compiled, schema) {
  return schema["x-nullable"] ? compiled.nullable() : compiled;
}

function parseMaybeJsonBody(data, headers = {}) {
  if (typeof data !== "string") return data;
  const contentType =
    headers["Content-Type"] ||
    headers["content-type"] ||
    headers?.common?.["Content-Type"] ||
    headers?.common?.["content-type"] ||
    "";
  if (
    !JSON_CONTENT_TYPE_RE.test(contentType) &&
    !data.trim().startsWith("{") &&
    !data.trim().startsWith("[")
  ) {
    return data;
  }
  try {
    return JSON.parse(data);
  } catch {
    return data;
  }
}

function issueSummary(issues = []) {
  return issues
    .slice(0, 5)
    .map((issue) => `${issue.path.join(".") || "<root>"}: ${issue.message}`)
    .join("; ");
}

function validationFailure({ kind, endpoint, schemaName, parsed }) {
  const schemaLabel = schemaName ? ` (${schemaName})` : "";
  return {
    ok: false,
    error: new ApiContractValidationError(
      `${kind} contract validation failed for ${endpoint.method.toUpperCase()} ${endpoint.template}${schemaLabel}: ${issueSummary(parsed.error.issues)}`,
      {
        kind,
        endpoint: endpoint.template,
        method: endpoint.method,
        schemaName,
        issues: parsed.error.issues,
      },
    ),
  };
}

function refSchemaName(schema) {
  return resolveRef(schema?.$ref)?.name || null;
}

export function validateContractedRequestConfig(config) {
  const endpoint = findOpenApiEndpoint(config?.url, config?.method);
  if (!endpoint) return { ok: true, skipped: true };

  const { requestBody, queryParameters } = endpoint.contract;
  if (requestBody) {
    const schema = schemaToZod(requestBody);
    const parsed = schema.safeParse(
      parseMaybeJsonBody(config.data, config.headers),
    );
    if (!parsed.success) {
      return validationFailure({
        kind: "request body",
        endpoint,
        schemaName: refSchemaName(requestBody),
        parsed,
      });
    }
  }

  if (queryParameters && Object.keys(queryParameters).length) {
    const rawQuery = {
      ...parseUrlSearchParams(config?.url),
      ...(config?.params || {}),
    };
    const shape = Object.fromEntries(
      Object.entries(queryParameters).map(([name, parameter]) => {
        let schema = schemaToZod(parameter.schema, { coercePrimitives: true });
        if (!parameter.required) schema = schema.optional();
        return [name, schema];
      }),
    );
    const parsed = z.object(shape).passthrough().safeParse(rawQuery);
    if (!parsed.success) {
      return validationFailure({
        kind: "query",
        endpoint,
        schemaName: null,
        parsed,
      });
    }
  }

  return { ok: true, endpoint };
}

function responseSchemaFor(endpoint, status) {
  const responses = endpoint.contract.responses || {};
  return (
    responses[String(status)] ||
    responses[String(Math.floor(Number(status) / 100) * 100)] ||
    Object.entries(responses).find(([code]) => code.startsWith("2"))?.[1] ||
    responses.default ||
    null
  );
}

function responseCandidates(data) {
  const candidates = [data];
  if (data && typeof data === "object") {
    if (Object.prototype.hasOwnProperty.call(data, "result"))
      candidates.push(data.result);
    if (Object.prototype.hasOwnProperty.call(data, "results"))
      candidates.push(data.results);
    if (Object.prototype.hasOwnProperty.call(data, "data"))
      candidates.push(data.data);
  }
  return candidates;
}

export function validateContractedResponse(response) {
  const endpoint = findOpenApiEndpoint(
    response?.config?.url,
    response?.config?.method,
  );
  if (!endpoint) return { ok: true, skipped: true };

  const schema = responseSchemaFor(endpoint, response?.status);
  if (!schema) return { ok: true, skipped: true, endpoint };

  const zodSchema = schemaToZod(schema);
  let firstFailure = null;
  for (const candidate of responseCandidates(response.data)) {
    const parsed = zodSchema.safeParse(candidate);
    if (parsed.success) return { ok: true, endpoint };
    if (!firstFailure) firstFailure = parsed;
  }

  return validationFailure({
    kind: "response",
    endpoint,
    schemaName: refSchemaName(schema),
    parsed: firstFailure,
  });
}

export function handleApiContractValidation(result, { strict = false } = {}) {
  if (result.ok) return;
  if (strict) throw result.error;
  // eslint-disable-next-line no-console
  console.warn(result.error.message, result.error.details);
}

export function assertContractedRequestConfig(config) {
  const result = validateContractedRequestConfig(config);
  handleApiContractValidation(result, {
    strict: shouldEnforceApiRequestContracts(),
  });
  return config;
}

export function assertContractedResponse(response) {
  const result = validateContractedResponse(response);
  handleApiContractValidation(result, {
    strict: shouldEnforceApiResponseContracts(),
  });
  return response;
}
