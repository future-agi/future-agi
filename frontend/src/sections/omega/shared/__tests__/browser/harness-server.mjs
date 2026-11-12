// One origin for the browser harness: the page and its bundle, a session exchange,
// a gateway-and-EE stand-in for the shared client's contracts, and a buffering proxy
// under /buffered (CH-12). Loopback only; it holds no secret and reads no file but the
// bundle it is given.
import { Buffer } from "node:buffer";
import http from "node:http";

import { startSseServer } from "../sseServer.fixture.js";

const PAGE = `<!doctype html><meta charset="utf-8"><title>omega client harness</title>
<main><h1>Omega client harness</h1><div id="out"></div></main>
<script type="module" src="/harness.js"></script>`;

// The session exchange's refusals in Django's API envelope (tfc/utils/api_errors.py
// build_error_envelope) with its own codes and words (accounts/views/omega_session.py,
// tfc/utils/error_codes.py at omega/p-f-gate 73c9f640a).
const envelope = (type, code, message) => ({
  status: false,
  type,
  code,
  detail: message,
  message,
  error: message,
  result: message,
});
const EXCHANGE = {
  viewer: [
    403,
    envelope(
      "permission_error",
      "omega_viewer_not_admitted",
      "Viewers cannot open Omega. Ask a workspace admin for member access.",
    ),
  ],
  refused: [
    403,
    envelope(
      "permission_error",
      "permission_denied",
      "No compatible Omega Squad role",
    ),
  ],
  throttled: [
    429,
    envelope(
      "rate_limit_error",
      "throttled",
      "Too many Omega sign-ins in a minute. Wait for the time in Retry-After and try again.",
    ),
    { "retry-after": "30" },
  ],
  down: [
    503,
    envelope(
      "service_unavailable",
      "service_unavailable",
      "Omega delegation is unavailable",
    ),
  ],
  signedOut: [
    401,
    envelope(
      "authentication_error",
      "not_authenticated",
      "Invalid browser access token",
    ),
  ],
};

/**
 * Sandbox exec as EE runs it: `started` {execId}, heartbeats, `stdout`, then `exit`.
 * mode "hold": one response for the whole run (EE after L06 keeps a stream past its
 * key). mode "cut": the response is ended cleanly at `cutSeconds` (EE before L06 at
 * the key's expiry) while the command keeps running; GET .../exec/<id>/events with
 * Last-Event-ID reattaches (SB:W1-EXEC's route).
 */
function createExecs({ scale = 1, cutSeconds = 44 } = {}) {
  const runs = new Map();
  let next = 0;
  const begin = (seconds) => {
    next += 1;
    const run = {
      id: `exec-${next}`,
      frames: [],
      listeners: new Set(),
      done: false,
    };
    const push = (event, data) => {
      const id = run.frames.length + 1;
      const text = `id: ${id}\nevent: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
      run.frames.push(text);
      run.listeners.forEach((listener) => listener(text, id));
      if (event === "exit") {
        run.done = true;
        run.listeners.forEach((listener) => listener(null));
      }
    };
    runs.set(run.id, run);
    push("started", { execId: run.id, sandboxId: "s1" });
    const ms = seconds * 1000 * scale;
    setTimeout(() => push("stdout", { data: "done\n" }), Math.max(0, ms - 50));
    setTimeout(
      () =>
        push("exit", {
          exitCode: 0,
          timedOut: false,
          cancelled: false,
          durationMs: ms,
        }),
      ms,
    );
    return run;
  };
  const serve = (res, run, after, cutMs) => {
    res.writeHead(200, {
      "content-type": "text/event-stream; charset=utf-8",
      "cache-control": "no-cache",
    });
    // EE opens every stream with a byte at once (exec: `started`; logs and the
    // transport contract: an `: open` comment). WebKit and Firefox resolve fetch
    // only with the first body byte, so a reattach opens with the comment too.
    res.flushHeaders();
    if (after > 0) res.write(": open\n\n");
    run.frames.slice(after).forEach((frame) => res.write(frame));
    if (run.done) return res.end();
    const heartbeat = setInterval(
      () => res.write(": keep-alive\n\n"),
      15_000 * scale,
    );
    const listener = (frame) => {
      if (frame === null) {
        clearInterval(heartbeat);
        res.end();
      } else res.write(frame);
    };
    run.listeners.add(listener);
    const cut = cutMs
      ? setTimeout(() => {
          run.listeners.delete(listener);
          clearInterval(heartbeat);
          res.end();
        }, cutMs)
      : null;
    res.on("close", () => {
      run.listeners.delete(listener);
      clearInterval(heartbeat);
      clearTimeout(cut);
    });
  };
  return { begin, serve, runs, cutMs: cutSeconds * 1000 * scale };
}

function forward(target, req, res, { buffered }) {
  const upstream = http.request(
    `${target}${req.url.replace(/^\/buffered/, "")}`,
    { method: req.method, headers: req.headers },
    (answer) => {
      if (!buffered) {
        res.writeHead(answer.statusCode, answer.headers);
        answer.pipe(res);
        // A dropped upstream socket is a dropped socket here too, as at the gateway.
        answer.on("close", () => {
          if (!answer.complete) res.destroy();
        });
        return;
      }
      const chunks = [];
      answer.on("data", (chunk) => chunks.push(chunk));
      const flush = () => {
        if (res.headersSent) return;
        const body = Buffer.concat(chunks);
        const headers = {
          ...answer.headers,
          "content-length": String(body.length),
        };
        delete headers["transfer-encoding"];
        res.writeHead(answer.statusCode, headers);
        res.end(body);
      };
      answer.on("end", flush);
      answer.on("aborted", flush);
    },
  );
  upstream.on("error", () => {
    if (!res.headersSent) res.writeHead(502).end();
  });
  req.pipe(upstream);
  res.on("close", () => upstream.destroy());
}

const readJson = (req) =>
  new Promise((resolve) => {
    let text = "";
    req.on("data", (chunk) => (text += chunk));
    req.on("end", () => {
      try {
        resolve(JSON.parse(text || "{}"));
      } catch {
        resolve({});
      }
    });
  });

export async function startHarness({ bundle, scale = 1 }) {
  const feed = await startSseServer({ total: 60, everyMs: 15, cutMs: 120 });
  const slowFeed = await startSseServer({
    total: 40,
    everyMs: 300,
    cutMs: 60_000,
  });
  const execs = createExecs({ scale });
  const keys = new Set();
  const log = {
    exchange: [],
    execRequests: [],
    reusedKeys: 0,
    feed: feed.log,
    slowFeed: slowFeed.log,
  };
  let exchangeMode = "ok";
  let minted = 0;
  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, "http://x");
    if (url.pathname === "/")
      return res.writeHead(200, { "content-type": "text/html" }).end(PAGE);
    if (url.pathname === "/harness.js")
      return res
        .writeHead(200, { "content-type": "text/javascript" })
        .end(bundle);
    if (url.pathname === "/control/exchange") {
      exchangeMode = (await readJson(req)).mode || "ok";
      return res.writeHead(204).end();
    }
    if (url.pathname === "/accounts/omega/session-exchange/") {
      log.exchange.push(exchangeMode);
      const refusal = EXCHANGE[exchangeMode];
      if (refusal)
        return res
          .writeHead(refusal[0], {
            "content-type": "application/json",
            ...(refusal[2] || {}),
          })
          .end(JSON.stringify(refusal[1]));
      minted += 1;
      return res.writeHead(200, { "content-type": "application/json" }).end(
        JSON.stringify({
          accessToken: `omega_delegate_h.p.${minted}`,
          tokenType: "Bearer",
          expiresIn: 45,
        }),
      );
    }
    if (url.pathname.startsWith("/buffered/omega/api/workspace/feed"))
      return forward(slowFeed.origin, req, res, { buffered: true });
    if (url.pathname === "/omega/api/workspace/feed")
      return forward(feed.origin, req, res, { buffered: false });
    if (!url.pathname.startsWith("/omega/api/"))
      return res.writeHead(404).end();
    const key = (req.headers.authorization || "").replace(/^Bearer /, "");
    if (keys.has(key)) log.reusedKeys += 1;
    keys.add(key);
    const refusal = /^\/omega\/api\/refuse\/(\d{3})\/([a-z_]+)$/.exec(
      url.pathname,
    );
    if (refusal) {
      const status = Number(refusal[1]);
      // ?detail=<json>: EE's workspace-route shape, {schemaVersion, error:
      // {code, message, detail}}, with EE's own sentence as the message.
      const detail = url.searchParams.get("detail");
      const body = detail
        ? {
            schemaVersion: "omega-workspace-sandboxes/v1",
            error: {
              code: refusal[2],
              message: url.searchParams.get("message") || "",
              detail: JSON.parse(detail),
            },
          }
        : {
            error: refusal[2],
            message: "Valid Omega API key required.",
            retryable: status === 503,
          };
      return res
        .writeHead(status, {
          "content-type": "application/json",
          ...(status === 503 ? { "retry-after": "1" } : {}),
        })
        .end(JSON.stringify(body));
    }
    if (
      url.pathname === "/omega/api/workspace/sandboxes/s1/exec" &&
      req.method === "POST"
    ) {
      const body = await readJson(req);
      const run = execs.begin(Number(body.seconds) || 90);
      log.execRequests.push({ kind: "exec", mode: body.mode, execId: run.id });
      return execs.serve(res, run, 0, body.mode === "cut" ? execs.cutMs : 0);
    }
    const attach =
      /^\/omega\/api\/workspace\/sandboxes\/s1\/exec\/([^/]+)\/events$/.exec(
        url.pathname,
      );
    if (attach && req.method === "GET") {
      const run = execs.runs.get(decodeURIComponent(attach[1]));
      log.execRequests.push({
        kind: "attach",
        execId: attach[1],
        after: req.headers["last-event-id"] || null,
      });
      if (!run)
        return res
          .writeHead(404, { "content-type": "application/json" })
          .end(JSON.stringify({ error: "not_found" }));
      return execs.serve(
        res,
        run,
        Number(req.headers["last-event-id"] || 0),
        execs.cutMs,
      );
    }
    return res
      .writeHead(404, { "content-type": "application/json" })
      .end(JSON.stringify({ error: "not_found" }));
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  return {
    origin: `http://127.0.0.1:${server.address().port}`,
    log,
    close: async () => {
      server.closeAllConnections();
      await new Promise((resolve) => server.close(resolve));
      await feed.close();
      await slowFeed.close();
    },
  };
}
