import { Buffer } from "node:buffer";
import http from "node:http";

/**
 * A stand-in for the gateway and EE over real sockets, for the shared
 * client's integration tests. It speaks the parts of their contracts the
 * client depends on:
 *
 * - every request must carry a fresh one-use key (a reused one is 401
 *   assertion_replayed, as EE's replay store answers);
 * - `GET /omega/api/workspace/feed`: events 1..total, one every `everyMs`,
 *   resumed after `Last-Event-ID`, ended by `event: end`. Every `cutMs` the
 *   response is ended (EE before L06 ends it cleanly at the key's expiry) or
 *   its socket destroyed (EE after L06 destroys a response it ends);
 * - `?transport=stream` opens with a comment at once and `?transport=poll&wait=`
 *   answers one long-poll window (EE's CH-12 contract): it ends as soon as it
 *   carried frames, or empty at the deadline;
 * - `/omega/api/refuse/<status>/<code>` answers that refusal as EE does.
 */
export async function startSseServer({
  total = 30,
  everyMs = 20,
  cutMs = 150,
  cutStyle = "alternate",
} = {}) {
  const seen = new Set();
  const log = { keys: [], connections: [], reused: 0 };
  // The feed's clock starts with its first request: event n becomes
  // available n * everyMs after it.
  let startedAt = null;
  const available = () =>
    Math.min(total, Math.floor((Date.now() - startedAt) / everyMs));
  const frame = (n) =>
    n === total
      ? `id: ${n}\nevent: log\ndata: {"n":${n}}\n\nevent: end\ndata: {"reason":"done"}\n\n`
      : `id: ${n}\nevent: log\ndata: {"n":${n}}\n\n`;

  const server = http.createServer((req, res) => {
    const url = new URL(req.url, "http://x");
    const key = (req.headers.authorization || "").replace(/^Bearer /, "");
    log.keys.push(key);
    if (!key.startsWith("omega_delegate_") || seen.has(key)) {
      log.reused += 1;
      res.writeHead(401, { "content-type": "application/json" });
      res.end(
        JSON.stringify({
          error: "assertion_replayed",
          message: "That Future AGI sign-in was already used.",
          retryable: true,
        }),
      );
      return;
    }
    seen.add(key);
    const refusal = /^\/omega\/api\/refuse\/(\d{3})\/([a-z_]+)$/.exec(
      url.pathname,
    );
    if (refusal) {
      res.writeHead(Number(refusal[1]), {
        "content-type": "application/json",
        ...(refusal[1] === "503" ? { "retry-after": "1" } : {}),
      });
      res.end(
        JSON.stringify({
          error: refusal[2],
          message: "Valid Omega API key required.",
        }),
      );
      return;
    }
    if (url.pathname !== "/omega/api/workspace/feed") {
      res.writeHead(404, { "content-type": "application/json" });
      res.end(JSON.stringify({ error: "not_found" }));
      return;
    }
    if (startedAt === null) startedAt = Date.now();
    const after = Number(req.headers["last-event-id"] || 0);
    const mode = url.searchParams.get("transport");
    const wait = Number(url.searchParams.get("wait") || 25000);
    const connection = {
      after,
      mode: mode || "legacy",
      at: Date.now() - startedAt,
    };
    log.connections.push(connection);
    res.writeHead(200, {
      "content-type": "text/event-stream; charset=utf-8",
      "cache-control": "no-cache, no-transform",
    });
    if (mode === "stream") res.write(": open\n\n");
    let sent = after;
    let closed = false;
    const timers = [];
    const stop = () => {
      closed = true;
      timers.forEach(clearInterval);
      timers.forEach(clearTimeout);
    };
    res.on("close", stop);
    const pump = () => {
      if (closed) return;
      const ready = available();
      let wrote = false;
      while (sent < ready) {
        sent += 1;
        res.write(frame(sent));
        wrote = true;
      }
      if (sent >= total) {
        stop();
        res.end();
        return;
      }
      if (mode === "poll" && wrote) {
        stop();
        res.end();
      }
    };
    pump();
    timers.push(setInterval(pump, Math.max(5, Math.floor(everyMs / 2))));
    if (mode === "poll") {
      timers.push(
        setTimeout(() => {
          if (closed) return;
          stop();
          res.end();
        }, wait),
      );
    } else {
      const index = log.connections.length;
      timers.push(
        setTimeout(() => {
          if (closed) return;
          stop();
          const destroy =
            cutStyle === "destroy" ||
            (cutStyle === "alternate" && index % 2 === 0);
          connection.cut = destroy ? "destroyed" : "ended";
          if (destroy) res.destroy();
          else res.end();
        }, cutMs),
      );
    }
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address();
  return {
    base: `http://127.0.0.1:${port}/omega/api`,
    origin: `http://127.0.0.1:${port}`,
    log,
    close: () =>
      new Promise((resolve) => {
        server.closeAllConnections?.();
        server.close(resolve);
      }),
  };
}

/**
 * A proxy that holds each response until it completes, then sends it whole:
 * a TLS-inspecting corporate proxy with response buffering on (CH-12).
 */
export async function startBufferingProxy(upstreamOrigin) {
  const server = http.createServer((req, res) => {
    const upstream = http.request(
      `${upstreamOrigin}${req.url}`,
      { method: req.method, headers: req.headers },
      (answer) => {
        const chunks = [];
        answer.on("data", (chunk) => chunks.push(chunk));
        const flush = () => {
          if (res.headersSent) return;
          const body = Buffer.concat(chunks);
          const headers = { ...answer.headers };
          delete headers["transfer-encoding"];
          headers["content-length"] = String(body.length);
          res.writeHead(answer.statusCode, headers);
          res.end(body);
        };
        answer.on("end", flush);
        answer.on("aborted", flush);
        answer.on("error", flush);
      },
    );
    upstream.on("error", () => {
      if (!res.headersSent) {
        res.writeHead(502);
        res.end();
      }
    });
    req.pipe(upstream);
    res.on("close", () => upstream.destroy());
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address();
  return {
    origin: `http://127.0.0.1:${port}`,
    close: () =>
      new Promise((resolve) => {
        server.closeAllConnections?.();
        server.close(resolve);
      }),
  };
}

/** A key minter whose every key is new (the session exchange's one-use keys). */
export function freshKeys() {
  let n = 0;
  return async () => ({
    token: `omega_delegate_h.p.${(n += 1)}`,
    expiresAt: Date.now() + 45_000,
  });
}
