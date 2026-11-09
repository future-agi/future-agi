// Wall W2: what the Future AGI gateway admits, generated from the route manifest
// (CP:P3, P4, D20, D21; READINESS L04).
//
// `manifest/console-route-manifest.v1.json` is the only list. It is byte-identical
// with Omega EE's copy (packages/server/src/managed-auth/), whose compiler
// (host-route-manifest.ts, lane E-AUTH) this module mirrors rule for rule: the
// same strict schema, the same parameter patterns and the same "a literal
// segment beats a parameter" tie-break. The `%` rule here is stricter than EE's
// `hostPathEncodingAllowed` at E-AUTH 28498cc5 by two refusals (`#`, and an
// encoded unreserved character); EE adopting both is owed to E-AUTH, and W2
// refuses them meanwhile. Nothing else decides
// admission: a method and path pair with no `routes` entry is refused (default
// deny), and no hand-written route regex lives outside this compiler.
//
// The gateway compiles `routes` only. `eeOnly` entries are validated so a typo
// cannot hide there, but they are never admitted: EE serves them to direct host
// clients (the Squad gateway, the interop harness), never to a browser.
//
// CX-WALL (X-1): a route may carry `credential: "channel"` (its channel token is its
// only credential, channel-token/serve.mjs) or `"channel-mint"` (a key route whose
// answer is such a token); absent means a host key. channel-token/manifest-credential.mjs
// holds the rules, the same as W3's.
//
// `hidden` (HIDE-API) names the features the Console does not show; EE's
// embedded Console reads the same list (shownFeatures.ts), so screens and routes
// are one switch. Each route under a hidden feature's paths keeps its classes and
// carries `hiddenBy`; server.mjs answers it 404 `not_found` in EE's own envelope,
// for every role, and never forwards it. Deleting the entry turns it back on.
import fs from "node:fs";
import { fileURLToPath } from "node:url";
import {
  checkChannelBindings,
  routeCredentialOf,
} from "./channel-token/manifest-credential.mjs";

export const CONSOLE_ROUTE_MANIFEST_SCHEMA = "console-route-manifest/v1";
/** Transport metadata applies only after this exact manifest route has been admitted. */
export const SIGNED_TRIGGER_EVENT_PATH = "/workspace/triggers/{id}/events";
// Resolved from this module, not the working directory: the local stack and the
// image both start the gateway from elsewhere.
export const MANIFEST_PATH = fileURLToPath(
  new URL("../manifest/console-route-manifest.v1.json", import.meta.url),
);

const METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"];
export const ROLE_RANK = Object.freeze({ member: 1, admin: 2, owner: 3 });
const PARAM = /^\{([a-zA-Z][a-zA-Z0-9]*)\}$/;
const LITERAL = /^[a-z0-9][a-z0-9-]*$/;
const NAME = /^[a-zA-Z][a-zA-Z0-9]*$/;
const FEATURE = /^[a-z][a-z0-9-]*$/;
const HIDDEN_PATH = /^\/workspace(?:\/[a-z0-9][a-z0-9-]*)+$/;

function object(value, name) {
  if (value === null || typeof value !== "object" || Array.isArray(value))
    throw new Error(`Route manifest ${name} must be an object.`);
  return value;
}

function exact(value, keys, name, optional = []) {
  if (
    !keys.every((key) => Object.hasOwn(value, key)) ||
    !Object.keys(value).every(
      (key) => keys.includes(key) || optional.includes(key),
    )
  ) {
    throw new Error(`Route manifest ${name} has missing or unknown fields.`);
  }
}

/** Strict like the routes: a typo in `hidden` fails the compile instead of hiding nothing or too much. */
function compileHidden(input) {
  const features = [];
  const seen = new Set();
  for (const [id, raw] of Object.entries(object(input, "hidden"))) {
    if (!FEATURE.test(id)) throw new Error(`Invalid hidden feature ${id}.`);
    const entry = object(raw, `hidden feature ${id}`);
    exact(entry, ["until", "paths"], `hidden feature ${id}`);
    if (typeof entry.until !== "string" || entry.until.trim() === "")
      throw new Error(`Hidden feature ${id} needs an until.`);
    if (!Array.isArray(entry.paths) || entry.paths.length === 0)
      throw new Error(`Hidden feature ${id} needs paths.`);
    for (const path of entry.paths) {
      if (typeof path !== "string" || !HIDDEN_PATH.test(path))
        throw new Error(
          `Hidden feature ${id} has an invalid path ${String(path)}.`,
        );
      if (seen.has(path))
        throw new Error(`Hidden path ${path} is listed twice.`);
      seen.add(path);
    }
    features.push(
      Object.freeze({
        id,
        until: entry.until,
        paths: Object.freeze([...entry.paths]),
      }),
    );
  }
  return features;
}

/** The hidden feature whose path is this route template or a prefix of it; two may not claim one route. */
function hiddenFeatureOf(features, path) {
  const owners = features.filter((feature) =>
    feature.paths.some(
      (prefix) => path === prefix || path.startsWith(`${prefix}/`),
    ),
  );
  if (owners.length > 1)
    throw new Error(`Route ${path} is hidden by two features.`);
  return owners[0]?.id;
}

/**
 * Strict: an unknown field, class, parameter or a duplicate route fails the
 * compile, so a typo can never widen the wall.
 *
 * @returns {{ routes: readonly object[], eeOnly: readonly object[], hidden: readonly object[], match(method: string, path: string): object | undefined }}
 */
export function compileConsoleRouteManifest(input) {
  const manifest = object(input, "manifest");
  exact(
    manifest,
    [
      "schemaVersion",
      "about",
      "params",
      "scopeParams",
      "classes",
      "hidden",
      "routes",
      "eeOnly",
    ],
    "manifest",
  );
  if (manifest.schemaVersion !== CONSOLE_ROUTE_MANIFEST_SCHEMA)
    throw new Error("Unsupported route manifest schema.");
  const params = new Map();
  for (const [name, pattern] of Object.entries(
    object(manifest.params, "params"),
  )) {
    if (!NAME.test(name) || typeof pattern !== "string")
      throw new Error(`Invalid route parameter ${name}.`);
    new RegExp(`^(?:${pattern})$`, "u");
    params.set(name, pattern);
  }
  const scopeParams = new Map();
  for (const [name, field] of Object.entries(
    object(manifest.scopeParams, "scopeParams"),
  )) {
    if (!params.has(name) || (field !== "orgId" && field !== "workspaceId"))
      throw new Error(`Invalid scope parameter ${name}.`);
    scopeParams.set(name, field);
  }
  const classes = object(manifest.classes, "classes");
  exact(classes, ["role", "body", "timeout", "rate"], "classes");
  const allowed = (name) => {
    if (!Array.isArray(classes[name]))
      throw new Error(`Route manifest class ${name} must be a list.`);
    return classes[name];
  };
  if (
    !Array.isArray(manifest.routes) ||
    manifest.routes.length === 0 ||
    !Array.isArray(manifest.eeOnly)
  ) {
    throw new Error("Route manifest needs routes and an eeOnly list.");
  }
  const hidden = compileHidden(manifest.hidden);
  const seen = new Set();
  const compiled = [];
  const eeOnly = [];
  const entries = [
    ...manifest.routes.map((raw) => ({ raw, surface: "browser" })),
    ...manifest.eeOnly.map((raw) => ({ raw, surface: "ee-only" })),
  ];
  for (const { raw, surface } of entries) {
    const route = object(raw, "route");
    exact(
      route,
      ["method", "path", "role", "body", "timeout", "rate"],
      "route",
      ["credential", "emergency", "budgets"],
    );
    if (
      route.emergency !== undefined &&
      !["workspace", "organization"].includes(route.emergency)
    )
      throw new Error("Unknown emergency authority class.");
    if (!METHODS.includes(route.method))
      throw new Error(
        `Route manifest method ${String(route.method)} is not supported.`,
      );
    for (const name of ["role", "body", "timeout", "rate"]) {
      if (
        !allowed(name).includes(route[name]) ||
        (name === "role" && !Object.hasOwn(ROLE_RANK, route[name]))
      ) {
        throw new Error(
          `Route ${String(route.path)} has an unknown ${name} class.`,
        );
      }
    }
    const path = route.path;
    // Browser routes are /workspace only; eeOnly never is.
    if (
      typeof path !== "string" ||
      !path.startsWith("/") ||
      (surface === "browser") !== /^\/workspace(?:\/|$)/.test(path)
    ) {
      throw new Error(`Route ${String(path)} is in the wrong manifest list.`);
    }
    const id = `${route.method} ${path}`;
    if (seen.has(id)) throw new Error(`Duplicate manifest route ${id}.`);
    seen.add(id);
    const scoped = [];
    const source = path
      .split("/")
      .slice(1)
      .map((segment) => {
        const param = PARAM.exec(segment)?.[1];
        if (param !== undefined) {
          const pattern = params.get(param);
          if (pattern === undefined)
            throw new Error(
              `Route ${id} uses an undeclared parameter ${param}.`,
            );
          const field = scopeParams.get(param);
          if (field === undefined) return `(?:${pattern})`;
          if (scoped.some(([name]) => name === param))
            throw new Error(
              `Route ${id} repeats the scope parameter ${param}.`,
            );
          scoped.push([param, field]);
          return `(?<${param}>${pattern})`;
        }
        if (!LITERAL.test(segment))
          throw new Error(`Route ${id} has an invalid literal segment.`);
        return segment;
      })
      .join("\\/");
    const hiddenBy =
      surface === "browser" ? hiddenFeatureOf(hidden, path) : undefined;
    const frozen = Object.freeze({
      ...route,
      credential: routeCredentialOf(route.credential, surface, id),
      surface,
      ...(hiddenBy === undefined ? {} : { hiddenBy }),
    });
    if (surface === "ee-only") {
      eeOnly.push(frozen);
      continue;
    }
    const literals = path
      .split("/")
      .filter((segment) => !PARAM.test(segment)).length;
    compiled.push({
      route: frozen,
      pattern: new RegExp(`^\\/${source}$`, "u"),
      literals,
      scoped: Object.freeze(scoped),
    });
  }
  checkChannelBindings(compiled.map((entry) => entry.route));
  for (const feature of hidden) {
    for (const prefix of feature.paths) {
      if (
        !compiled.some(
          (entry) =>
            entry.route.hiddenBy === feature.id &&
            (entry.route.path === prefix ||
              entry.route.path.startsWith(`${prefix}/`)),
        )
      ) {
        throw new Error(`Hidden path ${prefix} covers no browser route.`);
      }
    }
  }
  return Object.freeze({
    routes: Object.freeze(compiled.map((entry) => entry.route)),
    eeOnly: Object.freeze(eeOnly),
    hidden: Object.freeze(hidden),
    /** The browser route for this method and raw path, with the values a scope parameter must equal. */
    match(method, path) {
      const verb = String(method || "").toUpperCase();
      let best;
      for (const entry of compiled) {
        if (entry.route.method !== verb) continue;
        const found = entry.pattern.exec(path);
        if (found === null) continue;
        // A literal route wins over a parameterised one that also matches (/sandboxes/settings).
        if (best !== undefined && entry.literals <= best.entry.literals)
          continue;
        best = { entry, found };
      }
      if (best === undefined) return undefined;
      return {
        route: best.entry.route,
        scoped: best.entry.scoped.map(([name, field]) => [
          field,
          best.found.groups?.[name] ?? "",
        ]),
      };
    },
  });
}

/** Read and compile the manifest file; also returns its bytes for the sha256 pin. */
export function loadConsoleRouteManifest(file = MANIFEST_PATH) {
  const bytes = fs.readFileSync(file);
  return {
    bytes,
    manifest: compileConsoleRouteManifest(JSON.parse(bytes.toString("utf8"))),
  };
}

// Characters encodeURIComponent never encodes (RFC 3986 unreserved). Encoded, they could
// spell a manifest literal that the literal and parameter patterns do not see
// (`/sandboxes/%73ettings` would match `{sandbox}`, then decode to `settings` upstream).
const UNRESERVED = /^[A-Za-z0-9\-._~]$/;

/**
 * The `%` rule and the structural path checks, applied before any route is
 * matched. `%20` and other encoded characters are accepted (a sandbox file named
 * `my file.txt` opens); an encoded slash, backslash, dot, percent sign, NUL or
 * other control character is refused, so an encoding can never introduce a new
 * segment, a dot segment or a double decode upstream. So is an encoded
 * unreserved character (it can only disguise a literal), and so is `#`, which a
 * URL parser on the way to EE would read as the start of a fragment and cut.
 */
export function pathEncodingAllowed(path) {
  if (
    typeof path !== "string" ||
    !path.startsWith("/") ||
    path.includes("\\") ||
    path.includes("//") ||
    path.includes("#") ||
    /[\u0000-\u001f\u007f]/u.test(path)
  )
    return false;
  if (path.split("/").some((segment) => segment === "." || segment === ".."))
    return false;
  for (
    let index = path.indexOf("%");
    index !== -1;
    index = path.indexOf("%", index + 1)
  ) {
    const hex = path.slice(index + 1, index + 3);
    if (!/^[0-9A-Fa-f]{2}$/.test(hex)) return false;
    const byte = Number.parseInt(hex, 16);
    if (
      byte === 0x2f ||
      byte === 0x5c ||
      byte === 0x2e ||
      byte === 0x25 ||
      byte < 0x20 ||
      byte === 0x7f
    )
      return false;
    if (UNRESERVED.test(String.fromCharCode(byte))) return false;
  }
  try {
    decodeURIComponent(path);
  } catch {
    return false;
  }
  return true;
}
