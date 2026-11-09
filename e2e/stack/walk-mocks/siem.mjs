import { pathToFileURL } from "node:url";
import { timingSafeEqual } from "node:crypto";
import { json, read, listen } from "./http.mjs";
export const SIEM_TOKEN = "e2e-siem-fixture-not-a-secret";
const channelName = /^[A-Za-z0-9_.-]{1,80}$/;
const same = (a, b) =>
  Buffer.byteLength(a) === Buffer.byteLength(b) &&
  timingSafeEqual(Buffer.from(a), Buffer.from(b));
// HEC permits consecutive JSON objects, not just a JSON array or NDJSON.
export function hecEvents(raw) {
  const values = [];
  let start = 0,
    depth = 0,
    quoted = false,
    escaped = false;
  for (let i = 0; i < raw.length; i++) {
    const c = raw[i];
    if (quoted) {
      if (escaped) escaped = false;
      else if (c === "\\") escaped = true;
      else if (c === '"') quoted = false;
      continue;
    }
    if (c === '"') quoted = true;
    if (c === "{" || c === "[") {
      if (!depth) start = i;
      depth++;
    } else if (c === "}" || c === "]") {
      if (--depth < 0) throw new Error("invalid JSON");
      if (!depth) values.push(JSON.parse(raw.slice(start, i + 1)));
    } else if (!depth && !/\s/.test(c)) throw new Error("invalid JSON");
  }
  if (
    depth ||
    quoted ||
    !values.length ||
    values.some((v) => !v || Array.isArray(v) || !Object.hasOwn(v, "event"))
  )
    throw new Error("expected HEC event objects");
  return values;
}
export function createSIEM() {
  const channels = new Map();
  const state = (name) => {
    if (!channels.has(name))
      channels.set(name, {
        events: [],
        attempts: [],
        remaining: 0,
        status: 503,
      });
    return channels.get(name);
  };
  const data = async (req, res) => {
    const u = new URL(req.url, "https://siem.e2e-public.test:8443");
    const hec = ["/services/collector", "/services/collector/event"].includes(
      u.pathname,
    );
    const generic = /^\/json\/([A-Za-z0-9_.-]{1,80})$/.exec(u.pathname);
    if (req.method !== "POST" || (!hec && !generic))
      return json(res, 404, { error: "not_found" });
    const channel = generic?.[1] ?? u.searchParams.get("channel") ?? "default";
    if (!channelName.test(channel))
      return json(res, 400, { error: "invalid_channel" });
    const s = state(channel);
    const authorized = same(
      req.headers.authorization ?? "",
      `${hec ? "Splunk" : "Bearer"} ${SIEM_TOKEN}`,
    );
    if (s.attempts.length >= 10000 || s.events.length >= 10000)
      return json(res, 507, { error: "fixture_capacity" });
    s.attempts.push({
      at: new Date().toISOString(),
      path: req.url,
      authorized,
    });
    if (!authorized)
      return json(
        res,
        403,
        hec ? { text: "Invalid token", code: 4 } : { error: "invalid_token" },
      );
    if (s.remaining > 0) {
      s.remaining--;
      return json(res, s.status, { error: "injected_failure" });
    }
    const raw = await read(req);
    let events;
    try {
      const body = hec ? hecEvents(raw) : JSON.parse(raw);
      events = Array.isArray(body) ? body : [body];
      if (
        !events.length ||
        events.some((e) => !e || typeof e !== "object" || Array.isArray(e))
      )
        throw new Error("object required");
    } catch {
      return json(res, 400, { error: "invalid_event" });
    }
    if (s.events.length + events.length > 10000)
      return json(res, 507, { error: "fixture_capacity" });
    s.events.push(...events);
    return json(
      res,
      200,
      hec ? { text: "Success", code: 0 } : { accepted: events.length },
    );
  };
  const control = async (req, res) => {
    if (req.method === "GET" && req.url === "/__health")
      return json(res, 200, { ok: true });
    const match = /^\/__(received|failure)\/([A-Za-z0-9_.-]{1,80})$/.exec(
      req.url,
    );
    if (!match) return json(res, 404, { error: "not_found" });
    const s = state(match[2]);
    if (match[1] === "received" && req.method === "GET")
      return json(res, 200, { events: s.events, attempts: s.attempts });
    if (match[1] === "failure" && req.method === "POST") {
      const body = JSON.parse(await read(req));
      if (
        !Number.isInteger(body.count) ||
        body.count < 0 ||
        body.count > 1000 ||
        ![429, 500, 502, 503].includes(body.status)
      )
        return json(res, 400, { error: "invalid_failure" });
      s.remaining = body.count;
      s.status = body.status;
      return json(res, 200, { ok: true });
    }
    return json(res, 405, { error: "method_not_allowed" });
  };
  return { data, control };
}
if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(process.argv[1]).href
) {
  const sink = createSIEM();
  listen(sink.data, sink.control);
}
