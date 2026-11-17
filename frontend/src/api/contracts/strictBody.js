// Dev's strict request bodies (the capability contract). The routes below are validated
// on the server with validated_request(..., reject_unknown_fields=True):
//   - an unknown TOP-LEVEL key answers 400 "Unknown field." before the view
//     runs (tfc/utils/api_contracts.py _unknown_fields);
//   - an unknown key inside a nested serializer (a prompt_config entry, an
//     eval metric) is dropped by DRF without a word.
// The production build does not validate request bodies in the browser
// (openapi-contract.js skips them when MODE is production), so a field dev
// refuses shows up only as Django's 400, or not at all when it is nested.
//
// Which routes are strict is read from the backend, never listed here:
// strictRoutes.generated.js holds every [method, path] whose handler on this
// branch is validated_request(..., reject_unknown_fields=True), and
// futureagi/model_hub/tests/test_dsx_contract_parity.py fails when it differs
// from what the URL resolver serves (it also writes it). Which fields a route
// accepts is read from the generated contract (openapi-contract.generated.js,
// built from api_contracts/openapi/swagger.json, which drf-yasg builds from
// the same serializers); the parity test proves the two agree for every
// strict route the UI calls. __tests__/dsxRequestSweep.test.js checks every
// call site of every strict route against it.
import { OPENAPI_CONTRACT } from "./openapi-contract.generated";
import { findOpenApiEndpoint } from "./openapi-contract";
import { STRICT_ROUTES } from "./strictRoutes.generated";

export const DSX_STRICT_ROUTES = Object.freeze(
  STRICT_ROUTES.map(([method, path]) => Object.freeze({ method, path })),
);

const REF_RE = /^#\/definitions\/(.+)$/;

function definitionOfRef(ref) {
  const m = REF_RE.exec(String(ref ?? ""));
  return m ? m[1] : null;
}

/** The contract schema of a definition name (null when absent). */
export function definitionSchema(name) {
  return (name && OPENAPI_CONTRACT.definitions?.[name]) || null;
}

/** The request-body definition name of a contracted route, or null. */
export function bodyDefinitionOf(method, path) {
  const ep = OPENAPI_CONTRACT.endpoints?.[path]?.[String(method).toLowerCase()];
  return definitionOfRef(ep?.requestBody?.$ref);
}

/** The top-level field names dev's serializer accepts for a definition. */
export function acceptedFields(definition) {
  return Object.keys(definitionSchema(definition)?.properties ?? {});
}

/**
 * The strict route a request goes to, matched like the axios contract
 * interceptor matches it, or null when the route is not one of
 * DSX_STRICT_ROUTES.
 * @returns {{method: string, path: string, definition: string|null}|null}
 */
export function strictRouteOf(method, url) {
  const ep = findOpenApiEndpoint(url, method);
  if (!ep) return null;
  const hit = DSX_STRICT_ROUTES.find(
    (r) => r.method === ep.method && r.path === ep.template,
  );
  if (!hit) return null;
  return {
    method: hit.method,
    path: hit.path,
    definition: definitionOfRef(ep.contract?.requestBody?.$ref),
  };
}

/** The keys of a JSON or FormData body that travel on the wire. */
export function wireKeysOf(body) {
  if (typeof FormData !== "undefined" && body instanceof FormData) {
    return [...new Set([...body.keys()])];
  }
  if (!body || typeof body !== "object" || Array.isArray(body)) return [];
  // axios JSON-serializes the body: undefined-valued keys never travel.
  return Object.keys(body).filter((k) => body[k] !== undefined);
}

/** Top-level keys of `body` the definition does not accept (sorted). */
export function unknownFields(body, definition) {
  const accepted = new Set(acceptedFields(definition));
  return wireKeysOf(body)
    .filter((k) => !accepted.has(k))
    .sort();
}

function resolve(schema) {
  const name = definitionOfRef(schema?.$ref);
  return name ? definitionSchema(name) : schema;
}

// A nested object DRF validates with its own serializer: it has declared
// properties and no catch-all. Its unknown keys are dropped.
const dropsUnknown = (schema) =>
  Boolean(schema?.properties) &&
  Object.keys(schema.properties).length > 0 &&
  schema.additionalProperties !== true &&
  typeof schema.additionalProperties !== "object";

/**
 * Keys nested inside `body` that dev's serializers drop without a word
 * (an unknown key of a prompt_config entry, of an eval metric...).
 * @returns {{path: (string|number)[], key: string, value: unknown}[]}
 */
export function droppedNestedFields(body, definition) {
  const out = [];
  const walk = (value, schema, path) => {
    const s = resolve(schema);
    if (!s || value == null) return;
    if (Array.isArray(value)) {
      if (s.type === "array" && s.items)
        value.forEach((v, i) => walk(v, s.items, [...path, i]));
      return;
    }
    if (typeof value !== "object") return;
    const props = s.properties ?? {};
    Object.keys(value).forEach((k) => {
      if (value[k] === undefined) return;
      if (props[k]) walk(value[k], props[k], [...path, k]);
      else if (path.length && dropsUnknown(s))
        out.push({ path: [...path, k], key: k, value: value[k] });
    });
  };
  walk(body, { $ref: `#/definitions/${definition}` }, []);
  return out;
}

/**
 * The fields a request sends that dev refuses (top level, 400) on a strict
 * route; [] for any other route.
 */
export function strictViolations(method, url, body) {
  const route = strictRouteOf(method, url);
  if (!route?.definition) return [];
  return unknownFields(body, route.definition);
}
