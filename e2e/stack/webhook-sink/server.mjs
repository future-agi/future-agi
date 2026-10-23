// Webhook receiver for the E2E stacks (e2e/lib/sinks.ts WebhookSink). It sits on
// the public-classified `e2e-public` network (e2e/lib/public-network.ts) as
// hooks.e2e-public.test, because the product refuses every private address for a
// webhook, at save and at delivery.
//   ANY /r/<channel>[/fail/<n>][/...]  records { at, method, path, headers, body,
//                                       status } under <channel>; with /fail/<n>
//                                       the channel's first n requests answer 500,
//                                       the rest 200
//   GET /__received/<channel>           lists them, oldest first (host port, reads)
// Nothing is global: channels are minted per test. Kept in memory for the life of
// the container.
import { createServer } from "node:http";
import { pathToFileURL } from "node:url";

const PORT = process.env.PORT || 8080;
// A malformed fail directive (/fail/x) is a 404, never an ordinary path.
const RECORD =
  /^\/r\/([A-Za-z0-9_.-]{1,80})(?:\/fail\/(\d{1,3}))?(?:\/(?!fail(?:\/|$)).*)?$/;
const LISTED = /^\/__received\/([A-Za-z0-9_.-]{1,80})$/;
const MAX_BODY = 1024 * 1024;
const received = new Map();

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
    const listed = LISTED.exec(path);
    if (listed && req.method === "GET") {
      return json(res, 200, {
        channel: listed[1],
        requests: received.get(listed[1]) ?? [],
      });
    }
    const match = RECORD.exec(path);
    if (!match)
      return json(res, 404, { ok: false, error: `no route ${req.url}` });
    const [, channel, failFirst] = match;
    if (!received.has(channel)) received.set(channel, []);
    const requests = received.get(channel);
    const status =
      failFirst !== undefined && requests.length < Number(failFirst)
        ? 500
        : 200;
    requests.push({
      at: new Date().toISOString(),
      method: req.method,
      path: req.url,
      headers: req.headers,
      body: parse(raw),
      status,
    });
    json(
      res,
      status,
      status === 200
        ? { ok: true }
        : { ok: false, error: "webhook-sink fail-first" },
    );
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
