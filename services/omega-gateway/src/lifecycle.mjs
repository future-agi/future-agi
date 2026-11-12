// Readiness and graceful drain (READINESS 1.3, S06, CH-02; CP REL2).
//
// `/readyz` requires EE's HTTP 200 application/json object with status "ready" and ready true
// (at most 64 KiB), and HTTP 401 from the Django session exchange's unauthenticated GET.
// Other statuses, redirects and default pages fail closed. Results are cached briefly so a
// load balancer probing every second does not become load on EE or Django. While draining,
// `/readyz` answers 503 so the balancer stops sending new work.
//
// Drain (SIGTERM): stop accepting new requests (503 `omega_gateway_draining` with
// `Connection: close`), end open event streams at once with a clean end so their clients
// reconnect (with `Last-Event-ID`) to another replica, give other in-flight requests up to
// OMEGA_GATEWAY_DRAIN_MS (10 s), then close whatever is left.
const PROBE_TIMEOUT_MS = 2_000;
const CACHE_MS = 2_000;
const MAX_PROBE_BODY_BYTES = 64 * 1024;

// EE packages/server/src/index.ts GET /readyz: { status: "ready", ready: true }.
async function eeReady(response) {
  if (
    response.status !== 200 ||
    response.headers
      .get("content-type")
      ?.split(";", 1)[0]
      .trim()
      .toLowerCase() !== "application/json" ||
    !response.body
  )
    return false;
  const reader = response.body.getReader();
  const chunks = [];
  let bytes = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      bytes += value.byteLength;
      if (bytes > MAX_PROBE_BODY_BYTES) return false;
      chunks.push(Buffer.from(value));
    }
    const body = JSON.parse(Buffer.concat(chunks, bytes).toString("utf8"));
    return (
      body !== null &&
      typeof body === "object" &&
      !Array.isArray(body) &&
      body.status === "ready" &&
      body.ready === true
    );
  } finally {
    // Cancellation must not wait for an untrusted upstream to finish streaming.
    void reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

async function probe(url, fetchImpl, accept) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), PROBE_TIMEOUT_MS);
  let response;
  try {
    response = await fetchImpl(url, {
      method: "GET",
      redirect: "manual",
      headers: { accept: "application/json" },
      signal: controller.signal,
    });
    return (await accept(response)) ? "ok" : "fail";
  } catch {
    return "fail";
  } finally {
    clearTimeout(timer);
    controller.abort();
    // Status-only refusals and rejected EE responses never need their bodies buffered.
    if (response?.body && !response.body.locked) {
      void response.body.cancel().catch(() => {});
    }
  }
}

function endStream(response, source) {
  source?.unpipe?.(response);
  source?.destroy?.();
  if (!response.writableEnded) response.end();
}

export function createLifecycle(
  config,
  { fetchImpl = globalThis.fetch, now = () => Date.now() } = {},
) {
  let draining = false;
  let cached = null;
  let pendingCheck = null;
  const inFlight = new Set();
  const streams = new Map();
  let idle = null;

  function settleIdle() {
    if (inFlight.size === 0 && idle) {
      idle.resolve();
      idle = null;
    }
  }

  async function readiness() {
    if (draining) return { ready: false, checks: { draining: true } };
    if (cached && now() - cached.at < CACHE_MS) return cached.value;
    if (!pendingCheck) {
      pendingCheck = (async () => {
        const [ee, exchange] = await Promise.all([
          probe(
            `${config.remoteBaseUrl}${config.eeReadyPath}`,
            fetchImpl,
            eeReady,
          ),
          // OmegaSessionAuthentication + IsAuthenticated refuse this credential-free GET
          // with 401 before method dispatch (accounts/views/omega_session.py).
          probe(
            config.exchangeUrl,
            fetchImpl,
            (response) => response.status === 401,
          ),
        ]);
        const value = {
          ready: ee === "ok" && exchange === "ok",
          checks: { ee, exchange },
        };
        cached = { at: now(), value };
        return value;
      })().finally(() => {
        pendingCheck = null;
      });
    }
    return pendingCheck;
  }

  return {
    get draining() {
      return draining;
    },
    readiness,
    /** Count a request until its response closes. */
    track(response) {
      inFlight.add(response);
      response.once("close", () => {
        inFlight.delete(response);
        streams.delete(response);
        settleIdle();
      });
    },
    /** An open event stream: on drain its EE source is cancelled and the browser side ends cleanly. */
    trackStream(response, source) {
      if (draining) {
        endStream(response, source);
        return;
      }
      streams.set(response, source);
    },
    inFlight: () => inFlight.size,
    /**
     * Drain within `drainMs`. Resolves with what happened, for the shutdown log.
     * @param {import("node:http").Server[]} servers
     */
    async drain(servers, drainMs = config.drainMs) {
      if (draining) return { already: true };
      draining = true;
      const started = now();
      for (const server of servers) server.close();
      const streamsEnded = streams.size;
      for (const [response, source] of streams) endStream(response, source);
      streams.clear();
      for (const server of servers) server.closeIdleConnections?.();
      if (inFlight.size > 0) {
        await new Promise((resolve) => {
          idle = { resolve };
          const timer = setTimeout(resolve, drainMs);
          timer.unref?.();
        });
      }
      const abandoned = inFlight.size;
      for (const server of servers) server.closeAllConnections?.();
      return { streamsEnded, abandoned, drainMs: now() - started };
    },
  };
}
