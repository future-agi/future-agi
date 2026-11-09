import { createHmac } from "node:crypto";
import { readFileSync } from "node:fs";
import http from "node:http";

const channel = {
  id: "CE2EALERTS",
  name: "e2e-alerts",
  is_private: false,
  is_member: true,
};
const messages = [];
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

function reply(res, status, body, headers = {}) {
  res.writeHead(status, { "content-type": "application/json", ...headers });
  res.end(JSON.stringify(body));
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, "http://localhost:8080");
  if (url.pathname === "/health") return reply(res, 200, { ok: true });
  const routed = probeRoute(req.method, url.pathname);
  if (routed) return reply(res, ...routed);
  if (url.pathname === "/oauth/v2/authorize") {
    const redirect = new URL(url.searchParams.get("redirect_uri"));
    redirect.searchParams.set("state", url.searchParams.get("state") || "");
    if (url.searchParams.get("deny") === "1")
      redirect.searchParams.set("error", "access_denied");
    else redirect.searchParams.set("code", "e2e-authorized");
    res.writeHead(302, { location: redirect.toString() });
    return res.end();
  }
  if (url.pathname === "/api/oauth.v2.access") {
    return reply(res, 200, {
      ok: true,
      access_token: "xoxb-e2e-bot",
      token_type: "bot",
      scope: "channels:read,channels:join,groups:read,chat:write",
      team: { id: "TE2E", name: "E2E Slack Workspace" },
      bot_user_id: "UE2EBOT",
    });
  }
  if (url.pathname === "/api/auth.test") {
    return reply(res, 200, {
      ok: true,
      team_id: "TE2E",
      team: "E2E Slack Workspace",
      user_id: "UE2EBOT",
    });
  }
  if (url.pathname === "/api/conversations.list") {
    return reply(res, 200, {
      ok: true,
      channels: [channel],
      response_metadata: { next_cursor: "" },
    });
  }
  if (
    url.pathname === "/api/conversations.info" ||
    url.pathname === "/api/conversations.join"
  ) {
    return reply(res, 200, { ok: true, channel });
  }
  if (url.pathname === "/api/chat.postMessage" && req.method === "POST") {
    let data = "";
    for await (const chunk of req) data += chunk;
    const body = JSON.parse(data);
    const ts = `${Date.now() / 1000}`;
    messages.push({ ...body, ts });
    return reply(res, 200, { ok: true, channel: body.channel, ts });
  }
  if (url.pathname === "/messages" && req.method === "GET") {
    return reply(res, 200, { messages });
  }
  return reply(res, 404, { ok: false, error: "not_found" });
});

server.listen(8080, "0.0.0.0");
