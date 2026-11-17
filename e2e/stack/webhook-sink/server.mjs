// Isolated, attested E2E sink only. /r/<channel> records requests; /as/<vendor>
// checks method, credential presence and payload shape. /__received reads them;
// /__stopped and /__revoked control this in-memory fixture, never the product.
// Forwarder probe routes below bind each answer to its container-owned key.
import { createHmac } from "node:crypto";
import { readFileSync } from "node:fs";
import { createServer } from "node:http";
import { pathToFileURL } from "node:url";

const PORT = process.env.PORT || 8080;
// A malformed directive (/fail/x, /as/x) is a 404, never an ordinary path.
const RECORD =
  /^\/r\/([A-Za-z0-9_.-]{1,80})(?:\/as\/(datadog|linear|mixpanel|posthog))?(?:\/fail\/(\d{1,3}))?(\/(?!fail(?:\/|$)|as(?:\/|$)).*)?$/;

const JSON_TYPE = "application/json";
const events = (body) => (Array.isArray(body) ? body : []);
const filled = (v) => typeof v === "string" && v.trim().length > 0;

/** The prefix of a credential every vendor stand-in refuses (e2e/lib/export-sinks.ts REFUSED_PREFIX). */
// synthetic test value — synthetic refused-credential prefix
export const REFUSED_PREFIX = "e2e-refused-";
// Shape/method contract of the isolated export stand-ins. This is not vendor
// authentication: dummy credentials still use the sink's refusal/revocation API.
const object = (v) => v !== null && typeof v === "object" && !Array.isArray(v);
const jsonError = (status, message) => [
  status,
  "application/json",
  { error: message },
];
const event = (v) => object(v) && filled(v.event) && object(v.properties);

function exportContract(vendor, path, method, body, headers) {
  const expected =
    vendor === "datadog" && path === "/api/v1/validate" ? "GET" : "POST";
  // synthetic test value — synthetic method-refusal response
  if (method !== expected) return jsonError(405, "Method not allowed");
  if (vendor === "datadog") {
    if (!filled(headers["dd-api-key"]))
      // synthetic test value — synthetic missing-credential response
      return [403, "application/json", { errors: ["Forbidden"] }];
    if (path === "/api/v1/validate") return null;
    if (
      path === "/api/v2/logs" &&
      (!Array.isArray(body) ||
        !body.every((v) => object(v) && typeof v.message === "string"))
    )
      // synthetic test value — synthetic malformed-log response
      return jsonError(400, "Expected a log array");
    if (
      path === "/api/v2/series" &&
      (!object(body) ||
        !Array.isArray(body.series) ||
        !body.series.every(
          (v) =>
            object(v) &&
            filled(v.metric) &&
            Array.isArray(v.points) &&
            v.points.every(
              (p) =>
                object(p) &&
                Number.isFinite(p.timestamp) &&
                Number.isFinite(p.value),
            ),
        ))
    )
      // synthetic test value — synthetic malformed-metric response
      return jsonError(400, "Expected metric series with numeric points");
  }
  if (vendor === "posthog") {
    // synthetic test value — synthetic malformed-object response
    if (!object(body)) return jsonError(400, "Expected a JSON object");
    if (!filled(body.api_key))
      // synthetic test value — synthetic missing PostHog credential response
      return [
        401,
        "application/json",
        {
          type: "authentication_error",
          code: "invalid_api_key",
          detail: "Project API key invalid.",
        },
      ];
    if (
      path === "/batch/" &&
      (!Array.isArray(body.batch) || !body.batch.every(event))
    )
      // synthetic test value — synthetic malformed-batch response
      return jsonError(400, "Expected an event batch");
  }
  if (vendor === "mixpanel" && !Array.isArray(body))
    // synthetic test value — synthetic malformed-array response
    return jsonError(400, "Expected an event array");
  return null;
}

const revoked = new Set();
const refused = (credential) =>
  filled(credential) &&
  (credential.startsWith(REFUSED_PREFIX) || revoked.has(credential));

// Mixpanel /track (https://docs.mixpanel.com/reference/track-event): the text
// 1 when one or more events are valid, 0 when none is; an event is valid with
// its project token, unless that token is refused.
function mixpanelTrack(body) {
  const valid = events(body).some((e) => {
    const token = e?.properties?.token;
    return filled(e?.event) && filled(token) && !refused(token);
  });
  // synthetic test value — synthetic Mixpanel acceptance/refusal response
  return [200, "text/plain", valid ? "1" : "0"];
}

// Mixpanel /import (https://docs.mixpanel.com/reference/import-events), strict:
// Basic auth with the project secret as the username; every event needs an
// event name, properties.time, properties.distinct_id and a $insert_id of at
// most 36 alphanumeric or "-" characters. Any invalid event answers 400 with
// the failed records, otherwise 200 with the count imported.
const INSERT_ID = /^[A-Za-z0-9-]{1,36}$/;
function mixpanelImport(body, headers) {
  const [scheme, token] = String(headers?.authorization ?? "").split(" ");
  const user =
    scheme === "Basic" && token
      ? Buffer.from(token, "base64").toString("utf8").split(":")[0]
      : "";
  if (!filled(user) || refused(user)) {
    // synthetic test value — synthetic Mixpanel credential-refusal response
    return [
      401,
      JSON_TYPE,
      { code: 401, error: "Invalid credentials", status: "Unauthorized" },
    ];
  }
  const failed = [];
  events(body).forEach((e, index) => {
    const p = e?.properties ?? {};
    const insertId = p.$insert_id;
    const fail = (field, message) =>
      failed.push({ index, $insert_id: insertId ?? null, field, message });
    // synthetic test value — synthetic validation diagnostic
    if (!filled(e?.event)) fail("event", "'event' must be a non-empty string");
    else if (typeof p.time !== "number")
      // synthetic test value — synthetic time-validation diagnostic
      fail(
        "properties.time",
        "'properties.time' is invalid: must be specified as seconds since epoch",
      );
    else if (!filled(p.distinct_id))
      // synthetic test value — synthetic identity-validation diagnostic
      fail(
        "properties.distinct_id",
        "'properties.distinct_id' must be specified",
      );
    else if (!INSERT_ID.test(String(insertId ?? "")))
      // synthetic test value — synthetic insert-id diagnostic
      fail("properties.$insert_id", "'properties.$insert_id' is invalid");
  });
  if (failed.length) {
    // synthetic test value — synthetic failed-import response
    return [
      400,
      JSON_TYPE,
      {
        code: 400,
        error: "some data points in the request failed validation",
        failed_records: failed,
        num_records_imported: events(body).length - failed.length,
        status: "Bad Request",
      },
    ];
  }
  // synthetic test value — synthetic successful-import response
  return [
    200,
    JSON_TYPE,
    { code: 200, num_records_imported: events(body).length, status: "OK" },
  ];
}

// Linear's GraphQL API (https://developers.linear.app/docs/graphql/working-with-the-graphql-api):
// one POST /graphql with { query, variables } and the personal API key as the
// Authorization header, answering { data } or { errors }. The stand-in has one
// team, answers the queries integrations/services/linear_service.py sends
// (the teams and viewer of the connect check, the team list, issueCreate,
// issue and commentCreate) and refuses a request without a key. Each key
// numbers its own issues E2E-1, E2E-2, ...; an issue's URL is on the sink
// (/r/linear-issues/<identifier>), so following it reaches no vendor.
// synthetic test value — synthetic team fixture
const LINEAR_TEAM = { id: "e2e-team-0001", name: "E2E Team", key: "E2E" };
const LINEAR_VIEWER = {
  // synthetic test value — synthetic viewer identity
  id: "e2e-viewer",
  // synthetic test value — synthetic viewer name
  name: "E2E Viewer",
  // synthetic test value — synthetic reserved-domain address
  email: "e2e-viewer@e2e.test",
};
const linearIssueCount = new Map();
// synthetic test value — synthetic GraphQL error response
const linearError = (code, message) => [
  400,
  JSON_TYPE,
  { errors: [{ message, extensions: { code } }] },
];
function linearGraphql(body, headers) {
  const key = String(headers?.authorization ?? "");
  if (!key) {
    // synthetic test value — synthetic authentication-refusal response
    return linearError(
      "AUTHENTICATION_ERROR",
      "Authentication required, not authenticated",
    );
  }
  const query = String(body?.query ?? "");
  const vars = body?.variables ?? {};
  // synthetic test value — synthetic sink issue URL
  const base = `http://${headers?.host ?? "webhook-sink"}/r/linear-issues`;
  if (query.includes("issueCreate")) {
    if (vars.teamId !== LINEAR_TEAM.id || !filled(vars.title)) {
      // synthetic test value — synthetic failed issue-creation response
      return [
        200,
        JSON_TYPE,
        { data: { issueCreate: { success: false, issue: null } } },
      ];
    }
    // synthetic test value — synthetic per-key issue counter
    const n = (linearIssueCount.get(key) ?? 0) + 1;
    linearIssueCount.set(key, n);
    // synthetic test value — synthetic issue identifier
    const identifier = `${LINEAR_TEAM.key}-${n}`;
    return [
      200,
      JSON_TYPE,
      {
        data: {
          issueCreate: {
            // synthetic test value — synthetic success response
            success: true,
            issue: {
              // synthetic test value — synthetic issue id
              id: `e2e-issue-${identifier}`,
              identifier,
              // synthetic test value — synthetic issue URL
              url: `${base}/${identifier}`,
              title: vars.title,
            },
          },
        },
      },
    ];
  }
  if (query.includes("commentCreate")) {
    // synthetic test value — synthetic issue-id normalization
    const identifier = String(vars.issueId ?? "").replace(/^e2e-issue-/, "");
    return [
      200,
      JSON_TYPE,
      {
        data: {
          commentCreate: {
            // synthetic test value — synthetic comment success response
            success: true,
            comment: {
              // synthetic test value — synthetic comment id
              id: `e2e-comment-${vars.issueId}`,
              // synthetic test value — synthetic comment URL
              url: `${base}/${identifier}#comment`,
            },
          },
        },
      },
    ];
  }
  if (/\bissue\s*\(/.test(query)) {
    return [
      200,
      JSON_TYPE,
      // synthetic test value — synthetic issue lookup response
      { data: { issue: { id: `e2e-issue-${vars.id}` } } },
    ];
  }
  if (query.includes("teams")) {
    // synthetic test value — synthetic team response
    const data = { teams: { nodes: [LINEAR_TEAM] } };
    if (query.includes("viewer")) data.viewer = LINEAR_VIEWER;
    return [200, JSON_TYPE, { data }];
  }
  // synthetic test value — synthetic unknown-query response
  return linearError("GRAPHQL_VALIDATION_FAILED", "Unknown query");
}

// The vendors' documented answers, by the path the export service calls:
// Datadog (api/v1/validate, api/v2/logs, api/v2/series), Linear (/graphql,
// above), Mixpanel (/track, /import, above) and PostHog (/decide/, /batch/).
// [status, content type, body].
// Datadog answers 403 { errors: ["Forbidden"] } to a refused API key
// (https://docs.datadoghq.com/api/latest/authentication/), PostHog 401 to a
// refused project API key.
const datadog =
  (answer) =>
  (body, headers = {}) =>
    refused(headers["dd-api-key"])
      // synthetic test value — synthetic Datadog refusal response
      ? [403, JSON_TYPE, { errors: ["Forbidden"] }]
      : answer;
const posthog = (answer) => (body) =>
  refused(body?.api_key)
    // synthetic test value — synthetic PostHog refusal response
    ? [
        401,
        JSON_TYPE,
        {
          type: "authentication_error",
          code: "invalid_api_key",
          detail: "Project API key invalid.",
        },
      ]
    : answer;
const VENDORS = {
  datadog: {
    // synthetic test value — synthetic validation response
    "/api/v1/validate": datadog([200, JSON_TYPE, { valid: true }]),
    // synthetic test value — synthetic log acceptance response
    "/api/v2/logs": datadog([202, JSON_TYPE, {}]),
    // synthetic test value — synthetic metric acceptance response
    "/api/v2/series": datadog([202, JSON_TYPE, { errors: [] }]),
  },
  linear: {
    "/graphql": linearGraphql,
  },
  mixpanel: {
    "/track": mixpanelTrack,
    "/import": mixpanelImport,
  },
  posthog: {
    // synthetic test value — synthetic PostHog settings response
    "/decide/": posthog([
      200,
      JSON_TYPE,
      { featureFlags: {}, sessionRecording: false },
    ]),
    // synthetic test value — synthetic PostHog batch response
    "/batch/": posthog([200, JSON_TYPE, { status: 1 }]),
  },
};

/** [status, content type, body] for a request to `rest` under /as/<vendor>. */
export function vendorAnswer(
  vendor,
  rest,
  body,
  headers = {},
  method = "POST",
) {
  const route = VENDORS[vendor]?.[rest || "/"];
  if (!route)
    // synthetic test value — synthetic missing-route response
    return [
      404,
      JSON_TYPE,
      { ok: false, error: `${vendor} serves no ${rest}` },
    ];
  return (
    exportContract(vendor, rest, method, body, headers) ?? route(body, headers)
  );
}
const LISTED = /^\/__received\/([A-Za-z0-9_.-]{1,80})$/;
const STOPPED = /^\/__stopped\/([A-Za-z0-9_.-]{1,80})$/;
const REVOKED = /^\/__revoked\/([A-Za-z0-9_.-]{1,80})$/;
const MAX_BODY = 1024 * 1024;
const received = new Map();
const stopped = new Set();

// Loopback-forwarder probes (e2e/lib/forward-probe.mjs), the same route in mock-llm,
// webhook-sink and mock-slack (keep the three identical): GET /__probe/<key id>/<nonce>
// records the nonce and answers { answer: HMAC-SHA256(key, nonce) } (hex), the key being
// the one the harness planted inside this container for that inspection (docker exec
// over stdin, at /tmp/fwdprobe-keys/<key id>; it never crosses the network), so only
// this container can answer a probe sent through a runner's host forwarder; no
// well-formed key, no answer. GET /__probed/<nonce> answers how often it arrived (read
// from inside the container). Apart from every flow route; bounded.
const PROBE_KEYS = "/tmp/fwdprobe-keys/";
const PROBE = /^\/__probe\/(fwdprobe-[0-9a-f]{32})\/([A-Za-z0-9_.-]{1,80})$/;
const PROBED = /^\/__probed\/([A-Za-z0-9_.-]{1,80})$/;
const probeArrivals = new Map();

/** [status, body] for a forwarder probe route, or null for every other request. */
function probeRoute(method, path) {
  const probe = method === "GET" && PROBE.exec(path);
  if (probe) {
    const [, keyId, nonce] = probe;
    if (!probeArrivals.has(nonce) && probeArrivals.size >= 4096)
      probeArrivals.delete(probeArrivals.keys().next().value);
    probeArrivals.set(nonce, (probeArrivals.get(nonce) ?? 0) + 1);
    let key = "";
    try {
      key = readFileSync(PROBE_KEYS + keyId, "utf8");
    } catch {
      key = "";
    }
    if (!/^[0-9a-f]{64}$/.test(key))
      return [404, { ok: false, error: "no probe key" }];
    const hmac = createHmac("sha256", Buffer.from(key, "hex"));
    return [200, { answer: hmac.update(nonce).digest("hex") }];
  }
  const probed = method === "GET" && PROBED.exec(path);
  return probed ? [200, { count: probeArrivals.get(probed[1]) ?? 0 }] : null;
}

const json = (res, code, body) => {
  res.writeHead(code, { "Content-Type": "application/json" });
  res.end(JSON.stringify(body));
};

const parse = (raw) => {
  try {
    return raw ? JSON.parse(raw) : null;
  } catch {
    return raw;
  }
};

// Import-safe for socket-free unit tests (scripts/webhook-sink.test.mjs).
export function handleRequest(req, res) {
  const path = req.url.split("?")[0];
  let raw = "";
  let bytes = 0;
  let tooLarge = false;
  req.on("data", (c) => {
    if (tooLarge) return;
    bytes += Buffer.byteLength(c);
    if (bytes > MAX_BODY) {
      tooLarge = true;
      raw = "";
      return json(res, 413, { ok: false, error: "webhook body exceeds 1 MiB" });
    }
    raw += c;
  });
  req.on("error", (err) => {
    console.error(`request aborted: ${err.message}`);
    res.destroy();
  });
  req.on("end", () => {
    if (tooLarge) return;
    if (path === "/health") return json(res, 200, { ok: true });
    const routed = probeRoute(req.method, path);
    if (routed) return json(res, ...routed);
    const listed = LISTED.exec(path);
    if (listed && req.method === "GET") {
      return json(res, 200, {
        channel: listed[1],
        requests: received.get(listed[1]) ?? [],
      });
    }
    const stop = STOPPED.exec(path);
    if (stop && (req.method === "PUT" || req.method === "DELETE")) {
      if (req.method === "PUT") stopped.add(stop[1]);
      else stopped.delete(stop[1]);
      return json(res, 200, {
        channel: stop[1],
        stopped: stopped.has(stop[1]),
      });
    }
    const revoke = REVOKED.exec(path);
    if (revoke && (req.method === "PUT" || req.method === "DELETE")) {
      if (req.method === "PUT") revoked.add(revoke[1]);
      else revoked.delete(revoke[1]);
      return json(res, 200, {
        credential: revoke[1],
        revoked: revoked.has(revoke[1]),
      });
    }
    const match = RECORD.exec(path);
    if (!match)
      return json(res, 404, { ok: false, error: `no route ${req.url}` });
    const [, channel, vendor, failFirst, rest] = match;
    if (!received.has(channel)) received.set(channel, []);
    const requests = received.get(channel);
    const body = parse(raw);
    if (stopped.has(channel)) {
      requests.push({
        at: new Date().toISOString(),
        method: req.method,
        path: req.url,
        headers: req.headers,
        body,
        status: 0,
        accepted: false,
      });
      return res.destroy();
    }
    const failing =
      failFirst !== undefined && requests.length < Number(failFirst);
    const [status, type, answer] = failing
      // synthetic test value — synthetic fail-first response
      ? [500, JSON_TYPE, { ok: false, error: "webhook-sink fail-first" }]
      : vendor
        ? vendorAnswer(vendor, rest, body, req.headers, req.method)
        // synthetic test value — synthetic generic acceptance response
        : [200, JSON_TYPE, { ok: true }];
    requests.push({
      at: new Date().toISOString(),
      method: req.method,
      path: req.url,
      headers: req.headers,
      body,
      status,
      // /track returns HTTP 200 even when it accepts no events. Record the
      // observed answer, not merely transport success, for exact-set proofs.
      accepted:
        status >= 200 &&
        status < 300 &&
        !(
          vendor === "mixpanel" &&
          rest?.split("?")[0].replace(/\/$/, "") === "/track" &&
          answer !== "1"
        ),
    });
    res.writeHead(status, { "Content-Type": type });
    res.end(type === JSON_TYPE ? JSON.stringify(answer) : answer);
  });
}

if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(process.argv[1]).href
) {
  createServer(handleRequest).listen(PORT, () =>
    console.log(`webhook-sink on :${PORT}`),
  );
}
