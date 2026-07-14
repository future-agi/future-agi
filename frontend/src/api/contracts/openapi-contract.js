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

export const shouldEnforceApiResponseContracts = () => {
  const value = envValue("VITE_API_CONTRACT_STRICT_RESPONSES");
  if (value === "true") return true;
  if (value === "false" || value === "off") return false;
  return false;
};

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

function pathSpecificity(template) {
  const segments = template.split("/").filter(Boolean);
  const paramCount = (template.match(PARAM_RE) || []).length;
  return {
    staticSegmentCount: segments.length - paramCount,
    paramCount,
    length: template.length,
  };
}

function comparePathSpecificity(a, b) {
  const left = pathSpecificity(a);
  const right = pathSpecificity(b);

  if (left.staticSegmentCount !== right.staticSegmentCount) {
    return right.staticSegmentCount - left.staticSegmentCount;
  }
  if (left.paramCount !== right.paramCount) {
    return left.paramCount - right.paramCount;
  }
  return right.length - left.length;
}

const endpointMatchersByGroup = Object.keys(OPENAPI_CONTRACT.endpoints)
  .sort(comparePathSpecificity)
  .reduce((groups, template) => {
    const groupName = template.split("/").filter(Boolean)[0] || "root";
    groups[groupName] ||= [];
    groups[groupName].push({
      template,
      regex: pathTemplateToRegex(template),
    });
    return groups;
  }, {});

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
    const params = {};
    new URL(String(url), "http://futureagi.local").searchParams.forEach(
      (value, key) => {
        if (Object.prototype.hasOwnProperty.call(params, key)) {
          params[key] = Array.isArray(params[key])
            ? [...params[key], value]
            : [params[key], value];
        } else {
          params[key] = value;
        }
      },
    );
    return params;
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
  const groupName = pathname.split("/").filter(Boolean)[0] || "root";
  const endpointMatchers = endpointMatchersByGroup[groupName] || [];
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

function schemaCacheKey(name, options = {}) {
  return `${name}:coercePrimitives=${Boolean(options.coercePrimitives)}`;
}

function enumSchema(values) {
  const literals = values.map((value) => z.literal(value));
  if (literals.length === 0) return z.never();
  if (literals.length === 1) return literals[0];
  return z.union(literals);
}

// Real recursive JSON value — scalars, arrays, and objects of JSON values.
// Used for x-json-value so those fields validate as "any valid JSON" rather
// than z.any(): a malformed cell (undefined, function, class instance leaking
// into the payload) fails instead of silently passing.
const JSON_VALUE_SCHEMA = z.lazy(() =>
  z.union([
    z.string(),
    z.number(),
    z.boolean(),
    z.null(),
    z.array(JSON_VALUE_SCHEMA),
    z.record(JSON_VALUE_SCHEMA),
  ]),
);

function schemaToZod(schema, options = {}) {
  if (!schema || typeof schema !== "object") return z.any();

  if (schema["x-string-or-object"]) {
    const { "x-string-or-object": _extension, ...objectSchema } = schema;
    return nullableIfNeeded(
      z.union([z.string(), objectSchemaToZod(objectSchema, options)]),
      schema,
    );
  }

  if (schema["x-string-or-array"]) {
    return nullableIfNeeded(
      z.union([z.string(), z.array(z.unknown())]),
      schema,
    );
  }

  if (schema["x-json-value"]) {
    return nullableIfNeeded(JSON_VALUE_SCHEMA, schema);
  }

  if (schema.$ref) {
    const resolved = resolveRef(schema.$ref);
    if (!resolved?.schema) return z.any();
    const cacheKey = schemaCacheKey(resolved.name, options);
    if (!definitionSchemaCache.has(cacheKey)) {
      definitionSchemaCache.set(
        cacheKey,
        z.lazy(() => schemaToZod(resolved.schema, options)),
      );
    }
    return nullableIfNeeded(definitionSchemaCache.get(cacheKey), schema);
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
    if (additionalProperties === false) {
      return z.object({}).strict();
    }
    return z.record(z.any());
  }

  const shape = Object.fromEntries(
    keys.map((key) => {
      const property = properties[key];
      // drf-yasg marks file uploads read-only on the property (single
      // FileField) or on the array items (ListField of FileField); neither is
      // client-sent, so accept anything and let multipart File bodies pass.
      const isReadOnlyRequestField =
        options.requestBody &&
        (property?.readOnly ||
          (property?.type === "array" && property?.items?.readOnly));
      if (isReadOnlyRequestField) {
        return [key, z.any().optional()];
      }
      let field = schemaToZod(property, options);
      if (!required.has(key)) field = field.optional();
      return [key, field];
    }),
  );

  let compiled = z.object(shape);
  if (additionalProperties && typeof additionalProperties === "object") {
    compiled = compiled.catchall(schemaToZod(additionalProperties, options));
  } else if (additionalProperties === false) {
    compiled = compiled.strict();
  } else {
    compiled = compiled.passthrough();
  }
  return compiled;
}

function nullableIfNeeded(compiled, schema) {
  return schema["x-nullable"] ? compiled.nullable() : compiled;
}

function parseMaybeJsonBody(data, headers = {}) {
  if (typeof FormData !== "undefined" && data instanceof FormData) {
    const body = {};
    data.forEach((value, key) => {
      if (Object.prototype.hasOwnProperty.call(body, key)) {
        body[key] = Array.isArray(body[key])
          ? [...body[key], value]
          : [body[key], value];
      } else {
        body[key] = value;
      }
    });
    return body;
  }

  if (
    typeof URLSearchParams !== "undefined" &&
    data instanceof URLSearchParams
  ) {
    return Object.fromEntries(data.entries());
  }

  if (typeof data !== "string") {
    // Validate the wire shape, not the in-memory object: axios serializes
    // plain-object and array bodies with JSON.stringify, which drops
    // undefined-valued keys (e.g. run_prompt_config.id: undefined).
    // Validating the raw object would reject payloads whose serialized form
    // is perfectly valid. Scoped to exactly the shapes axios JSON-serializes
    // — Blob/File/ArrayBuffer bodies are sent raw and stay untouched here.
    if (
      Array.isArray(data) ||
      (data &&
        typeof data === "object" &&
        (Object.getPrototypeOf(data) === Object.prototype ||
          Object.getPrototypeOf(data) === null))
    ) {
      try {
        return JSON.parse(JSON.stringify(data));
      } catch {
        // Circular refs / BigInt — fall back to the raw object, matching
        // the pre-normalization behavior.
        return data;
      }
    }
    return data;
  }
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

function isFormLikeBody(data) {
  return (
    (typeof FormData !== "undefined" && data instanceof FormData) ||
    (typeof URLSearchParams !== "undefined" && data instanceof URLSearchParams)
  );
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

function normalizeQueryForContract(rawQuery, queryParameters) {
  return Object.fromEntries(
    Object.entries(rawQuery).map(([name, value]) => {
      const schema = queryParameters?.[name]?.schema;
      if (schema?.type === "array" && !Array.isArray(value)) {
        return [name, [value]];
      }
      return [name, value];
    }),
  );
}

function queryParamsForWire(params = {}) {
  return Object.fromEntries(
    Object.entries(params).flatMap(([name, value]) => {
      if (value === null || value === undefined) return [];
      if (Array.isArray(value)) {
        const values = value.filter(
          (item) => item !== null && item !== undefined,
        );
        return values.length ? [[name, values]] : [];
      }
      return [[name, value]];
    }),
  );
}

export function validateContractedRequestConfig(config) {
  const endpoint = findOpenApiEndpoint(config?.url, config?.method);
  if (!endpoint) return { ok: true, skipped: true };

  if (!endpoint.contract.runtimeRequestValidation) {
    return { ok: true, skipped: true, endpoint };
  }

  const { requestBody, queryParameters } = endpoint.contract;
  if (requestBody) {
    const schema = schemaToZod(requestBody, {
      coercePrimitives: isFormLikeBody(config.data),
      requestBody: true,
    });
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

  const rawQuery = {
    ...parseUrlSearchParams(config?.url),
    ...queryParamsForWire(config?.params || {}),
  };
  if (queryParameters && Object.keys(queryParameters).length) {
    const shape = Object.fromEntries(
      Object.entries(queryParameters).map(([name, parameter]) => {
        let schema = schemaToZod(parameter.schema, { coercePrimitives: true });
        if (!parameter.required) schema = schema.optional();
        return [name, schema];
      }),
    );
    const parsed = z
      .object(shape)
      .strict()
      .safeParse(normalizeQueryForContract(rawQuery, queryParameters));
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
  const numericStatus = Number(status);
  const exact = responses[String(status)];
  if (exact) return exact;

  const statusClass = Number.isFinite(numericStatus)
    ? responses[String(Math.floor(numericStatus / 100) * 100)]
    : null;
  if (statusClass) return statusClass;

  if (
    Number.isFinite(numericStatus) &&
    (numericStatus < 200 || numericStatus >= 300)
  ) {
    return responses.default || null;
  }

  return (
    Object.entries(responses).find(([code]) => code.startsWith("2"))?.[1] ||
    responses.default ||
    null
  );
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
  const parsed = zodSchema.safeParse(response.data);
  if (parsed.success) return { ok: true, endpoint };

  return validationFailure({
    kind: "response",
    endpoint,
    schemaName: refSchemaName(schema),
    parsed,
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
